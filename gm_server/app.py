"""WebSocket サーバー本体"""

import asyncio

from gm_server import config
from gm_server.context import Context, SessionState, websocket_sender
from gm_server.llm import LLMService
from gm_server.router import dispatch
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
    ctx = Context(send_json=websocket_sender(websocket), llm=llm, tts=tts, state=_state)
    async for raw_message in websocket:
        await dispatch(ctx, raw_message)


async def main():
    import websockets

    llm, _ = _services()
    async with websockets.serve(handler, config.HOST, config.PORT, max_size=config.MAX_MESSAGE_SIZE):
        print("=" * 65)
        print(f"  AI GM Bridge サーバー起動完了 (ws://{config.HOST}:{config.PORT})")
        print(f"  AIエンジン: {llm.engine_name}")
        print("=" * 65)
        await asyncio.Future()
