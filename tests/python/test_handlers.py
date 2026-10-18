import asyncio
import json
import os

import pytest

from gm_server import dungeon_scene
from gm_server.router import dispatch
from conftest import FakeCommunicate, FakeLLM


def actions(sent):
    return [m["action"] for m in sent]


async def test_player_message_sends_chat_roll_and_sound(make_ctx, sent, tmp_path):
    llm = FakeLLM(gm_responses=[{"narrative": "祭壇が光る。", "speech": "祭壇が光る", "dice": "/r 1d20+2"}])
    ctx = make_ctx(llm)
    assert await dispatch(ctx, json.dumps({"event": "player_message", "user": "PL", "content": "<p>祭壇を調べる</p>"}))
    assert llm.prompts == [("gm", "祭壇を調べる")]
    assert actions(sent) == ["chat", "roll", "play_sound"]
    assert sent[0]["speaker"] == "AI GM"
    assert sent[1] == {"action": "roll", "speaker": "AI GM (判定)", "formula": "1d20+2"}
    assert sent[2]["subtitle"] == "祭壇が光る"
    assert os.path.exists(tmp_path / sent[2]["src"])


async def test_player_message_without_dice_or_speech(make_ctx, sent):
    ctx = make_ctx(FakeLLM(gm_responses=[{"narrative": "静かだ。", "speech": "", "dice": None}]))
    await dispatch(ctx, json.dumps({"event": "player_message", "content": "待つ"}))
    assert actions(sent) == ["chat"]


async def test_player_message_empty_is_ignored(make_ctx, sent):
    llm = FakeLLM()
    ctx = make_ctx(llm)
    await dispatch(ctx, json.dumps({"event": "player_message", "content": "<p> </p>"}))
    assert sent == [] and llm.prompts == []


async def test_tts_failure_skips_play_sound(make_ctx, sent):
    ctx = make_ctx(FakeLLM(gm_responses=[{"narrative": "n", "speech": "s", "dice": None}]))

    class Broken(FakeCommunicate):
        async def save(self, path):
            raise RuntimeError("403")

    ctx.tts._communicate_factory = Broken
    await dispatch(ctx, json.dumps({"event": "player_message", "content": "x"}))
    assert actions(sent) == ["chat"]


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", json.dumps({"event": "unknown"}), json.dumps({"no": "event"})])
async def test_dispatch_ignores_bad_input(make_ctx, sent, raw):
    assert await dispatch(make_ctx(), raw) is False
    assert sent == []


async def test_dispatch_survives_handler_exception(make_ctx, sent):
    class Exploding(FakeLLM):
        async def query_ai_gm(self, player_input):
            raise RuntimeError("boom")

    assert await dispatch(make_ctx(Exploding()), json.dumps({"event": "player_message", "content": "x"})) is False


async def test_room_entered_plays_narration(make_ctx, sent):
    ctx = make_ctx()
    await dispatch(ctx, json.dumps({"event": "room_entered", "token_name": "PC", "room_name": "部屋 1", "flavor": "冷たい風"}))
    assert actions(sent) == ["play_sound"]
    assert sent[0]["volume"] == 0.95
    assert sent[0]["subtitle"] == "📍 部屋 1: 冷たい風"
    assert FakeCommunicate.calls[0].text == "部屋 1。冷たい風"


async def test_room_entered_without_flavor_is_silent(make_ctx, sent):
    await dispatch(make_ctx(), json.dumps({"event": "room_entered", "room_name": "部屋 1", "flavor": ""}))
    assert sent == []


@pytest.mark.parametrize("prompt, expected", [
    ("", True),
    ("ダンジョンを作って", True),
    ("古い寺院を生成して", True),
    ("寺院について教えて", False),
    ("ダンジョンのNPCを動かして", False),
    ("ゴブリンの会話を考えて", False),
])
def test_is_dungeon_intent(prompt, expected):
    assert dungeon_scene.is_dungeon_intent(prompt) is expected


async def test_ai_command_routes_to_support_gm(make_ctx, sent):
    llm = FakeLLM(support_responses=["了解です。\n次の手を考えます"])
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "敵の作戦を考えて"}))
    assert sent == [{"action": "chat", "speaker": "サポートGM Geminiちゃん", "content": "了解です。<br>次の手を考えます"}]


async def test_ai_command_empty_support_reply_shows_warning(make_ctx, sent):
    await dispatch(make_ctx(FakeLLM()), json.dumps({"event": "ai_command", "prompt": "相談"}))
    assert "応答がありませんでした" in sent[0]["content"]


async def test_dungeon_wizard_card_flow(make_ctx, sent):
    ctx = make_ctx()
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "氷の洞窟ダンジョン"}))
    assert ctx.state.pending_dungeon["user_prompt"] == "氷の洞窟ダンジョン"
    mode_values = [c["value"] for c in sent[-1]["choices"]]
    assert mode_values == ["dungeon_mode:underground", "dungeon_mode:ruins", "dungeon_mode:wilderness"]

    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "dungeon_mode:ruins", "label": "廃墟"}))
    assert ctx.state.pending_dungeon["scene_mode"] == "ruins"
    assert "地上の廃墟" in sent[-1]["message"]
    size_value = sent[-1]["choices"][0]["value"]
    assert size_value == "dungeon_size:small"

    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": size_value, "label": "小"}))
    density_value = sent[-1]["choices"][1]["value"]
    assert density_value == "dungeon_density:small:0.7"

    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": density_value, "label": "密集"}))
    assert sent[-1]["choices"][2]["value"] == "dungeon_loop:small:0.7:0.4"
    assert actions(sent) == ["interactive_card"] * 4


async def test_wilderness_wizard_skips_density(make_ctx, sent):
    ctx = make_ctx()
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "ダンジョン"}))
    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "dungeon_mode:wilderness", "label": "野外"}))
    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "dungeon_size:small", "label": "小"}))
    assert sent[-1]["choices"][0]["value"].startswith("dungeon_loop:small:")
    assert "サイズは「小」" in sent[-1]["message"]


async def test_new_ai_command_resets_scene_mode(make_ctx, sent):
    ctx = make_ctx()
    ctx.state.pending_dungeon["scene_mode"] = "wilderness"
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "ダンジョン"}))
    assert ctx.state.pending_dungeon["scene_mode"] == "underground"


@pytest.mark.parametrize("value, mode", [
    ("dungeon_mode:ruins", "ruins"),
    ("dungeon_mode:wilderness", "wilderness"),
    ("dungeon_mode:space", "underground"),
    ("dungeon_mode", "underground"),
])
def test_parse_dungeon_mode_choice(value, mode):
    c = dungeon_scene.parse_dungeon_choice(value)
    assert (c.kind, c.scene_mode) == ("mode", mode)


@pytest.mark.parametrize("value, expected", [
    ("dungeon_size:medium", ("size", "medium", 0.5, 0.2)),
    ("dungeon_density:large:0.7", ("density", "large", 0.7, 0.2)),
    ("dungeon_loop:small:1.0:0.4", ("loop", "small", 1.0, 0.4)),
    # 旧実装はパーツが足りないと loop_ratio が未定義のまま UnboundLocalError になっていた
    ("dungeon_loop:small", ("loop", "small", 0.5, 0.2)),
    ("dungeon_loop:huge:abc:9", ("loop", "small", 0.5, 1.0)),
])
def test_parse_dungeon_choice(value, expected):
    c = dungeon_scene.parse_dungeon_choice(value)
    assert (c.kind, c.size_key, c.density, c.loop_ratio) == expected


@pytest.mark.parametrize("value", ["", "other:1", "dungeon_unknown:small"])
def test_parse_dungeon_choice_rejects(value):
    assert dungeon_scene.parse_dungeon_choice(value) is None


async def test_dungeon_loop_choice_builds_scene_with_fallback_lore(make_ctx, sent, tmp_path, monkeypatch):
    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    ctx = make_ctx(FakeLLM(json_responses=[None, None]))
    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "dungeon_loop:small:0.4:0.2", "label": "標準"}))
    assert actions(sent) == ["chat", "chat", "create_dungeon_scene", "play_sound"]
    scene = sent[2]
    assert scene["name"].startswith("黒水城塞")
    assert scene["grid_width"] == 20 and scene["room_count"] == len(scene["rooms"])
    assert scene["walls"] and len(scene["start_pos"]) == 2
    assert scene["scene_mode"] == "underground"
    assert all(len(w) == 5 for w in scene["walls"])
    assert os.path.exists(tmp_path / scene["background_src"])
    json.dumps(scene)  # JSON 化できること (タプルや set が混ざっていない)


async def test_dungeon_scene_uses_ai_lore_and_room_flavors(make_ctx, sent, tmp_path, monkeypatch):
    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    lore = {"dungeon_name": "氷窟", "floor_title": "第1層", "factions": []}
    rooms = [{"id": 0, "title": "凍った入口", "flavor": "息が白い"}, "壊れた要素"]
    ctx = make_ctx(FakeLLM(json_responses=[lore, rooms]))
    scene = await dungeon_scene.generate_and_create_dungeon_scene(ctx, "small", 0.2, 0.4, user_prompt="氷", seed=7)
    assert scene["name"] == "氷窟 - 第1層"
    assert scene["rooms"][0]["title"] == "凍った入口"


async def test_loop_choice_uses_pending_scene_mode(make_ctx, sent, tmp_path, monkeypatch):
    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    ctx = make_ctx(FakeLLM(json_responses=[None, None]))
    ctx.state.pending_dungeon["scene_mode"] = "wilderness"
    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "dungeon_loop:small:0.5:0.2", "label": "標準"}))
    scene = sent[2]
    assert scene["scene_mode"] == "wilderness"
    assert scene["walls"] == []
    assert 2 <= scene["room_count"] <= dungeon_scene.WILDERNESS_POINT_COUNT
    assert scene["background_src"].startswith("dungeon_wilderness_")
    assert "野外" in sent[-1]["subtitle"]


async def test_room_flavor_prompt_carries_mode_and_light_presets(make_ctx, sent, monkeypatch):
    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    llm = FakeLLM(json_responses=[None, [{"id": 0, "title": "崩れた門", "light_preset": "moonlight_dim"}]])
    ctx = make_ctx(llm)
    scene = await dungeon_scene.generate_and_create_dungeon_scene(ctx, "small", 0.2, 0.4, seed=3, scene_mode="ruins")
    assert scene["rooms"][0]["light_preset"] == "moonlight_dim"
    rooms_prompt = llm.prompts[1][1]
    assert "廃墟" in rooms_prompt and "bonfire_bright" in rooms_prompt
    assert any(w[4] in ("low_wall", "rubble") for w in scene["walls"])


async def test_map_generation_timeout_without_answer_aborts(make_ctx, sent, monkeypatch):
    import time

    from gm_server import deadline

    monkeypatch.setattr(dungeon_scene, "MAPGEN_TIMEOUT", 0.05)
    monkeypatch.setattr(deadline, "EXTEND_ANSWER_WAIT", 0.05)
    monkeypatch.setattr(dungeon_scene, "_build_geometry", lambda *a: time.sleep(0.5))
    ctx = make_ctx()
    assert await dungeon_scene.generate_and_create_dungeon_scene(ctx, "small", 0.2) is None
    assert actions(sent) == ["interactive_card", "chat", "chat"]
    assert sent[0]["title"] == "タイムアウト確認"
    assert "打ち切った" in sent[-1]["content"]
    assert ctx.state.timeout_requests == {}


def test_normalize_chunk_ids_handles_zero_and_one_based():
    zero = [{"id": 5, "title": "a"}, {"id": 6, "title": "b"}, {"id": 99, "title": "他の部屋"}]
    assert [r["id"] for r in dungeon_scene._normalize_chunk_ids(zero, [5, 6, 7])] == [5, 6]
    one = [{"id": 6, "title": "a"}, {"id": 7, "title": "b"}, {"id": 8, "title": "c"}]
    assert [r["id"] for r in dungeon_scene._normalize_chunk_ids(one, [5, 6, 7])] == [5, 6, 7]
    assert dungeon_scene._normalize_chunk_ids([{"id": "x"}], [0]) == []


async def test_room_flavors_are_split_into_jobs(make_ctx, sent, monkeypatch):
    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    # 1 つ目のジョブ (入口を含む) は失敗、2 つ目は 1 始まりで返す
    llm = FakeLLM(json_responses=[None, None, None, None])
    ctx = make_ctx(llm)
    gen = dungeon_scene.DungeonGenerator(width=100, height=100, seed=3, density=0.5, loop_ratio=0.2)
    gen.generate()
    topos = gen.get_room_topologies()
    selected = dungeon_scene.prioritize_key_topologies(topos, max_count=dungeon_scene.MAX_ROOMS_FOR_AI)
    second_ids = [t["id"] for t in selected[dungeon_scene.ROOMS_PER_JOB:2 * dungeon_scene.ROOMS_PER_JOB]]
    llm.json_responses = [None, [{"id": i + 1, "title": f"T{i}"} for i in second_ids]]
    flavors = await dungeon_scene.query_room_flavors(ctx, {}, topos, "underground")
    expected_jobs = -(-len(selected) // dungeon_scene.ROOMS_PER_JOB)
    assert len([p for p in llm.prompts if p[0] == "json"]) == expected_jobs
    assert [f["id"] for f in flavors] == second_ids
    notes = gen.get_room_flavor_notes(custom_flavors=flavors, id_base=0)
    assert notes[second_ids[0]]["title"] == f"T{second_ids[0]}"
    assert notes[0]["type"] == "start"


async def test_long_steps_send_progress_to_fvtt(make_ctx, sent, monkeypatch):
    from gm_server import deadline

    monkeypatch.setattr(dungeon_scene, "get_active_world_info", lambda: {"title": "W", "description": "D"})
    monkeypatch.setattr(deadline, "NOTIFY_INTERVAL", 0.01)

    class SlowLLM(FakeLLM):
        async def query_json(self, prompt, unbounded=False):
            assert unbounded  # 上限と延長確認は run_with_deadline が管理する
            await asyncio.sleep(0.05)
            return None

    ctx = make_ctx(SlowLLM())
    await dungeon_scene.generate_and_create_dungeon_scene(ctx, "small", 0.2, seed=1)
    progress_msgs = [m["message"] for m in sent if m["action"] == "progress"]
    assert any(m.startswith("[伝承]") for m in progress_msgs)
    assert any(m.startswith("[部屋の情景] 部屋 ") for m in progress_msgs)
    assert "create_dungeon_scene" in actions(sent)


def test_support_gm_persona_matches_the_mascot():
    # マスコットと同じキャラクター (名前・肩書き・世話焼きでちょっとドジ・中身は正確)
    from gm_server.config import SPEAKER_SUPPORT
    from gm_server.llm import SUPPORT_GM_PERSONA
    assert SPEAKER_SUPPORT == "サポートGM Geminiちゃん"
    for word in ("Geminiちゃん", "星詠みのサポートGM", "任せて！", "ごめんね", "敬語は使わない", "正確"):
        assert word in SUPPORT_GM_PERSONA
