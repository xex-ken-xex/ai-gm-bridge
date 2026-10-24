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


def test_mask_secrets_hides_openai_anthropic_and_config_keys():
    openai = "sk-proj-" + "a1B2" * 8
    anthropic = "sk-ant-api03-" + "Z9_y" * 8
    text = f'OPENAI {openai} / {anthropic} / x-api-key: {anthropic} / {{"api_key": "lm-local-secret", "model": "qwen"}} / api_key = "toml-secret"'
    masked = logs.mask_secrets(text, secrets=[])
    for value in (openai, anthropic, "lm-local-secret", "toml-secret"):
        assert value not in masked
    assert '"model": "qwen"' in masked
    # 「sk-」で始まるだけの短い語は伏せない
    assert logs.mask_secrets("task-list sk-short", secrets=[]) == "task-list sk-short"


def test_register_secret_masks_values_from_config(monkeypatch):
    monkeypatch.setattr(logs, "_extra_secrets", set())
    logs.register_secret("lmstudio-token-777")
    logs.register_secret("short")
    assert logs.mask_secrets("token lmstudio-token-777 short") == "token *** short"


def test_mask_log_file_and_mask_agy_logs(tmp_path):
    key = "AIza" + "k" * 35
    day = tmp_path / "2026-10-24"
    day.mkdir()
    agy = day / "agy-120000-0001.log"
    agy.write_text(f"request header x-goog-api-key: {key}\nok\n", encoding="utf-8")
    clean = day / "agy-120001-0002.log"
    clean.write_text("nothing secret\n", encoding="utf-8")
    other = day / "notes.log"
    other.write_text(key, encoding="utf-8")
    assert logs.mask_agy_logs(str(tmp_path)) == 1
    assert key not in agy.read_text(encoding="utf-8") and "ok" in agy.read_text(encoding="utf-8")
    assert clean.read_text(encoding="utf-8") == "nothing secret\n"
    # agy のログ以外は触らない (サーバーのログは書くときに伏せている)
    assert other.read_text(encoding="utf-8") == key
    assert logs.mask_log_file(str(tmp_path / "missing.log")) is False
    assert logs.mask_log_file(None) is False


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


def test_console_stream_erases_rest_of_line_on_a_terminal():
    """日本語は 1 文字 2 桁なので、空白の上書きでは前の行の文字が残る。端末では行末まで消す"""
    class Tty(io.StringIO):
        def isatty(self):
            return True

    raw = Tty()
    out = logs.ConsoleStream(raw, now=lambda: datetime.datetime(2026, 10, 18, 0, 0, 0))
    out.write("\r[集落] 経過 3秒 / タイムアウトまで残り 297秒")
    out.flush()
    out.write("\r[集落] 終了 (3.0秒)          ")
    out.flush()
    out.write("\n")
    assert raw.getvalue().endswith("\r2026-10-18 00:00:00 INFO    [集落] 終了 (3.0秒)" + logs.ERASE_EOL + "\n")


def test_clip_to_width_counts_wide_characters_as_two():
    assert logs.display_width("abc区画") == 7
    assert logs.clip_to_width("abc区画", 7) == "abc区画"
    clipped = logs.clip_to_width("区画 1・2・3 の描写を作成中", 10)
    assert logs.display_width(clipped) <= 10 and clipped.endswith("…")


def test_live_line_is_clipped_to_terminal_width(monkeypatch):
    import io
    import os

    class Tty(io.StringIO):
        def isatty(self):
            return True

    raw = Tty()
    monkeypatch.setattr(logs.shutil, "get_terminal_size", lambda fallback=None: os.terminal_size((40, 20)))
    out = logs.ConsoleStream(raw, now=lambda: datetime.datetime(2026, 10, 17, 9, 0, 0))
    out.write("\r[旅路 2/2 地点の描写] 地点 1・2・3・4・5 の描写を作成中 / 経過 0秒 / タイムアウトまで残り 300秒")
    out.flush()
    shown = raw.getvalue().split("\r")[-1].replace(logs.ERASE_EOL, "")
    assert logs.display_width(shown) <= 39
