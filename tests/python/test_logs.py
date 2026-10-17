import datetime
import io
import logging
import os

import pytest

from gm_server import logs


@pytest.fixture
def file_log(tmp_path):
    """ログファイルに書く handler を付け、終わったら外す"""
    today = [datetime.date(2026, 10, 17)]
    handler = logs.DailyFolderHandler(str(tmp_path), keep_days=10, today=lambda: today[0])
    handler.setFormatter(logs.make_formatter())
    logs.logger.addHandler(handler)
    yield tmp_path, today, handler
    logs.logger.removeHandler(handler)
    handler.close()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_mask_secrets_hides_api_keys():
    key = "AIza" + "x" * 35
    text = f"key={key} GEMINI {key} https://a/?key=abc&x=1 x-goog-api-key: secret123 Authorization: Bearer tok999"
    masked = logs.mask_secrets(text, secrets=[])
    assert key not in masked and "abc" not in masked and "secret123" not in masked and "tok999" not in masked
    assert logs.mask_secrets("値 my-secret-value", secrets=["my-secret-value"]) == "値 ***"


def test_secret_values_reads_key_like_env(monkeypatch):
    monkeypatch.setattr(logs.config, "API_KEY", "gemini-key-123456")
    values = logs.secret_values({"OPENAI_API_KEY": "sk-abcdefgh", "GITHUB_TOKEN": "short", "HOME": "/home/kenic"})
    assert "gemini-key-123456" in values and "sk-abcdefgh" in values
    assert "short" not in values and "/home/kenic" not in values


def test_daily_folder_handler_writes_dated_file_and_prunes(file_log):
    tmp_path, today, _ = file_log
    for name in ("2026-10-07", "2026-10-08", "2026-10-16", "not-a-date"):
        os.makedirs(tmp_path / name)
    logs.logger.info("一行目")
    path = tmp_path / "2026-10-17" / "server.log"
    line = read(path).splitlines()[0]
    assert line.endswith("INFO    一行目") and line.startswith("2026-")
    # 今日を含めて 10 日 (10/08〜10/17) より古いフォルダだけ消える。日付でないフォルダは触らない
    assert sorted(os.listdir(tmp_path)) == ["2026-10-08", "2026-10-16", "2026-10-17", "not-a-date"]
    today[0] = datetime.date(2026, 10, 18)
    logs.logger.warning("日付が変わった")
    assert "WARNING 日付が変わった" in read(tmp_path / "2026-10-18" / "server.log")
    assert "2026-10-08" not in os.listdir(tmp_path)


def test_ai_request_and_response_are_logged_with_secrets_masked(file_log, monkeypatch):
    tmp_path, _, _ = file_log
    monkeypatch.setattr(logs.config, "API_KEY", "super-secret-key-999")
    rid = logs.ai_request("Gemini", "部屋を描写して super-secret-key-999", model="m")
    logs.ai_response(rid, "Gemini", '{"ok": true}', 1.25)
    logs.ai_error(rid, "Gemini", "503", 2.0)
    text = read(tmp_path / "2026-10-17" / "server.log")
    assert f"[AI #{rid}] → Gemini 問い合わせ" in text and "部屋を描写して ***" in text
    assert f"[AI #{rid}] ← Gemini 応答 (1.2秒" in text and '{"ok": true}' in text
    assert "WARNING [AI" in text and "super-secret-key-999" not in text


def test_console_stream_prefixes_time_and_level(file_log):
    tmp_path, _, _ = file_log
    raw = io.StringIO()
    now = datetime.datetime(2026, 10, 17, 9, 5, 3)
    out = logs.ConsoleStream(raw, now=lambda: now)
    print("サーバー起動", file=out)
    print("\n生成に失敗しました", file=out)
    lines = raw.getvalue().split("\n")
    assert lines[0] == "2026-10-17 09:05:03 INFO    サーバー起動"
    assert lines[1] == ""
    assert lines[2] == "2026-10-17 09:05:03 WARNING 生成に失敗しました"
    text = read(tmp_path / "2026-10-17" / "server.log")
    assert "INFO    サーバー起動" in text and "WARNING 生成に失敗しました" in text


def test_console_stream_redraws_progress_line_and_logs_only_the_final(file_log):
    tmp_path, _, _ = file_log
    raw = io.StringIO()
    out = logs.ConsoleStream(raw, now=lambda: datetime.datetime(2026, 10, 17, 9, 0, 0))
    out.write("\r[生成] 経過 1秒")
    out.flush()
    out.write("\r[生成] 経過 2秒")
    out.flush()
    out.write("\r[生成] 終了 (2.0秒)")
    out.flush()
    out.write("\n")
    assert raw.getvalue().count("\r2026-10-17 09:00:00 INFO    [生成] 経過") == 2
    assert raw.getvalue().endswith("\r2026-10-17 09:00:00 INFO    [生成] 終了 (2.0秒)\n")
    text = read(tmp_path / "2026-10-17" / "server.log")
    assert "終了 (2.0秒)" in text and "経過 1秒" not in text


def test_stderr_lines_are_errors():
    raw = io.StringIO()
    out = logs.ConsoleStream(raw, default_level=logging.ERROR)
    out.write("  File \"x.py\", line 1\n")
    assert " ERROR   " in raw.getvalue()


def test_clip_for_log():
    assert logs.clip_for_log("abc", 5) == "abc"
    assert logs.clip_for_log("a" * 10, 4) == "aaaa…(全 10 文字)"
