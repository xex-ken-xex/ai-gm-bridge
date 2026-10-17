import json

from gm_server.handlers import npc_dialogue
from gm_server.router import HANDLERS
from conftest import FakeCommunicate, FakeLLM


def listener(npc_id, name, distance=10, **more):
    return {"id": npc_id, "name": name, "distance_ft": distance, "goal": "店を守る", "trust": 0,
            "public_known": "街道の噂", "secret_known": "密輸の帳簿を隠している", "ignorance": "王都の政治",
            "short_term_memory": [], "long_term_memory": ["[鮮明] (重要度:4) 盗賊に襲われた"], **more}


def request(**more):
    return {"event": "npc_dialogue_request", "speaker_name": "アリア", "speaker_token_id": "pc1", "scene_id": "s1",
            "player_input": "帳簿を見せてくれ", "room_name": "酒場", "room_flavor": "煙たい",
            "listening_npcs": [listener("n2", "ボルグ", 25), listener("n1", "エドガー", 5)], **more}


def test_npc_dialogue_is_registered():
    assert HANDLERS["npc_dialogue_request"] is npc_dialogue.handle


def test_normalize_listeners_drops_bad_entries_and_sorts_by_distance():
    raw = [listener("a", "A", 30), {"name": "id なし"}, listener("a", "重複", 1), "x",
           listener("b", "B", 5, trust=99), listener("c", "C", 10), listener("d", "D", 20), listener("e", "E", 40)]
    got = npc_dialogue.normalize_listeners(raw)
    assert [n["id"] for n in got] == ["b", "c", "d", "a"]  # 近い順に 4 人まで
    assert got[0]["trust"] == 5
    assert npc_dialogue.normalize_listeners(None) == []


def test_prompt_contains_knowledge_but_not_voice():
    data = request()
    listeners = npc_dialogue.normalize_listeners([listener("n1", "エドガー", voice={"name": "ja-JP-NanamiNeural"})])
    prompt = npc_dialogue.build_npc_dialogue_prompt(data, listeners)
    assert "密輸の帳簿" in prompt and "王都の政治" in prompt and "帳簿を見せてくれ" in prompt and "酒場" in prompt
    assert "Nanami" not in prompt


def test_normalize_result_keeps_ids_inside_the_listeners_and_clamps_numbers():
    listeners = npc_dialogue.normalize_listeners(request()["listening_npcs"])
    raw = {
        "primary_npc_id": "知らない id", "primary_npc_name": "ボルグ",
        "narrative_action": "<b>腕を組んだ</b>", "speech": "「見せられねえな」",
        "ambient_reactions": [{"npc_id": "n1", "silent_emote": "目を逸らす"}, {"npc_id": "n2", "silent_emote": "本人"},
                              {"npc_id": "zz", "silent_emote": "部外者"}],
        "physical_action": "fly_away", "trust_delta": -9,
        "new_memory": {"summary": "帳簿を探られた", "importance": 12},
        "check_offer": {"skill": "説得", "dc": 99, "target_secret_hint": "帳簿は樽の下"},
    }
    res = npc_dialogue.normalize_dialogue_result(raw, listeners, "アリア", "帳簿")
    assert res["primary_npc_id"] == "n2" and res["primary_npc_name"] == "ボルグ"  # 名前で引き当てる
    assert res["narrative_action"] == "腕を組んだ" and res["speech"] == "見せられねえな"
    assert res["ambient_reactions"] == [{"npc_id": "n1", "npc_name": "エドガー", "silent_emote": "目を逸らす"}]
    assert res["physical_action"] == "stay" and res["trust_delta"] == -2
    assert res["new_memory"] == {"summary": "帳簿を探られた", "importance": 5}
    assert res["check_offer"] == {"skill": "説得", "dc": 30, "target_secret_hint": "帳簿は樽の下"}
    no_check = npc_dialogue.normalize_dialogue_result({**raw, "check_offer": {"skill": None}}, listeners, "アリア", "帳簿")
    assert no_check["check_offer"] is None


def test_unreadable_answer_falls_back_to_the_nearest_npc():
    listeners = npc_dialogue.normalize_listeners(request()["listening_npcs"])
    res = npc_dialogue.normalize_dialogue_result("壊れた応答", listeners, "アリア", "帳簿")
    assert res["fallback"] is True and res["primary_npc_id"] == "n1" and res["speech"]


def test_npc_voice_rejects_unknown_names():
    assert npc_dialogue.npc_voice({"voice": {"name": "ja-JP-NanamiNeural", "rate": "+10%"}})["voice"] == "ja-JP-NanamiNeural"
    assert npc_dialogue.npc_voice({"voice": {"name": "rm -rf"}})["voice"] == npc_dialogue.DEFAULT_NPC_VOICE
    assert npc_dialogue.npc_voice(None)["pitch"] == "+0Hz"


async def test_handle_sends_the_response_then_the_npc_voice(make_ctx, sent):
    answer = {"primary_npc_id": "n1", "primary_npc_name": "エドガー", "narrative_action": "眉をひそめた。",
              "speech": "帳簿？ 何のことだ", "trust_delta": -1, "check_offer": {"skill": "看破", "dc": 14, "target_secret_hint": "樽の下"}}
    llm = FakeLLM(json_responses=[answer])
    data = request()
    data["listening_npcs"][1]["voice"] = {"name": "ja-JP-NanamiNeural", "rate": "-10%", "pitch": "+5Hz"}
    await npc_dialogue.handle(make_ctx(llm), data)
    assert [m["action"] for m in sent] == ["npc_dialogue_response", "play_sound"]
    resp = sent[0]
    assert resp["speaker_token_id"] == "pc1" and resp["scene_id"] == "s1"
    assert resp["result"]["check_offer"]["target_secret_hint"] == "樽の下"
    assert "エドガー" in sent[1]["subtitle"]
    assert FakeCommunicate.calls[-1].voice == "ja-JP-NanamiNeural"
    json.dumps(sent)


async def test_handle_ignores_requests_without_listeners(make_ctx, sent):
    llm = FakeLLM()
    await npc_dialogue.handle(make_ctx(llm), request(listening_npcs=[]))
    assert sent == [] and llm.prompts == []
