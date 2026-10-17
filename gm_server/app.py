"""WebSocket サーバー本体"""

import asyncio
import contextlib

from gm_server import config, logs
from gm_server.context import ConnectionHub, Context, SessionState
from gm_server.llm import LLMService
from gm_server.router import dispatch, is_immediate
from gm_server.tts import SpeechSynthesizer


def create_gemini_client():
    if not config.API_KEY:
        return None
    from google import genai

    return genai.Client(api_key=config.API_KEY)


# 接続をまたいで共有する状態 (サポートGM の会話 ID・ウィザード途中経過)
_llm = None
_tts = None
_state = SessionState()
# 送り先の接続 (依頼元が閉じていたら再接続後の GM へ送る)
_hub = ConnectionHub()


def _services():
    global _llm, _tts
    if _llm is None:
        _llm = LLMService(gemini_client=create_gemini_client())
    if _tts is None:
        _tts = SpeechSynthesizer()
    return _llm, _tts


async def handler(websocket):
    print("\nFoundry VTT が接続しました！(サポートGM agy 連携モード待機中)")
    print("=" * 65)
    llm, tts = _services()
    ctx = Context(send_json=_hub.sender_for(websocket), llm=llm, tts=tts, state=_state)
    await _hub.attach(websocket)
    try:
        await serve_messages(ctx, websocket)
    finally:
        _hub.detach(websocket)


async def serve_messages(ctx, messages) -> None:
    """
    受信メッセージは 1 件ずつ順番に処理する (シーン構築の途中に次の処理が割り込まないように)。
    ただし延長確認の回答だけは、順番待ちの処理がそれを待っているため即座に処理する。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def worker():
        while True:
            raw = await queue.get()
            try:
                await dispatch(ctx, raw)
            finally:
                queue.task_done()

    worker_task = asyncio.create_task(worker())
    try:
        async for raw_message in messages:
            if is_immediate(raw_message):
                await dispatch(ctx, raw_message)
            else:
                queue.put_nowait(raw_message)
        # 接続が閉じたら受信済みの分を処理し終えてから終わる
        await queue.join()
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


async def main():
    import websockets

    log_handler = logs.setup_logging()
    llm, tts = _services()
    async with websockets.serve(handler, config.HOST, config.PORT, max_size=config.MAX_MESSAGE_SIZE):
        print("=" * 65)
        print(f"  AI GM Bridge サーバー起動完了 (ws://{config.HOST}:{config.PORT})")
        print(f"  AIエンジン: {llm.engine_name}")
        print(f"  音声エンジン: {tts.engine_name}")
        print(f"  詳細ログ: {log_handler.log_dir}/<日付>/server.log ({log_handler.keep_days} 日分)")
        print("=" * 65)
        await asyncio.Future()
