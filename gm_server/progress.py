"""時間のかかる処理の経過表示 (サーバーのコンソールで同じ行を上書き)"""

import asyncio
import contextlib
import sys
import time
from typing import Awaitable, Callable, Optional, TextIO, Union


# 表示中の progress の数。並列ジョブの内側の progress は外側の 1 行に任せて表示しない
_active = 0
# 同じ行を上書き表示中か (その間のログは改行してから出す)
_live_line = False


def log(message: str) -> None:
    """progress の上書き行を壊さずにログを 1 行出す"""
    print(("\n" if _live_line else "") + message)


# ログの 3 段のグループ分け: ■ 全体の処理 → ▶ 工程 → ・細かい作業
def log_group(message: str) -> None:
    """全体の処理の始まり・終わり (例: ■ 遺跡『塔』のシーン構築を始めます)"""
    log(f"■ {message}")


def log_step(message: str) -> None:
    """全体の中の工程 (例: ▶ [2/2 区画の情景] AI に問い合わせます)"""
    log(f"  ▶ {message}")


def log_detail(message: str) -> None:
    """工程の中の細かい作業 (例: ・区画 3・5・8 の情景を作成中)"""
    log(f"    ・{message}")


def format_progress(label: str, elapsed: float, limit: Optional[float] = None, detail: str = "") -> str:
    """`[label] 完了 1/3 / 経過 12秒 / タイムアウトまで残り 288秒`。残りは処理の見込みではなくタイムアウトまでの時間"""
    text = f"[{label}] " + (f"{detail} / " if detail else "") + f"経過 {int(elapsed)}秒"
    if limit is not None:
        text += f" / タイムアウトまで残り {max(0, int(limit - elapsed))}秒"
    return text


@contextlib.asynccontextmanager
async def progress(
    label: str,
    limit: Union[None, float, Callable[[], float]] = None,
    stream: Optional[TextIO] = None,
    interval: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    detail: Optional[Callable[[], str]] = None,
    notify: Optional[Callable[[str], Awaitable[None]]] = None,
    notify_interval: float = 30.0,
):
    """
    with ブロックの間、経過時間 (と limit までの残り時間) を同じ行に上書き表示する。
    detail を渡すと「完了 2/3」のような進捗を行頭に添える。
    notify を渡すと notify_interval 秒ごとに同じ文面で呼ぶ (FVTT への進捗送信用)。
    端末以外 (ログファイルへのリダイレクト等) では上書きせず、終了時に 1 行だけ出す。
    既に別の progress が表示中のとき (並列ジョブの内側) は何も表示しない。
    """
    global _active, _live_line
    if _active:
        yield
        return
    out = stream or sys.stdout
    # limit は延長に追従できるよう関数でも受け取る
    current_limit = limit if callable(limit) else (lambda: limit)
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
            draw(format_progress(label, clock() - start, current_limit(), detail() if detail else ""))
            await asyncio.sleep(interval)

    async def notify_loop() -> None:
        while True:
            await asyncio.sleep(notify_interval)
            try:
                await notify(format_progress(label, clock() - start, current_limit(), detail() if detail else ""))
            except Exception as e:
                # 送信に失敗しても本処理は止めない
                log(f"進捗の送信に失敗しました ({e})")

    ticker = asyncio.create_task(tick()) if live else None
    notifier = asyncio.create_task(notify_loop()) if notify else None
    _active += 1
    _live_line = live
    try:
        yield
    finally:
        _active -= 1
        _live_line = False
        for task in (ticker, notifier):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        final = f"[{label}] " + (f"{detail()} / " if detail else "") + f"終了 ({clock() - start:.1f}秒)"
        if live:
            draw(final)
            out.write("\n")
        else:
            out.write(final + "\n")
        out.flush()
