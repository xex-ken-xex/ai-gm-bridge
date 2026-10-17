import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gm_server.context import Context, SessionState  # noqa: E402
from gm_server.llm import LLMService  # noqa: E402
from gm_server.tts import SpeechSynthesizer  # noqa: E402


class FakeCommunicate:
    """edge_tts.Communicate の代わりに空の mp3 を書き出す"""

    calls = []

    def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
        self.text, self.voice, self.rate, self.pitch = text, voice, rate, pitch
        FakeCommunicate.calls.append(self)

    async def save(self, path):
        with open(path, "wb") as f:
            f.write(b"ID3")


class FakeLLM:
    """LLMService の代わり。応答をキューで与える"""

    def __init__(self, gm_responses=None, json_responses=None, support_responses=None):
        self.gm_responses = list(gm_responses or [])
        self.json_responses = list(json_responses or [])
        self.support_responses = list(support_responses or [])
        self.prompts = []

    async def query_ai_gm(self, player_input):
        self.prompts.append(("gm", player_input))
        return self.gm_responses.pop(0) if self.gm_responses else {"narrative": "", "speech": "", "dice": None}

    async def query_json(self, prompt, unbounded=False):
        self.prompts.append(("json", prompt))
        return self.json_responses.pop(0) if self.json_responses else None

    async def ask_support_gm(self, prompt):
        self.prompts.append(("support", prompt))
        return self.support_responses.pop(0) if self.support_responses else ""


async def _no_sleep(_seconds):
    return None


@pytest.fixture
def sent():
    return []


@pytest.fixture
def make_ctx(tmp_path, sent):
    def _make(llm=None):
        async def send_json(msg):
            sent.append(msg)

        FakeCommunicate.calls = []
        tts = SpeechSynthesizer(data_dir=str(tmp_path), communicate_factory=FakeCommunicate, settle_delay=0, engine="edge")
        return Context(
            send_json=send_json,
            llm=llm or FakeLLM(),
            tts=tts,
            state=SessionState(),
            data_dir=str(tmp_path),
            sleep=_no_sleep,
        )

    return _make


@pytest.fixture(autouse=True)
def agy_workdir(tmp_path, monkeypatch):
    """テストで ~/ai-gm-logs に agy の作業フォルダを作らない"""
    from gm_server import llm

    monkeypatch.setattr(llm, "AGY_WORKDIR", str(tmp_path / "agy-workspace"))
