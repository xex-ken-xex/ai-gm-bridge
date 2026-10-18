import asyncio
import json

from conftest import FakeCommunicate, FakeLLM
from test_tts import Clock, FakePost, make

from gm_server import protocol
from gm_server.context import Context, SessionState
from gm_server.router import dispatch
from gm_server.tts import support_voice


def run(coro):
    return asyncio.run(coro)


async def _no_sleep(_seconds):
    return None


def ctx_with(tts, sent):
    async def send_json(msg):
        sent.append(msg)

    return Context(send_json=send_json, llm=FakeLLM(), tts=tts, state=SessionState(), data_dir=tts.data_dir, sleep=_no_sleep)


def test_play_sound_gm_only_flag():
    assert "gm_only" not in protocol.play_sound("a.mp3")
    assert protocol.play_sound("a.mp3", gm_only=True)["gm_only"] is True


async def test_voice_test_with_edge_plays_only_for_gm(make_ctx, sent):
    await dispatch(make_ctx(), json.dumps({"event": "voice_test", "voice": "narrator"}))
    assert [m["action"] for m in sent] == ["play_sound", "progress"]
    assert sent[0]["gm_only"] is True and sent[0]["src"].startswith("voice_test")
    assert "edge-tts で読み上げました (AI_GM_TTS_ENGINE が edge です)" in sent[1]["message"]
    assert "ナレーター" in sent[1]["message"]
    assert FakeCommunicate.calls[0].text == sent[0]["subtitle"]


def test_voice_test_reports_irodori_and_voice(tmp_path):
    sent, post = [], FakePost()
    tts = make(tmp_path, post)
    run(dispatch(ctx_with(tts, sent), json.dumps({"event": "voice_test", "voice": "support"})))
    assert [m["action"] for m in sent] == ["play_sound", "progress"]
    body = post.calls[0][1]
    assert body["irodori"]["caption"] == support_voice()["caption"]
    message = sent[1]["message"]
    assert message.startswith("声のテスト (Geminiちゃん): Irodori-TTS で読み上げました")
    assert support_voice()["caption"] in message and "種" in message


def test_voice_test_retries_irodori_during_pause_and_reports_fallback(tmp_path):
    sent, post = [], FakePost(fail=True)
    tts = make(tmp_path, post, clock=Clock())
    run(tts.synthesize("先の失敗", "a.mp3"))  # 失敗して edge-tts だけの間に入る
    assert len(post.calls) == 1
    run(dispatch(ctx_with(tts, sent), json.dumps({"event": "voice_test"})))
    # テストでは間を無視してもう一度 Irodori を試し、だめなら edge-tts で鳴らして理由を出す
    assert len(post.calls) == 2
    assert sent[-1]["action"] == "progress"
    assert "Irodori-TTS で作れなかったため、edge-tts で読み上げました (接続できません)" in sent[-1]["message"]
    assert any(m["action"] == "play_sound" and m["gm_only"] for m in sent)


def test_last_engine_reports_pause(tmp_path):
    post = FakePost(fail=True)
    tts = make(tmp_path, post)
    run(tts.synthesize("一回目", "a.mp3"))
    assert tts.last_engine == "edge" and tts.last_irodori_error == "接続できません"
    run(tts.synthesize("二回目", "b.mp3"))
    assert tts.last_engine == "edge" and "edge-tts を使う間" in tts.last_irodori_error


def test_describe_voice_uses_narrator_reference(tmp_path, monkeypatch):
    from gm_server import tts as tts_mod

    monkeypatch.setattr(tts_mod.config, "IRODORI_NARRATOR_VOICE", "test_ref")
    tts = make(tmp_path, FakePost())
    assert tts.describe_voice() == f"参照音声 test_ref・種 {tts_mod.config.IRODORI_SEED}"
