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


@pytest.fixture
def file_log(tmp_path):
    import datetime

    from gm_server import logs

    handler = logs.DailyFolderHandler(str(tmp_path), today=lambda: datetime.date(2026, 10, 18))
    handler.setFormatter(logs.make_formatter())
    logs.logger.addHandler(handler)
    yield tmp_path / "2026-10-18"
    logs.logger.removeHandler(handler)
    handler.close()


def fake_agy(monkeypatch, code):
    """agy の代わりに python を動かす。渡された引数と stdin を記録する"""
    import asyncio
    import sys

    real_exec = asyncio.create_subprocess_exec
    calls = []

    async def run(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_exec(sys.executable, "-c", code, **kwargs)

    monkeypatch.setattr("asyncio.create_subprocess_exec", run)
    return calls


async def test_call_agy_logs_stderr_lines_and_passes_log_file(monkeypatch, file_log, tmp_path):
    import asyncio

    from gm_server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "AGY_WORKDIR", str(tmp_path / "work"))

    code = ("import sys; sys.stderr.write('step 1\\nstep 2\\n'); "
            "print('{\"response\": \"ok\", \"conversation_id\": \"c9\"}')")
    calls = fake_agy(monkeypatch, code)
    assert await call_agy("hi", None) == ("ok", "c9")
    args, kwargs = calls[0]
    # agy が入力待ちでサーバーの端末を読まないよう stdin はつながない
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    # agy がモジュールのソースを読み回らないよう、空の作業フォルダで動かす
    assert kwargs["cwd"] == str(tmp_path / "work") and (tmp_path / "work").is_dir()
    log_file = args[args.index("--log-file") + 1]
    assert log_file.startswith(str(file_log)) and "agy-" in log_file
    text = (file_log / "server.log").read_text(encoding="utf-8")
    assert "agy stderr: step 1" in text and "agy stderr: step 2" in text
    assert '← agy 応答' in text and "exit_code=0" in text


async def test_call_agy_timeout_keeps_partial_output_and_process_state(monkeypatch, file_log, tmp_path):
    from gm_server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "AGY_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(llm_mod, "AGY_WATCH_INTERVAL", 0.1)
    code = ("import sys, time; sys.stdout.write('partial-out'); sys.stdout.flush(); "
            "sys.stderr.write('thinking...\\n'); sys.stderr.flush(); time.sleep(30)")
    fake_agy(monkeypatch, code)
    assert await call_agy("hi", "conv", timeout=0.5) == ("", "conv")
    text = (file_log / "server.log").read_text(encoding="utf-8")
    assert "秒以内に応答なし" in text and "agy の動作ログ:" in text
    assert "それまでの標準出力 (11B):\npartial-out" in text
    assert "thinking..." in text
    assert "agy 実行中" in text and "状態=" in text


def test_process_snapshot_reads_own_process():
    import os

    from gm_server.llm import process_snapshot

    snap = process_snapshot(os.getpid())
    assert f"pid={os.getpid()}" in snap and "CPU=" in snap
    assert process_snapshot(999999999) == "(プロセス情報なし)"


async def test_call_agy_takes_the_answer_even_if_agy_does_not_exit(monkeypatch, file_log):
    """agy は結果を出したあと終了せずに残ることがある。結果がそろったら受け取って止める"""
    import time

    from gm_server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "AGY_EXIT_GRACE", 0.2)
    code = ("import sys, time; sys.stdout.write('{\"response\": \"done\",\\n \"conversation_id\": \"c1\"}\\n'); "
            "sys.stdout.flush(); time.sleep(30)")
    fake_agy(monkeypatch, code)
    started = time.monotonic()
    assert await call_agy("hi", None, timeout=20) == ("done", "c1")
    assert time.monotonic() - started < 5
    text = (file_log / "server.log").read_text(encoding="utf-8")
    assert "終了しないため止めました" in text and "← agy 応答" in text


def test_complete_agy_output():
    from gm_server.llm import complete_agy_output

    assert complete_agy_output(b'{"response": "x"}\n')
    assert not complete_agy_output(b'{"response": "x"')
    assert not complete_agy_output(b'[1, 2]') and not complete_agy_output(b"")


async def test_call_agy_masks_its_own_log_file(monkeypatch, file_log, tmp_path):
    """agy の動作ログはサーバーを通らないので、終わったあとで API キーを伏せる"""
    import asyncio
    import sys

    from gm_server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "AGY_WORKDIR", str(tmp_path / "work"))
    key = "AIza" + "q" * 35
    real_exec = asyncio.create_subprocess_exec
    written = []

    async def run(*args, **kwargs):
        path = args[args.index("--log-file") + 1]
        written.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"POST generateContent?key={key}\n")
        return await real_exec(sys.executable, "-c", "print('{\"response\": \"ok\"}')", **kwargs)

    monkeypatch.setattr("asyncio.create_subprocess_exec", run)
    assert (await call_agy("hi", None))[0] == "ok"
    text = open(written[0], encoding="utf-8").read()
    assert key not in text and "key=***" in text
