import json

from gm_server.handlers import npc_turn
from gm_server.router import HANDLERS
from conftest import FakeCommunicate, FakeLLM


def perception(**more):
    return {
        "token_id": "orc1", "npc_name": "オークの戦士", "npc_role": "humanoid", "npc_bio": "<p>縄張りを守る</p>",
        "npc_goal": "砦を守る", "hp_current": 4, "hp_max": 15, "health_status": "負傷・消耗", "move_speed": 30,
        "available_actions": [{"name": "グレートアックス", "type": "weapon"}, {"name": "突進", "type": "feat"}, "投げ槍"],
        "room_name": "見張り台", "room_flavor": "崩れた石壁", "room_gm_notes": "油の樽がある",
        "perceived_targets": [
            {"token_id": "pc2", "name": "ボルド", "distance_ft": 30, "adjacent": False, "has_los": True},
            {"token_id": "pc1", "name": "アリア", "distance_ft": 5, "adjacent": True, "has_los": "yes",
             "health_status": "倒れている"},
            {"name": "id なし"},
        ],
        "voice": {"name": "ja-JP-NanamiNeural", "rate": "+5%", "pitch": "-5Hz"},
        **more,
    }


def request(**more):
    return {"event": "npc_turn_request", "scene_id": "s1", "combat": {"id": "c1", "round": 2, "turn": 3},
            "perception": perception(), **more}


def test_npc_turn_is_registered():
    assert HANDLERS["npc_turn_request"] is npc_turn.handle


def test_normalize_perception_cleans_and_sorts_targets():
    perc = npc_turn.normalize_perception(perception())
    assert [t["token_id"] for t in perc["perceived_targets"]] == ["pc1", "pc2"]
    assert perc["perceived_targets"][0]["has_los"] is None  # 真偽値でなければ不明
    assert perc["npc_bio"] == "縄張りを守る"
    assert [a["name"] for a in perc["available_actions"]] == ["グレートアックス", "突進", "投げ槍"]
    assert npc_turn.normalize_perception({"npc_name": "id なし"}) is None
    assert npc_turn.normalize_perception("x") is None


def test_prompt_contains_situation_but_not_voice():
    perc = npc_turn.normalize_perception(perception())
    prompt = npc_turn.build_npc_tactics_prompt(perc, 2)
    assert "グレートアックス" in prompt and "油の樽" in prompt and "砦を守る" in prompt and '"pc2"' in prompt
    assert "Nanami" not in prompt


def test_sanitize_formula():
    assert npc_turn.sanitize_formula("1d20+5") == "1d20+5"
    assert npc_turn.sanitize_formula(" 2d6 + (1d4) ") == "2d6 + (1d4)"
    for bad in (None, "", "5", "1d20+@abilities.str.mod", "1d20)", "(1d6", "1d6; alert(1)"):
        assert npc_turn.sanitize_formula(bad) is None, bad


def test_normalize_decision_keeps_targets_and_actions_inside_the_perception():
    perc = npc_turn.normalize_perception(perception())
    raw = {"internal_thought": "逃げる", "move_intent": "teleport", "target_token_id": "知らない",
           "action_type": "dance", "action_name": "グレートアックス".lower(), "dice_formula": "1d20+@x",
           "emote": "<b>斧を振り上げる</b>", "speech": "「死ね！」"}
    d = npc_turn.normalize_turn_decision(raw, perc)
    assert d["move_intent"] == "hold_position" and d["target_token_id"] is None
    assert d["action_type"] == "attack" and d["action_name"] == "グレートアックス"
    assert d["dice_formula"] is None and d["emote"] == "斧を振り上げる" and d["speech"] == "死ね！"
    unknown = npc_turn.normalize_turn_decision({**raw, "action_name": "メテオ"}, perc)
    assert unknown["action_name"] is None
    # 接近なのに狙いが無ければ、倒れていない最寄りの PC
    approach = npc_turn.normalize_turn_decision({**raw, "move_intent": "approach_target"}, perc)
    assert approach["move_intent"] == "approach_target" and approach["target_token_id"] == "pc2"


def test_unreadable_answer_falls_back_to_attacking_the_nearest_standing_pc():
    perc = npc_turn.normalize_perception(perception())
    d = npc_turn.normalize_turn_decision("壊れた応答", perc)
    assert d["fallback"] is True and d["target_token_id"] == "pc2" and d["action_name"] == "グレートアックス"
    alone = npc_turn.normalize_perception(perception(perceived_targets=[], available_actions=[]))
    d = npc_turn.normalize_turn_decision(None, alone)
    assert d["move_intent"] == "hold_position" and d["target_token_id"] is None and d["dice_formula"] == "1d20"


async def test_handle_echoes_the_turn_and_speaks_with_the_npc_voice(make_ctx, sent):
    answer = {"internal_thought": "傷が深い", "move_intent": "retreat_from_enemies", "target_token_id": "pc2",
              "action_type": "dodge_or_flee", "action_name": None, "dice_formula": None,
              "emote": "オークは後ずさる", "speech": "覚えていろ"}
    await npc_turn.handle(make_ctx(FakeLLM(json_responses=[answer])), request())
    assert [m["action"] for m in sent] == ["execute_npc_turn", "play_sound"]
    turn = sent[0]
    assert turn["token_id"] == "orc1" and turn["scene_id"] == "s1"
    assert turn["combat"] == {"id": "c1", "round": 2, "turn": 3}
    assert turn["decision"]["move_intent"] == "retreat_from_enemies" and turn["decision"]["internal_thought"] == "傷が深い"
    assert "オークの戦士" in sent[1]["subtitle"]
    assert FakeCommunicate.calls[-1].voice == "ja-JP-NanamiNeural"
    json.dumps(sent)


async def test_handle_ignores_requests_without_a_token(make_ctx, sent):
    llm = FakeLLM()
    await npc_turn.handle(make_ctx(llm), request(perception={"npc_name": "誰"}))
    assert sent == [] and llm.prompts == []
