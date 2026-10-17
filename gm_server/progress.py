"""時間のかかる処理の経過表示 (サーバーのコンソールで同じ行を上書き)"""

import asyncio
import contextlib
import sys
import time
from typing import Callable, Optional, TextIO


def format_progress(label: str, elapsed: float, limit: Optional[float] = None) -> str:
    """`[label] 経過 12秒 / 残り 288秒`。残りは処理の見込みではなくタイムアウトまでの時間"""
    text = f"[{label}] 経過 {int(elapsed)}秒"
    if limit is not None:
        text += f" / タイムアウトまで残り {max(0, int(limit - elapsed))}秒"
    return text


@contextlib.asynccontextmanager
async def progress(
    label: str,
    limit: Optional[float] = None,
    stream: Optional[TextIO] = None,
    interval: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
):
    """
    with ブロックの間、経過時間 (と limit までの残り時間) を同じ行に上書き表示する。
    端末以外 (ログファイルへのリダイレクト等) では上書きせず、終了時に 1 行だけ出す。
    """
    out = stream or sys.stdout
    live = hasattr(out, "isatty") and out.isatty()
    start = clock()
    last_len = 0

    def draw(text: str) -> None:
        nonlocal last_len
        # 前回より短いと文字が残るので空白で消す
        out.write("\r" + text + " " * max(0, last_len - len(text)))
        out.flush()
        last_len = len(text)

    async def tick() -> None:
        while True:
            draw(format_progress(label, clock() - start, limit))
            await asyncio.sleep(interval)

    ticker = asyncio.create_task(tick()) if live else None
    try:
        yield
    finally:
        if ticker:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
        final = f"[{label}] 完了 ({clock() - start:.1f}秒)"
        if live:
            draw(final)
            out.write("\n")
        else:
            out.write(final + "\n")
        out.flush()
