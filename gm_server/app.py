"""WebSocket サーバー本体"""

import asyncio
import contextlib
import ipaddress
from typing import Optional

from gm_server import config, logs, protocol
from gm_server.handlers.ai_settings import is_loopback_url
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
# 裏で動かすタスク (途中で捨てられないよう参照を持っておく)
_background = set()
# 送り先の接続 (依頼元が閉じていたら再接続後の GM へ送る)
_hub = ConnectionHub()


def _services():
    global _llm, _tts
    if _llm is None:
        # AI の接続先の振り分け設定 (ai_providers.json) があれば読む
        _llm = LLMService.from_config(gemini_client=create_gemini_client())
    if _tts is None:
        _tts = SpeechSynthesizer()
        # edge-tts に切り替わったときの通知は、そのとき繋がっている GM に送る
        _tts.notify = lambda message: _hub.send(protocol.progress(message))
    return _llm, _tts


def is_allowed_origin(origin: Optional[str], peer: Optional[str] = None, allowed=None) -> bool:
    """
    繋いできたページ (Origin ヘッダー) を受けてよいか。
    ブラウザは開いているどのサイトからでも ws://127.0.0.1:8765 に繋げてしまうため、FVTT のページ以外は断る。
    Origin が無いもの (tools/test_send.py など、ブラウザ以外のプログラム) は、自分の PC から (peer がループバック) だけ受ける。
    (Origin を付けない LAN のプログラムは断る。ただし Origin はブラウザ以外なら偽れるので、
    AI_GM_HOST=0.0.0.0 にしたときの守りにはならない。0.0.0.0 が要るなら合言葉 (トークン) を足すこと)
    """
    if not origin:
        return is_loopback_address(peer)
    # null は file:// や sandbox の iframe。どのサイトからでも名乗れるので、一覧にあっても受けない
    if origin == "null":
        return False
    allowed = config.ALLOWED_ORIGINS if allowed is None else allowed
    return origin.rstrip("/") in allowed or is_loopback_url(origin)


def is_loopback_address(address: Optional[str]) -> bool:
    try:
        return ipaddress.ip_address(address or "").is_loopback
    except ValueError:
        return False


def request_origin(websocket) -> Optional[str]:
    # websockets 14 以降は websocket.request.headers、13 以前 (legacy) は websocket.request_headers
    request = getattr(websocket, "request", None)
    headers = getattr(request, "headers", None) or getattr(websocket, "request_headers", None)
    return headers.get("Origin") if headers is not None else None


def peer_address(websocket) -> Optional[str]:
    remote = getattr(websocket, "remote_address", None)
    return str(remote[0]) if remote else None


async def handler(websocket):
    origin = request_origin(websocket)
    peer = peer_address(websocket)
    if not is_allowed_origin(origin, peer):
        # 断った相手は攻撃してきたページかもしれないので、その URL を足すようには勧めない
        print(f"⚠️ 接続を断りました (ページ: {origin or 'なし'}、接続元: {peer})。"
              f"自分で FVTT を localhost 以外の URL で開いているときだけ、その URL を AI_GM_ALLOWED_ORIGINS に書いてください")
        await websocket.close(code=1008, reason="origin not allowed")
        return
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
        irodori_error = await tts.check_irodori()
        if irodori_error:
            print(f"  ※ Irodori-TTS に繋がりません ({irodori_error})。繋がるまで edge-tts で読み上げます")
        elif irodori_error == "":
            print("  Irodori-TTS に繋がりました (裏でモデルを読み込みます)")
            # 最初の読み上げでモデルの読み込みを待たないよう、裏で 1 回作らせておく
            _background.add(asyncio.create_task(tts.warm_up_irodori()))
        print(f"  詳細ログ: {log_handler.log_dir}/<日付>/server.log ({log_handler.keep_days} 日分)")
        print("=" * 65)
        await asyncio.Future()
