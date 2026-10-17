import asyncio
import os

import pytest

from gm_server import tts as tts_mod
from gm_server.tts import IrodoriClient, SpeechSynthesizer, default_caption, rate_to_speed
from tests.python.conftest import FakeCommunicate


def run(coro):
    return asyncio.run(coro)


class FakePost:
    """IrodoriClient の HTTP 送信の代わり。fail=True なら例外"""

    def __init__(self, fail=False, audio=b"MP3DATA"):
        self.fail, self.audio, self.calls = fail, audio, []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        if self.fail:
            raise ConnectionRefusedError("接続できません")
        return self.audio


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, post, clock=None, engine="irodori", **client_kw):
    FakeCommunicate.calls = []
    return SpeechSynthesizer(
        data_dir=str(tmp_path), communicate_factory=FakeCommunicate, settle_delay=0, engine=engine,
        irodori=IrodoriClient(url="http://irodori:8088/", post=post, **client_kw),
        retry_after=60, clock=clock or Clock(),
    )


def read(tmp_path, name):
    with open(os.path.join(tmp_path, name), "rb") as f:
        return f.read()


def test_rate_to_speed():
    assert rate_to_speed("+0%") == 1.0
    assert rate_to_speed("+10%") == 1.1
    assert rate_to_speed("-15%") == 0.85
    assert rate_to_speed("-90%") == 0.25
    assert rate_to_speed("+500%") == 4.0
    assert rate_to_speed("おかしな値") == 1.0


def test_default_caption_follows_voice_and_pitch():
    assert default_caption("ja-JP-KeitaNeural") == tts_mod.config.IRODORI_CAPTION_MALE
    assert default_caption("ja-JP-NanamiNeural") == tts_mod.config.IRODORI_CAPTION_FEMALE
    assert default_caption("ja-JP-KeitaNeural", "+12Hz").endswith("やや高めの声。")
    assert default_caption("ja-JP-NanamiNeural", "-10Hz").endswith("やや低めの声。")
    assert default_caption(None, "+5Hz") == tts_mod.config.IRODORI_CAPTION_FEMALE


def test_irodori_writes_mp3_with_caption_and_speed(tmp_path):
    post = FakePost()
    synth = make(tmp_path, post, api_key="secret")
    assert run(synth.synthesize(" こんにちは。 ", "v.mp3", voice="ja-JP-KeitaNeural", rate="+20%", caption="しわがれた老人の声"))
    assert read(tmp_path, "v.mp3") == b"MP3DATA"
    assert FakeCommunicate.calls == []
    url, body, headers, _ = post.calls[0]
    assert url == "http://irodori:8088/v1/audio/speech"
    assert body == {
        "model": "irodori-tts", "input": "こんにちは。", "voice": "none", "response_format": "mp3",
        "speed": 1.2, "irodori": {"caption": "しわがれた老人の声"},
    }
    assert headers == {"Authorization": "Bearer secret"}


def test_irodori_uses_default_caption_without_one(tmp_path):
    post = FakePost()
    synth = make(tmp_path, post)
    assert run(synth.synthesize("やあ", "v.mp3", voice="ja-JP-KeitaNeural", pitch="-15Hz"))
    _, body, headers, _ = post.calls[0]
    assert body["irodori"]["caption"] == tts_mod.config.IRODORI_CAPTION_MALE + "やや低めの声。"
    assert headers == {}


def test_irodori_failure_falls_back_to_edge_and_pauses(tmp_path):
    post, clock = FakePost(fail=True), Clock()
    synth = make(tmp_path, post, clock=clock)
    assert run(synth.synthesize("一回目", "a.mp3", voice="ja-JP-KeitaNeural", rate="+10%"))
    assert len(post.calls) == 1
    assert [(c.text, c.voice, c.rate) for c in FakeCommunicate.calls] == [("一回目", "ja-JP-KeitaNeural", "+10%")]
    assert read(tmp_path, "a.mp3") == b"ID3"

    # 休止中は Irodori に問い合わせない
    clock.now += 30
    assert run(synth.synthesize("二回目", "b.mp3"))
    assert len(post.calls) == 1

    # 休止が明けたらもう一度 Irodori を試す
    post.fail = False
    clock.now += 31
    assert run(synth.synthesize("三回目", "c.mp3"))
    assert len(post.calls) == 2
    assert read(tmp_path, "c.mp3") == b"MP3DATA"


def test_empty_audio_counts_as_failure(tmp_path):
    synth = make(tmp_path, FakePost(audio=b""))
    assert run(synth.synthesize("やあ", "v.mp3"))
    assert read(tmp_path, "v.mp3") == b"ID3"


def test_edge_engine_never_calls_irodori(tmp_path):
    post = FakePost()
    synth = make(tmp_path, post, engine="edge")
    assert run(synth.synthesize("やあ", "v.mp3"))
    assert post.calls == []
    assert synth.engine_name.startswith("edge-tts")


def test_unknown_engine_falls_back_to_edge(tmp_path):
    synth = make(tmp_path, FakePost(), engine="voicevox")
    assert synth.engine == "edge"


def test_empty_text_is_skipped(tmp_path):
    post = FakePost()
    synth = make(tmp_path, post)
    assert not run(synth.synthesize("  ", "v.mp3"))
    assert post.calls == [] and FakeCommunicate.calls == []


class RecordingPost(FakePost):
    """失敗するかどうかを呼ぶたびに決める"""

    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        if not self.results.pop(0):
            raise TimeoutError("timed out")
        return self.audio


def test_only_first_call_waits_longer(tmp_path):
    post, clock = RecordingPost([False, True, True]), Clock()
    synth = make(tmp_path, post, clock=clock, timeout=20, first_timeout=180)
    run(synth.synthesize("一", "a.mp3"))
    clock.now += 61
    run(synth.synthesize("二", "b.mp3"))
    run(synth.synthesize("三", "c.mp3"))
    # 1 回目が失敗しても、固まったサーバーで何度も長く待たないよう 2 回目からは短い待ち上限
    assert [c[3] for c in post.calls] == [180, 20, 20]


def test_first_timeout_is_never_shorter_than_timeout(tmp_path):
    client = IrodoriClient(timeout=30, first_timeout=5, post=FakePost())
    assert client.first_timeout == 30


def test_gm_is_notified_once_per_fallback(tmp_path):
    post, clock = RecordingPost([False, False, True, False]), Clock()
    synth = make(tmp_path, post, clock=clock)
    notices = []

    async def notify(message):
        notices.append(message)

    synth.notify = notify
    for name in ("a", "b", "c", "d"):
        run(synth.synthesize("やあ", f"{name}.mp3"))
        clock.now += 61
    # 失敗が続いても 1 回、Irodori が戻ったあとにまた失敗したらもう 1 回
    assert len(notices) == 2
    assert "edge-tts" in notices[0]


def test_notify_error_does_not_break_synthesis(tmp_path):
    synth = make(tmp_path, FakePost(fail=True))

    async def notify(message):
        raise RuntimeError("未接続")

    synth.notify = notify
    assert run(synth.synthesize("やあ", "v.mp3"))


def test_check_irodori(tmp_path):
    calls = []

    def ok_get(url, headers, timeout):
        calls.append((url, timeout))
        return b"{}"

    def bad_get(url, headers, timeout):
        raise ConnectionRefusedError("Connection refused")

    synth = make(tmp_path, FakePost(), get=ok_get, health_timeout=5)
    assert run(synth.check_irodori()) == ""
    assert calls == [("http://irodori:8088/health", 5)]
    assert "refused" in run(make(tmp_path, FakePost(), get=bad_get).check_irodori())
    assert run(make(tmp_path, FakePost(), engine="edge").check_irodori()) is None
