"""
タイムアウト付きの待ち処理。上限を過ぎたら FVTT の GM に延長するか確認する

- 待っている間は progress() で経過時間とタイムアウトまでの残り時間を表示し、30 秒ごとに FVTT へも送る
- 上限を過ぎると「延長する / 打ち切る」のカードを GM に出す (処理自体はその間も続ける)
- GM の回答 (`timeout_extend:<id>` / `timeout_abort:<id>`) は router が受信キューを待たずに処理する
"""

import asyncio
import contextlib
import uuid
from typing import Awaitable, Callable, Optional

from gm_server import protocol
from gm_server.config import SPEAKER_SUPPORT
from gm_server.progress import log, progress

# 延長確認カードに回答が無いとき、打ち切るまで待つ秒数
EXTEND_ANSWER_WAIT = 120.0

# FVTT へ途中経過を送る間隔 (秒)
NOTIFY_INTERVAL = 30.0
# on_status (画面の進捗表示) を呼ぶ間隔 (秒)
STATUS_INTERVAL = 2.0

# on_status(経過秒, タイムアウトまでの残り秒, detail の文字列)
StatusCallback = Callable[[float, float, str], Awaitable[None]]

TIMEOUT_CHOICE_PREFIX = "timeout_"


def notifier(ctx) -> Callable[[str], Awaitable[None]]:
    """progress() の notify 用: 途中経過を FVTT の GM 画面へ送る"""
    async def _send(text: str) -> None:
        await ctx.send(protocol.progress(text))
    return _send


def extend_card(label: str, elapsed: float, extend_by: float, request_id: str) -> dict:
    return protocol.interactive_card(
        title="タイムアウト確認",
        message=f"「{label}」が {int(elapsed)} 秒たっても終わっていません。{int(extend_by)} 秒延長しますか？",
        choices=[
            {"label": f"延長する (+{int(extend_by)}秒)", "value": f"timeout_extend:{request_id}"},
            {"label": "打ち切る", "value": f"timeout_abort:{request_id}"},
        ],
    )


def is_timeout_choice(value: str) -> bool:
    return isinstance(value, str) and value.startswith(TIMEOUT_CHOICE_PREFIX)


def resolve_timeout_choice(ctx, value: str) -> bool:
    """延長確認カードの回答を、待っている run_with_deadline に渡す。渡せたら True"""
    kind, _, request_id = value.partition(":")
    fut = ctx.state.timeout_requests.get(request_id)
    if fut is None or fut.done():
        print(f"既に終わった延長確認への回答です: {value}")
        return False
    fut.set_result(kind == "timeout_extend")
    return True


async def _ask_extend(ctx, label: str, elapsed: float, extend_by: float, task: asyncio.Future,
                      answer_wait: float) -> bool:
    """延長するなら True。回答待ちの間に処理が終わったら True (呼び出し側が結果を使う)"""
    loop = asyncio.get_running_loop()
    request_id = uuid.uuid4().hex[:8]
    answer = loop.create_future()
    ctx.state.timeout_requests[request_id] = answer
    try:
        log(f"[{label}] タイムアウト。延長するか GM に確認中...")
        await ctx.send(extend_card(label, elapsed, extend_by, request_id))
        await asyncio.wait({answer, task}, timeout=answer_wait, return_when=asyncio.FIRST_COMPLETED)
        if answer.done():
            return answer.result()
        if task.done():
            return True
        await ctx.send(protocol.chat(
            f"⏱️ 延長の確認に {int(answer_wait)} 秒応答が無かったため「{label}」を打ち切りました。",
            speaker=SPEAKER_SUPPORT,
        ))
        return False
    finally:
        ctx.state.timeout_requests.pop(request_id, None)


async def run_with_deadline(
    ctx,
    label: str,
    awaitable: Awaitable,
    limit: float,
    detail: Optional[Callable[[], str]] = None,
    answer_wait: Optional[float] = None,
    notify_interval: Optional[float] = None,
    on_status: Optional[StatusCallback] = None,
    status_interval: Optional[float] = None,
):
    """
    awaitable を limit 秒まで待つ。過ぎたら GM に延長を確認し、延長なら limit 秒ずつ延ばす。
    打ち切りになったら処理をキャンセルして asyncio.TimeoutError を投げる。
    on_status を渡すと STATUS_INTERVAL 秒ごとに (経過, 残り, detail) で呼ぶ (シナリオビルダーの進捗表示用)。
    """
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(awaitable)
    start = loop.time()
    deadline = limit
    wait_for_answer = EXTEND_ANSWER_WAIT if answer_wait is None else answer_wait

    async def status_loop() -> None:
        while True:
            elapsed = loop.time() - start
            try:
                await on_status(elapsed, deadline - elapsed, detail() if detail else "")
            except Exception as e:
                # 進捗の表示に失敗しても本処理は止めない
                log(f"[{label}] 進捗の送信に失敗しました ({e})")
            await asyncio.sleep(STATUS_INTERVAL if status_interval is None else status_interval)

    status_task = asyncio.create_task(status_loop()) if on_status else None
    try:
        async with progress(label, limit=lambda: deadline, detail=detail, notify=notifier(ctx),
                            notify_interval=NOTIFY_INTERVAL if notify_interval is None else notify_interval):
            while True:
                remaining = deadline - (loop.time() - start)
                if remaining > 0:
                    await asyncio.wait({task}, timeout=remaining)
                if task.done():
                    return task.result()
                extend = await _ask_extend(ctx, label, loop.time() - start, limit, task, wait_for_answer)
                if task.done():
                    return task.result()
                if not extend:
                    raise asyncio.TimeoutError(f"{label} を打ち切りました")
                deadline = (loop.time() - start) + limit
                log(f"[{label}] {int(limit)} 秒延長しました")
    finally:
        if status_task:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
