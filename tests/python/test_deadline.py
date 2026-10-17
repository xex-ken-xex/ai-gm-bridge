import asyncio
import json

import pytest

from gm_server import deadline
from gm_server.app import serve_messages
from gm_server.router import is_immediate


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(deadline, "NOTIFY_INTERVAL", 30.0)


def answer_card(ctx, sent, choice_index):
    """最後に送られた延長確認カードの選択肢を押したことにする"""
    card = [m for m in sent if m.get("action") == "interactive_card"][-1]
    value = card["choices"][choice_index]["value"]
    assert deadline.resolve_timeout_choice(ctx, value)
    return value


async def test_returns_result_within_limit(make_ctx, sent):
    ctx = make_ctx()

    async def quick():
        return 42

    assert await deadline.run_with_deadline(ctx, "x", quick(), limit=1) == 42
    assert sent == []


async def test_extend_then_finish(make_ctx, sent):
    ctx = make_ctx()

    async def slow():
        await asyncio.sleep(0.15)
        return "ok"

    async def press_extend():
        while not ctx.state.timeout_requests:
            await asyncio.sleep(0.005)
        answer_card(ctx, sent, 0)

    presser = asyncio.create_task(press_extend())
    result = await deadline.run_with_deadline(ctx, "部屋の情景", slow(), limit=0.05, answer_wait=5)
    await presser
    assert result == "ok"
    card = sent[0]
    assert card["title"] == "タイムアウト確認"
    assert [c["value"].split(":")[0] for c in card["choices"]] == ["timeout_extend", "timeout_abort"]
    assert "部屋の情景" in card["message"]


async def test_abort_cancels_work(make_ctx, sent):
    ctx = make_ctx()
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def press_abort():
        while not ctx.state.timeout_requests:
            await asyncio.sleep(0.005)
        answer_card(ctx, sent, 1)

    presser = asyncio.create_task(press_abort())
    with pytest.raises(asyncio.TimeoutError):
        await deadline.run_with_deadline(ctx, "伝承", slow(), limit=0.02, answer_wait=5)
    await presser
    assert cancelled.is_set()
    assert ctx.state.timeout_requests == {}


async def test_work_finishing_while_asking_returns_result(make_ctx, sent):
    ctx = make_ctx()

    async def slow():
        await asyncio.sleep(0.08)
        return "done"

    assert await deadline.run_with_deadline(ctx, "x", slow(), limit=0.02, answer_wait=5) == "done"
    # 後から押された回答は無視される
    value = sent[0]["choices"][0]["value"]
    assert deadline.resolve_timeout_choice(ctx, value) is False


def test_is_immediate():
    assert is_immediate(json.dumps({"event": "ai_choice", "value": "timeout_extend:ab"}))
    assert not is_immediate(json.dumps({"event": "ai_choice", "value": "dungeon_size:small"}))
    assert not is_immediate(json.dumps({"event": "player_message"}))
    assert not is_immediate("壊れた")


async def test_serve_messages_lets_timeout_answer_skip_the_queue(make_ctx, sent, monkeypatch):
    """延長確認を待つ処理が受信キューを塞いでいても、回答は先に届く"""
    from gm_server import router

    ctx = make_ctx()
    order = []

    async def blocking_handler(ctx_, data):
        order.append("start")
        result = await deadline.run_with_deadline(ctx_, "x", asyncio.sleep(0.2, "late"), limit=0.02, answer_wait=5)
        order.append(result)

    monkeypatch.setitem(router.HANDLERS, "slow_job", blocking_handler)

    async def incoming():
        yield json.dumps({"event": "slow_job"})
        while not ctx.state.timeout_requests:
            await asyncio.sleep(0.005)
        rid = next(iter(ctx.state.timeout_requests))
        yield json.dumps({"event": "ai_choice", "value": f"timeout_extend:{rid}", "label": "延長する"})

    await asyncio.wait_for(serve_messages(ctx, incoming()), timeout=3)
    assert order == ["start", "late"]


async def test_run_with_deadline_reports_status_until_done(make_ctx):
    ctx = make_ctx()
    calls = []

    async def on_status(elapsed, remaining, detail):
        calls.append((round(remaining), detail))

    async def work():
        await asyncio.sleep(0.05)
        return "ok"

    result = await deadline.run_with_deadline(ctx, "テスト", work(), limit=100, detail=lambda: "完了 0/1",
                                              on_status=on_status, status_interval=0.01)
    assert result == "ok"
    assert len(calls) >= 2 and calls[0] == (100, "完了 0/1")
    count = len(calls)
    await asyncio.sleep(0.03)
    assert len(calls) == count  # 終わったら止まる
