import json

import pytest

from gm_server.handlers import adventure_log
from gm_server.router import HANDLERS, dispatch
from conftest import FakeLLM


def request(**more):
    return {
        "event": "adventure_log_request", "mode": "create", "session_number": 2,
        "transcript": [
            {"speaker": "AI GM", "text": "<p>霧の港町に着いた。</p>"},
            {"speaker": "アリア", "text": "宿の女将に話を聞こう"},
            {"speaker": "女将マルタ", "text": "北の灯台に近づいちゃいけないよ"},
            {"speaker": "", "text": "  "},
        ],
        "pcs": ["アリア", "ボルグ"],
        "place_hints": ["霧の港町", "霧の港町"],
        "inventory_changes": [{"holder": "アリア", "name": "灯台の鍵", "quantity": 1}],
        **more,
    }


def actions(sent):
    return [m["action"] for m in sent]


def test_adventure_log_is_registered():
    assert HANDLERS["adventure_log_request"] is adventure_log.handle


@pytest.mark.parametrize("prompt, expected", [
    ("記録を作って", True),
    ("冒険日誌を更新して", True),
    ("今日のセッションをまとめて", True),
    ("セッションに出す NPC を作って", False),
    ("ダンジョンを作って", False),
    ("記録", False),
])
def test_is_adventure_log_intent(prompt, expected):
    assert adventure_log.is_adventure_log_intent(prompt) is expected


def test_log_mode():
    assert adventure_log.log_mode("記録を更新して") == "update"
    assert adventure_log.log_mode("日誌に追記して") == "update"
    assert adventure_log.log_mode("記録を作って") == "create"


async def test_ai_command_asks_fvtt_to_collect(make_ctx, sent):
    await dispatch(make_ctx(), json.dumps({"event": "ai_command", "user": "GM", "prompt": "記録を更新して"}))
    assert actions(sent) == ["chat", "adventure_log_collect"]
    assert sent[1]["mode"] == "update"


def test_transcript_keeps_newest_lines_within_limit(monkeypatch):
    monkeypatch.setattr(adventure_log, "MAX_TRANSCRIPT_CHARS", 30)
    lines = adventure_log.normalize_transcript([{"speaker": "A", "text": f"発言{i}" * 2} for i in range(10)])
    assert lines[-1] == "A: 発言9発言9" and len("\n".join(lines)) <= 30


async def test_handle_writes_log_from_ai(make_ctx, sent):
    llm = FakeLLM(json_responses=[{
        "title": "霧の港と灯台の噂",
        "replay": "一行は霧の港町にたどり着いた。\n女将マルタ「北の灯台に近づいちゃいけないよ」",
        "items": [{"name": "干し魚", "holder": "ボルグ", "note": "市場で買った"}],
        "people": [{"name": "女将マルタ", "note": "宿の女将。灯台を恐れている"}, {"name": ""}],
        "places": [{"name": "霧の港町", "note": "最初に着いた町"}],
    }])
    await adventure_log.handle(make_ctx(llm), request())
    prompt = llm.prompts[0][1]
    assert "第2回の記録を新しく書きます" in prompt
    assert "アリア: 宿の女将に話を聞こう" in prompt and "<p>" not in prompt
    assert "- アリア: 灯台の鍵" in prompt and "霧の港町" in prompt
    msg = sent[0]
    assert msg["action"] == "adventure_log_write" and msg["session_number"] == 2 and not msg["fallback"]
    assert msg["title"] == "霧の港と灯台の噂"
    # 所持品の差分は AI が書き漏らしても載る
    assert [i["name"] for i in msg["items"]] == ["干し魚", "灯台の鍵"]
    assert [p["name"] for p in msg["people"]] == ["女将マルタ"]


async def test_handle_falls_back_to_excerpt(make_ctx, sent):
    await adventure_log.handle(make_ctx(FakeLLM(json_responses=[None])), request(mode="update", previous_replay="<p>前回</p>"))
    msg = sent[0]
    assert msg["fallback"] and msg["mode"] == "update" and msg["title"] == "第2回の記録"
    assert "女将マルタ: 北の灯台に近づいちゃいけないよ" in msg["replay"]
    assert [i["name"] for i in msg["items"]] == ["灯台の鍵"]
    assert [p["name"] for p in msg["places"]] == ["霧の港町"]


async def test_update_prompt_includes_previous_replay(make_ctx, sent):
    llm = FakeLLM()
    await adventure_log.handle(make_ctx(llm), request(mode="update", previous_replay="<p>前回は森を抜けた</p>"))
    prompt = llm.prompts[0][1]
    assert "第2回の続きを書きます" in prompt and "第2回全体" in prompt and "前回は森を抜けた" in prompt


async def test_handle_ignores_empty_transcript(make_ctx, sent):
    llm = FakeLLM()
    await adventure_log.handle(make_ctx(llm), request(transcript=[]))
    assert sent == [] and llm.prompts == []
