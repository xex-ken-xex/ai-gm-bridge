import json

import pytest

from gm_server import skills
from gm_server.handlers import ai_command
from gm_server.router import dispatch
from conftest import FakeLLM


def actions(sent):
    return [m["action"] for m in sent]


def router_llm(*responses):
    return FakeLLM(support_responses=[json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r for r in responses])


def test_every_ready_skill_has_runner():
    for s in skills.SKILLS:
        assert (s.name in ai_command.SKILL_RUNNERS) is s.ready, s.name


def test_router_prompt_embeds_skills_and_gm_text():
    prompt = skills.build_router_prompt("港町を作りたい", persona="私はGeminiちゃん")
    assert prompt.startswith("私はGeminiちゃん")
    assert "- build_town（町生成スキル）" in prompt
    assert "create_npc（NPC作成スキル（準備中））" in prompt
    assert prompt.rstrip().endswith("港町を作りたい")
    assert "{skills}" not in prompt and "{gm_text}" not in prompt


def test_help_lists_display_names():
    text = skills.help_text()
    assert "ダンジョン生成スキル" in text and "NPC作成スキル（準備中）" in text and "冒険記録スキル" in text
    assert "ヘルプ:" not in text


@pytest.mark.parametrize("resp, expected", [
    ("ただの文章", None),
    ('{"type":"unknown"}', None),
    ('{"type":"answer","text":" 答え "}', {"type": "answer", "text": "答え"}),
    ('{"type":"skill","skill":"fly","text":"空は飛べないよ"}', {"type": "answer", "text": "空は飛べないよ"}),
    ('{"type":"skill","skill":"fly"}', None),
    ('{"type":"clarify","question":"どっち？","options":[{"label":"x","skill":"fly"}]}', {"type": "answer", "text": "どっち？"}),
])
def test_parse_router_response_edge_cases(resp, expected):
    assert skills.parse_router_response(resp) == expected


def test_parse_router_response_keeps_only_simple_params():
    r = skills.parse_router_response('前置き {"type":"skill","skill":"build_town","params":{"request":"港町","x":{"a":1},"y":""},"say":"任せて"}')
    assert r == {"type": "skill", "skill": "build_town", "params": {"request": "港町"}, "say": "任せて", "text": ""}


async def test_ai_command_sends_router_prompt_with_persona_once(make_ctx, sent):
    llm = router_llm({"type": "answer", "text": "はい"}, {"type": "answer", "text": "うん"})
    ctx = make_ctx(llm)
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "毒の判定は？"}))
    assert "星詠みのサポートGM" in llm.prompts[0][1] and "毒の判定は？" in llm.prompts[0][1]
    llm.conversation_id = "conv-1"
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "続き"}))
    assert "星詠みのサポートGM" not in llm.prompts[1][1]
    assert [m["content"] for m in sent] == ["はい", "うん"]


async def test_build_town_opens_builder_with_request(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "build_town", "params": {"request": "密輸の港町"}, "say": "町生成スキルで作るね！"})
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "港町を作りたい"}))
    assert actions(sent) == ["chat", "open_builder"]
    assert sent[0]["content"] == "町生成スキルで作るね！"
    assert sent[1] == {"action": "open_builder", "tab": "settlement", "user_prompt": "密輸の港町"}


async def test_builder_request_falls_back_to_gm_text(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "build_region", "params": {}})
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "北の王国"}))
    assert sent[1] == {"action": "open_builder", "tab": "region", "user_prompt": "北の王国"}


async def test_generate_dungeon_with_scene_mode_skips_mode_card(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "generate_dungeon", "params": {"request": "ゴブリンの洞窟", "scene_mode": "underground"}})
    ctx = make_ctx(llm)
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "ゴブリンの巣の洞窟を今すぐ"}))
    assert ctx.state.pending_dungeon == {"user_prompt": "ゴブリンの洞窟", "scene_mode": "underground"}
    assert sent[-1]["choices"][0]["value"].startswith("dungeon_size:")


async def test_generate_dungeon_without_scene_mode_asks_mode(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "generate_dungeon", "params": {"scene_mode": "moon"}})
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "マップ"}))
    assert sent[-1]["choices"][0]["value"] == "dungeon_mode:underground"


async def test_adventure_log_skill_uses_mode(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "adventure_log", "params": {"mode": "update"}})
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "今日の分を足しておいて"}))
    assert actions(sent) == ["chat", "adventure_log_collect"]
    assert sent[1]["mode"] == "update"


async def test_not_ready_skill_shows_notice_and_text(make_ctx, sent):
    llm = router_llm({"type": "skill", "skill": "create_npc", "params": {"role": "宿屋の女将"}, "text": "女将マルタ: 豪快"})
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": "宿屋の女将を作って"}))
    assert actions(sent) == ["chat"]
    assert "準備中" in sent[0]["content"] and "女将マルタ" in sent[0]["content"]


async def test_empty_prompt_shows_help_without_agy(make_ctx, sent):
    llm = FakeLLM()
    await dispatch(make_ctx(llm), json.dumps({"event": "ai_command", "prompt": ""}))
    assert llm.prompts == []
    assert "町生成スキル" in sent[0]["content"]


async def test_clarify_card_then_choice_runs_skill(make_ctx, sent):
    llm = router_llm({"type": "clarify", "question": "どっちを作ろうか？", "options": [
        {"label": "すぐ遊べるマップ", "skill": "generate_dungeon", "params": {}},
        {"label": "遺跡の設定から", "skill": "build_scenario", "params": {}},
    ]})
    ctx = make_ctx(llm)
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "ダンジョンを作って"}))
    assert sent[-1]["action"] == "interactive_card"
    assert sent[-1]["message"] == "どっちを作ろうか？"
    assert [c["value"] for c in sent[-1]["choices"]] == ["ai_skill:0", "ai_skill:1"]

    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "ai_skill:1", "label": "遺跡の設定から"}))
    assert sent[-1] == {"action": "open_builder", "tab": "site", "user_prompt": "ダンジョンを作って"}
    assert ctx.state.pending_skill_options == []

    # 一度選んだカードをもう一度押しても何もしない
    count = len(sent)
    await dispatch(ctx, json.dumps({"event": "ai_choice", "value": "ai_skill:0", "label": "すぐ遊べるマップ"}))
    assert len(sent) == count


async def test_no_agy_reply_falls_back_to_keywords(make_ctx, sent):
    ctx = make_ctx(FakeLLM())
    await dispatch(ctx, json.dumps({"event": "ai_command", "prompt": "氷の洞窟ダンジョン"}))
    assert sent[-1]["choices"][0]["value"] == "dungeon_mode:underground"
