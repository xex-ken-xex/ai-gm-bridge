import json

import pytest

from gm_server import builder, tts
from gm_server.router import HANDLERS, dispatch
from conftest import FakeLLM


@pytest.fixture(autouse=True)
def fixed_world(monkeypatch):
    monkeypatch.setattr(builder, "get_active_world_info", lambda: {"title": "W", "description": "D"})


@pytest.fixture(autouse=True)
def progress(monkeypatch):
    """画面の進捗表示 (builder_progress) は sent と分けて集める"""
    calls = []

    def fake_status(ctx, stage, step, steps, step_label, kind="draft"):
        async def _send(elapsed, remaining, detail):
            calls.append((stage, step, steps, step_label, detail))
        return _send

    monkeypatch.setattr(builder, "stage_status", fake_status)
    return calls


def main(sent):
    """画面の進捗表示 (builder_progress) を除いたメッセージ"""
    return [m for m in sent if m["action"] != "builder_progress"]


def actions(sent):
    return [m["action"] for m in main(sent)]


def region_result(**extra):
    return {
        "region_name": "白塩の野",
        "public_lore": {"terrain_and_climate": "塩の平原"},
        "dm_secrets": {"hidden_threat": "墓"},
        "canon": ["資源: 岩塩", "", 3],
        "open_threads": "文字列は捨てる",
        **extra,
    }


def test_builder_events_are_registered():
    assert HANDLERS["builder_generate_stage"] is builder.handle_generate_stage
    assert HANDLERS["builder_build_scene"] is builder.handle_build_scene


def test_normalize_stage_result():
    assert builder.normalize_stage_result("region", None) is None
    assert builder.normalize_stage_result("region", {"region_name": "", "public_lore": {}}) is None
    assert builder.normalize_stage_result("region", {"region_name": "x", "public_lore": "text"}) is None
    assert builder.normalize_stage_result("unknown", region_result()) is None
    r = builder.normalize_stage_result("region", region_result(dm_secrets="x"))
    assert r["canon"] == ["資源: 岩塩", "3"]
    assert r["open_threads"] == [] and r["dm_secrets"] == {} and r["consistency_notes"] == ""


@pytest.mark.parametrize("stage", ["region", "settlement", "wilderness", "site_lore"])
def test_fallbacks_are_valid_results(stage):
    assert builder.normalize_stage_result(stage, builder.fallback_stage_result(stage)) is not None


def test_builder_size_key_limits_large_maps():
    """M12: ビルダーでは小・中・大だけ (既定は中)。巨大は中に丸める"""
    assert builder.builder_size_key("small") == "small"
    assert builder.builder_size_key("large") == "large"
    assert builder.builder_size_key("xlarge") == "medium"
    assert builder.builder_size_key(None) == "medium"


def test_wilderness_map_keeps_100_squares():
    """旅路の地点グラフはサイズの呼び名が変わっても 100x100 のまま (同じ seed で同じ地図)"""
    gen = builder.make_wilderness_generator(1234)
    assert (gen.width, gen.height) == (100, 100)


def test_parse_seed():
    assert builder.parse_seed("12") == 12
    assert builder.parse_seed(0) is None and builder.parse_seed("x") is None


async def test_generate_region_sends_draft_with_cards_and_seed(make_ctx, sent):
    llm = FakeLLM(json_responses=[region_result()])
    ctx = make_ctx(llm)
    payload = {"event": "builder_generate_stage", "stage": "region", "seed": 77, "user_prompt": "塩",
               "world": {"title": "アンティキティラ", "description": "黒竜"}}
    assert await dispatch(ctx, json.dumps(payload))
    assert actions(sent) == ["builder_stage_result"]
    msg = sent[0]
    assert msg["stage"] == "region" and msg["seed"] == 77 and msg["fallback"] is False
    assert msg["result"]["region_name"] == "白塩の野"
    assert [c["axis"] for c in msg["cards"]] == [axis for _, axis, _ in builder.draw_cards.__globals__["DECKS"]["region"]]
    prompt = llm.prompts[0][1]
    assert "アンティキティラ" in prompt and "黒竜" in prompt
    assert all(c["text"] in prompt for c in msg["cards"])


async def test_same_seed_draws_same_cards(make_ctx, sent):
    ctx = make_ctx(FakeLLM(json_responses=[region_result(), region_result()]))
    for _ in range(2):
        await builder.handle_generate_stage(ctx, {"stage": "region", "seed": 5})
    assert sent[0]["cards"] == sent[1]["cards"]


async def test_generate_falls_back_when_ai_fails(make_ctx, sent):
    ctx = make_ctx(FakeLLM(json_responses=[{"broken": True}]))
    await builder.handle_generate_stage(ctx, {"stage": "settlement"})
    assert actions(sent) == ["chat", "builder_stage_result"]
    assert sent[1]["fallback"] is True
    assert sent[1]["result"]["settlement_name"]


async def test_unknown_stage_is_ignored(make_ctx, sent):
    await builder.handle_generate_stage(make_ctx(), {"stage": "dungeon"})
    assert sent == []


class PromptLLM(FakeLLM):
    """応答をプロンプトの中身で選ぶ (並列ジョブの順番に依存しない)"""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    async def query_json(self, prompt, unbounded=False):
        self.prompts.append(("json", prompt))
        return self.answer(prompt)


def chunk_ids(prompt):
    return [p["id"] for p in json.loads(prompt.split("【あなたが書く地点】\n")[1].split("\n")[0])]


def wilderness_core(topos, offset=0):
    plan = [{"id": t["id"] + offset, "title": f"地点{t['id']}", "purpose": f"役割{t['id']}", "safe_rest": t["id"] == 1}
            for t in topos]
    return {"wilderness_title": "塩の道", "public_lore": {"route_overview": "道"}, "canon": ["旅路: 本道"],
            "landmark_plan": plan}


async def test_wilderness_draft_is_split_into_core_and_parallel_landmarks(make_ctx, sent):
    topos = builder.make_wilderness_generator(99).get_room_topologies()

    def answer(prompt):
        if "【あなたが書く地点】" not in prompt:
            return wilderness_core(topos, offset=1)  # AI が 1 始まりで返しても地点グラフの id にそろう
        # 地点の細部も 1 始まりで返ってくる
        return [{"id": i + 1, "tts_narration": f"読み上げ{i}", "gm_notes": f"メモ{i}"} for i in chunk_ids(prompt)]

    llm = PromptLLM(answer)
    await builder.handle_generate_stage(make_ctx(llm), {"stage": "wilderness", "seed": 99})
    assert actions(sent)[-1] == "builder_stage_result" and "chat" not in actions(sent)
    msg = sent[-1]
    assert msg["map"]["seed"] == 99 and msg["map"]["points"] == builder.WILDERNESS_POINT_COUNT
    lms = msg["result"]["landmarks"]
    assert [lm["id"] for lm in lms] == [t["id"] for t in topos]
    assert lms[0]["title"] == "地点0" and lms[0]["tts_narration"] == "読み上げ0" and lms[0]["gm_notes"] == "メモ0"
    assert {lm["role"] for lm in lms} >= {"start", "goal"}
    assert lms[1]["safe_rest"] is True
    assert "landmark_plan" not in msg["result"]
    assert '"neighbors"' in llm.prompts[0][1]
    detail_prompts = [p for _, p in llm.prompts[1:]]
    assert len(detail_prompts) == -(-len(topos) // builder.ROOMS_PER_JOB)
    assert sorted(i for p in detail_prompts for i in chunk_ids(p)) == [t["id"] for t in topos]


async def test_wilderness_keeps_the_plan_when_details_fail(make_ctx, sent):
    topos = builder.make_wilderness_generator(99).get_room_topologies()
    llm = PromptLLM(lambda p: None if "【あなたが書く地点】" in p else wilderness_core(topos))
    await builder.handle_generate_stage(make_ctx(llm), {"stage": "wilderness", "seed": 99})
    assert actions(sent) == ["chat", "builder_stage_result"]
    assert "細部が一部" in sent[0]["content"]
    msg = sent[-1]
    assert msg["fallback"] is False
    assert [lm["gm_notes"] for lm in msg["result"]["landmarks"]] == [f"役割{t['id']}" for t in topos]


def settlement_core():
    return {
        "settlement_name": "塩見の里",
        "public_lore": {"visible_sorrows": "荷車が戻らない",
                        "key_npcs": [{"name": "エドガー", "role": "職人頭", "want": "道を取り戻す"}]},
        "dm_secrets": {"sorrow_truth": "塔の一団"},
        "canon": ["集落: 塩見の里"],
    }


async def test_settlement_is_split_into_core_and_secrets(make_ctx, sent):
    extra = {
        "key_npcs": [{"name": "エドガー", "personality": "寡黙"}, {"name": "知らない人", "personality": "x"}],
        "dm_secrets": {"npc_hidden_agenda": "裏取引", "sorrow_truth": "書き換えようとした真相"},
        "audio_prompts": {"joy_bgm_en": "lute"},
        "canon": ["集落: 塩見の里", "人物: エドガーは裏取引を知る"],
        "open_threads": ["裏取引の相手"],
    }
    llm = FakeLLM(json_responses=[settlement_core(), extra])
    await builder.handle_generate_stage(make_ctx(llm), {"stage": "settlement"})
    assert actions(sent) == ["builder_stage_result"]
    res = sent[0]["result"]
    assert res["public_lore"]["key_npcs"] == [{"name": "エドガー", "role": "職人頭", "want": "道を取り戻す", "personality": "寡黙"}]
    assert res["dm_secrets"] == {"npc_hidden_agenda": "裏取引", "sorrow_truth": "塔の一団"}
    assert res["canon"] == ["集落: 塩見の里", "人物: エドガーは裏取引を知る"]
    assert res["open_threads"] == ["裏取引の相手"] and res["audio_prompts"] == {"joy_bgm_en": "lute"}
    assert "塩見の里" in llm.prompts[1][1] and "塔の一団" in llm.prompts[1][1]


async def test_settlement_keeps_the_core_when_secrets_fail(make_ctx, sent):
    await builder.handle_generate_stage(make_ctx(FakeLLM(json_responses=[settlement_core(), None])), {"stage": "settlement"})
    assert actions(sent) == ["chat", "builder_stage_result"]
    assert sent[1]["fallback"] is False and sent[1]["result"]["settlement_name"] == "塩見の里"


def spy_voices(ctx):
    """ctx.tts.synthesize に渡された声の指定を記録する"""
    voices, original = [], ctx.tts.synthesize

    async def synthesize(text, file_name, **kwargs):
        voices.append(kwargs)
        return await original(text, file_name, **kwargs)

    ctx.tts.synthesize = synthesize
    return voices


async def test_build_wilderness_regenerates_the_same_points(make_ctx, sent, tmp_path):
    topos = builder.make_wilderness_generator(1234).get_room_topologies()
    landmarks = [{"id": t["id"], "title": f"地点{t['id']}", "tts_narration": f"風{t['id']}"} for t in topos]
    ctx = make_ctx()
    voices = spy_voices(ctx)
    await builder.handle_build_scene(ctx, {
        "scene_mode": "wilderness", "name": "塩の道", "public_lore": "道", "dm_secrets": "見張り",
        "map": {"seed": 1234}, "landmarks": landmarks,
    })
    assert actions(sent) == ["create_dungeon_scene", "play_sound"]
    # 完了報告は Geminiちゃんの声 (ナレーターの声を変えても変わらない)
    assert voices == [tts.support_voice()]
    scene = main(sent)[0]
    assert scene["scene_mode"] == "wilderness" and scene["room_count"] == len(topos)
    assert [r["title"] for r in scene["rooms"]] == [f"地点{t['id']}" for t in topos]
    assert scene["lore"]["dm_secrets"] == "見張り"
    json.dumps(scene)


async def test_build_wilderness_without_seed_drops_landmarks(make_ctx, sent):
    ctx = make_ctx()
    await builder.handle_build_scene(ctx, {"scene_mode": "wilderness", "landmarks": [{"id": 0, "title": "古い下書き"}]})
    assert actions(sent) == ["chat", "create_dungeon_scene", "play_sound"]
    assert "seed" in sent[0]["content"]
    assert all(r["title"] != "古い下書き" for r in main(sent)[1]["rooms"])
    # 画面の「構築中」は最後に消す
    assert sent[-1]["action"] == "builder_progress" and sent[-1]["finished"] is True and sent[-1]["kind"] == "build"


async def test_build_site_assigns_roles_before_asking_ai(make_ctx, sent):
    llm = FakeLLM()
    ctx = make_ctx(llm)
    voices = spy_voices(ctx)
    subs = [{"title": "閂を外す", "target_topology": "dead_end"}, {"title": "副官と取引", "target_topology": "hub"}]
    await builder.handle_build_scene(ctx, {
        "scene_mode": "ruins", "name": "見張り塔", "size_key": "xlarge", "density": 0.5, "loop_ratio": 0.2, "seed": 8,
        "public_lore": "大目標: 荷を取り返す", "dm_secrets": "頭目は孤立している", "sub_objectives": subs,
        "parents": [{"category": "settlement", "name": "グレン", "canon": ["手仕事: 香木の鈴"]}],
    })
    # マップだけのシーンを先に作り、情景は後から (この AI は何も返さない)
    assert actions(sent) == ["create_dungeon_scene", "chat", "chat", "play_sound"]
    assert voices == [tts.support_voice()]
    scene = main(sent)[0]
    assert scene["scene_key"].startswith("builder-8-")
    assert scene["grid_width"] == 50  # xlarge は既定の medium に丸める (M12)
    prompts = [p for kind, p in llm.prompts if kind == "json"]
    assert prompts and all("頭目は孤立している" in p and "香木の鈴" in p for p in prompts)
    assert all("中目標「閂を外す」の場所" in p for p in prompts)
    sub_rooms = [r for r in scene["rooms"] if r["role"] == "sub_goal"]
    assert len(sub_rooms) == 2  # AI の情景が無くても中目標のバッジは付く
    assert scene["lore"]["history_summary"] == "大目標: 荷を取り返す"


def test_apply_room_plan_overrides_ai_roles():
    plan = {0: {"role": "start", "label": "入口"}, 1: {"role": "empty", "label": "空室"}, 2: {"role": "sub_goal", "label": "中目標「鍵」"}}
    flavors = [{"id": 0, "role": "boss"}, {"id": 1, "role": "treasure", "title": "倉"}]
    result = {r["id"]: r for r in builder.apply_room_plan(flavors, plan)}
    assert result[0]["role"] == "start" and result[0]["safe_rest"] is True
    assert result[1]["role"] == "empty" and result[1]["is_empty"] is True and result[1]["title"] == "倉"
    assert result[2]["role"] == "sub_goal" and "鍵" in result[2]["gm_notes"]


async def test_wilderness_details_run_one_at_a_time_on_agy(make_ctx, sent):
    """agy は同時に起動すると応答が返らなくなるため、地点の描写も 1 本ずつ問い合わせる"""
    import asyncio

    topos = builder.make_wilderness_generator(99).get_room_topologies()
    running, peak = 0, 0

    class OneAtATime(PromptLLM):
        concurrency = 1

        async def query_json(self, prompt, unbounded=False):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1
            return await super().query_json(prompt, unbounded)

    def answer(prompt):
        if "【あなたが書く地点】" not in prompt:
            return wilderness_core(topos)
        return [{"id": i, "tts_narration": "音"} for i in chunk_ids(prompt)]

    await builder.handle_generate_stage(make_ctx(OneAtATime(answer)), {"stage": "wilderness", "seed": 99})
    assert peak == 1
    assert all(lm.get("tts_narration") == "音" for lm in sent[-1]["result"]["landmarks"])


async def test_split_stages_report_each_step_for_the_progress_line(make_ctx, sent, progress):
    await builder.handle_generate_stage(make_ctx(FakeLLM(json_responses=[settlement_core(), {}])), {"stage": "settlement"})
    assert [(p[1], p[2], p[3]) for p in progress] == [(1, 2, "骨組み"), (2, 2, "人物と秘密")]


async def test_stage_status_sends_builder_progress(make_ctx, sent, monkeypatch):
    from gm_server import deadline

    monkeypatch.undo()
    ctx = make_ctx()
    send = builder.stage_status(ctx, "wilderness", 2, 2, "地点の描写")
    await send(12.6, 287.4, "完了 1/2")
    assert sent == [{
        "action": "builder_progress", "kind": "draft", "finished": False, "stage": "wilderness", "step": 2, "steps": 2, "step_label": "地点の描写",
        "detail": "完了 1/2", "elapsed": 12, "remaining": 287,
    }]
    assert deadline.STATUS_INTERVAL > 0


async def test_build_site_reflects_rooms_as_each_job_finishes(make_ctx, sent):
    """できた区画から順に update_dungeon_rooms で反映する"""
    def answer(prompt):
        rooms = json.loads(prompt.split("【あなたが書く区画】\n")[1].split("\n")[0])
        return [{"id": r["id"], "title": f"区画{r['id']}", "tts_narration": "音"} for r in rooms]

    ctx = make_ctx(PromptLLM(answer))
    await builder.handle_build_scene(ctx, {"scene_mode": "ruins", "name": "塔", "seed": 8, "density": 0.5})
    msgs = main(sent)
    scene = msgs[0]
    updates = [m for m in msgs if m["action"] == "update_dungeon_rooms"]
    assert updates and all(u["scene_key"] == scene["scene_key"] for u in updates)
    updated = [r for u in updates for r in u["rooms"]]
    assert len(updated) == len({r["id"] for r in updated})
    assert all(r["title"] == f"区画{r['id'] - 1}" for r in updated)  # 部屋ノートの id は 1 始まり
    # 先に送ったシーンの区画は、AI の情景が無い既定の文面
    assert not any(r["title"].startswith("区画") for r in scene["rooms"])
    assert msgs[-1]["action"] == "play_sound" and f"{len(updated)}区画に情景" in msgs[-2]["content"]
    progress_kinds = {(m["kind"], m["step_label"]) for m in sent if m["action"] == "builder_progress"}
    assert ("build", "完了") in progress_kinds


async def test_build_site_by_script_does_not_ask_ai(make_ctx, sent):
    """「スクリプト」で構築: AI に区画の情景を頼まず、仮の区画のままシーンを作って終える"""
    llm = FakeLLM()
    ctx = make_ctx(llm)
    await builder.handle_build_scene(ctx, {"scene_mode": "ruins", "name": "塔", "seed": 8, "use_ai": False})
    assert llm.prompts == []
    assert actions(sent) == ["create_dungeon_scene", "play_sound"]
    assert "scene_key" not in main(sent)[0]
    steps = {m["steps"] for m in sent if m["action"] == "builder_progress"}
    assert steps == {1}


async def test_build_wilderness_by_script_ignores_landmarks(make_ctx, sent):
    ctx = make_ctx()
    await builder.handle_build_scene(ctx, {"scene_mode": "wilderness", "use_ai": False,
                                           "landmarks": [{"id": 0, "title": "古い下書き"}]})
    # seed が無くても警告は出さない (自分で作る DM 向け)
    assert actions(sent) == ["create_dungeon_scene", "play_sound"]
    assert all(r["title"] != "古い下書き" for r in main(sent)[0]["rooms"])


async def test_auto_extend_flag_is_passed_to_the_deadline(make_ctx, sent, monkeypatch):
    seen = []

    async def fake_deadline(ctx, label, awaitable, limit, **kwargs):
        seen.append(kwargs.get("auto_extend"))
        return await awaitable

    monkeypatch.setattr(builder, "run_with_deadline", fake_deadline)
    ctx = make_ctx(FakeLLM(json_responses=[region_result(), region_result()]))
    await builder.handle_generate_stage(ctx, {"stage": "region", "auto_extend": True})
    await builder.handle_generate_stage(ctx, {"stage": "region"})
    assert seen == [True, False]
