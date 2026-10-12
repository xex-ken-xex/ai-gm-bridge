import asyncio
import json
import websockets

async def handler(websocket):
    print("FVTTが接続してきました！")
    await asyncio.sleep(1)

    # 1. チャット投稿
    msg = {
        "action": "chat",
        "speaker": "AI GM",
        "content": "テストメッセージです。音声を再生します！"
    }
    await websocket.send(json.dumps(msg))
    print("チャットメッセージを送信しました。")

    await asyncio.sleep(1)

    # 2. 音声再生テスト（Foundry VTT標準のダイス音）
    sound_msg = {
        "action": "play_sound",
        "src": "sounds/dice.wav",
        "volume": 0.8
    }
    await websocket.send(json.dumps(sound_msg))
    print("音声再生コマンド（sounds/dice.wav）を送信しました。")

    # 接続を維持
    while True:
        await asyncio.sleep(1)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("WebSocketサーバー待機中 (ws://localhost:8765)...")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
