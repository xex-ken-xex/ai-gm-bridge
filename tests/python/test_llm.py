import pytest

from gm_server.llm import LLMService, call_agy, is_retryable_error


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def make_agy(responses):
    calls = []

    async def runner(prompt, conversation_id, timeout=None):
        calls.append((prompt, conversation_id))
        return responses.pop(0) if responses else ("", conversation_id or "")

    runner.calls = calls
    return runner


async def no_sleep(_):
    return None


def test_is_retryable_error():
    assert is_retryable_error(Exception("503 UNAVAILABLE"))
    assert is_retryable_error(Exception("429 RESOURCE_EXHAUSTED"))
    assert not is_retryable_error(Exception("400 INVALID_ARGUMENT"))


async def test_gemini_retries_on_503_then_succeeds():
    client = FakeClient([Exception("503 overloaded"), '{"narrative": "ok", "speech": "s", "dice": null}'])
    waits = []

    async def record_sleep(sec):
        waits.append(sec)

    llm = LLMService(gemini_client=client, agy_runner=make_agy([]), sleep=record_sleep)
    result = await llm.query_ai_gm("扉を開ける")
    assert result["narrative"] == "ok"
    assert client.models.calls == 2
    assert waits == [2]


async def test_gemini_non_retryable_error_falls_back_to_agy():
    client = FakeClient([Exception("400 bad request")])
    agy = make_agy([('{"narrative": "agy応答", "speech": "x", "dice": null}', "conv-1")])
    llm = LLMService(gemini_client=client, agy_runner=agy, sleep=no_sleep)
    result = await llm.query_ai_gm("扉を開ける")
    assert result["narrative"] == "agy応答"
    assert llm.conversation_id == "conv-1"
    assert client.models.calls == 1


async def test_gemini_invalid_json_falls_back_to_agy():
    client = FakeClient(["これはJSONではない"])
    agy = make_agy([('{"narrative": "agy", "speech": "", "dice": null}', "c")])
    llm = LLMService(gemini_client=client, agy_runner=agy, sleep=no_sleep)
    assert (await llm.query_ai_gm("x"))["narrative"] == "agy"


async def test_agy_plain_text_is_wrapped():
    agy = make_agy([("通路の奥で水音がする。気をつけて！\n次へ", "c")])
    llm = LLMService(gemini_client=None, agy_runner=agy)
    result = await llm.query_ai_gm("進む")
    assert result == {"narrative": "通路の奥で水音がする。気をつけて！<br>次へ", "speech": "通路の奥で水音がする", "dice": None}


async def test_query_json_prefers_gemini_and_falls_back():
    llm = LLMService(gemini_client=FakeClient(['[{"id": 0}]']), agy_runner=make_agy([]), sleep=no_sleep)
    assert await llm.query_json("p") == [{"id": 0}]

    agy = make_agy([('```json\n{"a": 1}\n```', "")])
    llm = LLMService(gemini_client=FakeClient([Exception("500 boom")]), agy_runner=agy, sleep=no_sleep)
    assert await llm.query_json("p") == {"a": 1}


async def test_ask_support_gm_adds_persona_only_on_first_turn():
    agy = make_agy([("回答1", "conv-9"), ("回答2", "conv-9")])
    llm = LLMService(agy_runner=agy)
    assert await llm.ask_support_gm("相談1") == "回答1"
    assert await llm.ask_support_gm("相談2") == "回答2"
    first_prompt, first_conv = agy.calls[0]
    second_prompt, second_conv = agy.calls[1]
    assert "サポートGM" in first_prompt and first_conv is None
    assert second_prompt == "相談2" and second_conv == "conv-9"


async def test_call_agy_missing_binary(monkeypatch):
    async def raise_missing(*args, **kwargs):
        raise FileNotFoundError("agy")

    monkeypatch.setattr("asyncio.create_subprocess_exec", raise_missing)
    assert await call_agy("hi", "conv") == ("", "conv")


async def test_call_agy_times_out_and_kills_process(monkeypatch):
    import asyncio
    import sys

    real_exec = asyncio.create_subprocess_exec
    procs = []

    async def slow_agy(*args, **kwargs):
        proc = await real_exec(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", slow_agy)
    assert await call_agy("hi", "conv", timeout=0.2) == ("", "conv")
    assert procs[0].returncode is not None


async def test_gemini_timeout_falls_back_to_agy():
    import time

    class SlowClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                time.sleep(0.5)

    agy = make_agy([('{"ok": true}', "c")])
    llm = LLMService(gemini_client=SlowClient(), agy_runner=agy, sleep=no_sleep, gemini_timeout=0.05)
    assert await llm.query_json("x") == {"ok": True}


def test_build_agy_command_adds_model_and_effort_only_when_set():
    from gm_server.llm import build_agy_command

    assert build_agy_command("hi", None, model="", effort="") == ["agy", "-p", "hi", "--output-format", "json"]
    assert build_agy_command("hi", "c1", model="gemini-x", effort="low") == [
        "agy", "-p", "hi", "--output-format", "json", "--model", "gemini-x", "--effort", "low", "--conversation", "c1",
    ]


def test_concurrency_depends_on_engine(monkeypatch):
    from gm_server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "AGY_CONCURRENCY", 1)
    monkeypatch.setattr(llm_mod, "LLM_CONCURRENCY", 3)
    assert LLMService(gemini_client=None).concurrency == 1
    assert LLMService(gemini_client=object()).concurrency == 3
