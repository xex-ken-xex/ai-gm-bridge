import asyncio
import io

from gm_server.progress import format_progress, progress


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_format_progress():
    assert format_progress("agy", 12.7, 300) == "[agy] 経過 12秒 / タイムアウトまで残り 287秒"
    assert format_progress("agy", 400, 300) == "[agy] 経過 400秒 / タイムアウトまで残り 0秒"
    assert format_progress("Gemini", 3.2) == "[Gemini] 経過 3秒"
    assert format_progress("部屋", 5, 60, "完了 1/3") == "[部屋] 完了 1/3 / 経過 5秒 / タイムアウトまで残り 55秒"


async def test_progress_overwrites_same_line_on_tty():
    out = FakeTTY()
    async with progress("agy", limit=10, stream=out, interval=0.01):
        await asyncio.sleep(0.05)
    text = out.getvalue()
    assert text.count("\r") >= 2
    assert "残り" in text
    assert text.rstrip(" \n").endswith("秒)") and text.endswith("\n")
    assert text.count("\n") == 1


async def test_progress_prints_one_line_when_not_tty():
    out = io.StringIO()
    async with progress("agy", limit=10, stream=out, interval=0.01):
        await asyncio.sleep(0.03)
    text = out.getvalue()
    assert "\r" not in text
    assert text.startswith("[agy] 終了 (") and text.count("\n") == 1


async def test_progress_stops_ticker_on_error():
    out = FakeTTY()
    try:
        async with progress("x", stream=out, interval=0.01):
            raise ValueError("boom")
    except ValueError:
        pass
    before = out.getvalue()
    await asyncio.sleep(0.03)
    assert out.getvalue() == before


async def test_nested_progress_is_silent_and_detail_is_shown():
    out = FakeTTY()
    inner = FakeTTY()
    state = {"done": 0}
    async with progress("部屋", limit=10, stream=out, interval=0.01, detail=lambda: f"完了 {state['done']}/2"):
        async with progress("agy", limit=5, stream=inner, interval=0.01):
            await asyncio.sleep(0.03)
        state["done"] = 2
        await asyncio.sleep(0.02)
    assert inner.getvalue() == ""
    assert "完了 0/2" in out.getvalue()
    assert "完了 2/2 / 終了" in out.getvalue()


async def test_progress_notifies_periodically():
    sent = []

    async def notify(text):
        sent.append(text)

    async with progress("部屋", limit=60, stream=io.StringIO(), notify=notify, notify_interval=0.02,
                        detail=lambda: "完了 1/3"):
        await asyncio.sleep(0.07)
    assert len(sent) >= 2
    assert sent[0].startswith("[部屋] 完了 1/3 / 経過")
    count = len(sent)
    await asyncio.sleep(0.05)
    assert len(sent) == count  # 終了後は送らない


async def test_progress_notify_failure_does_not_break(capsys):
    async def notify(text):
        raise RuntimeError("closed")

    async with progress("x", stream=io.StringIO(), notify=notify, notify_interval=0.01):
        await asyncio.sleep(0.03)
    assert "進捗の送信に失敗" in capsys.readouterr().out
