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
    assert text.startswith("[agy] 完了 (") and text.count("\n") == 1


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
