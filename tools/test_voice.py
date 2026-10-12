import asyncio
import json
import os
import websockets
import edge_tts

# Foundry VTTのDataフォルダのパス
VOICE_FILE_PATH = os.path.expanduser("~/foundrydata/Data/ai_voice.mp3")

async def generate_speech(text: str, output_path: str):
    """edge-tts を使ってテキストから音声ファイル (MP3) を生成する"""
    # 日本語音声（男性: ja-JP-KeitaNeural、女性: ja-JP-NanamiNeural）
    voice = "ja-JP-NanamiNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    print(f"音声ファイルを生成しました: {output_path}")

async def handler(websocket):
    print("FVTTが接続してきました！")
    await asyncio.sleep(1)

    # 1. 読み上げるセリフ
    speech_text = "冒険者の皆さん、ようこそ。私の声が聞こえていますか？"

    # チャットに投稿
    chat_msg = {
        "action": "chat",
        "speaker": "AI GM (ナナミ)",
        "content": speech_text
    }
    await websocket.send(json.dumps(chat_msg))
    print(f"チャットを送信しました: {speech_text}")

    # 音声を生成
    print("音声を生成中...")
    await generate_speech(speech_text, VOICE_FILE_PATH)

    # 2. FVTTに音声再生を指示 (Dataフォルダ直下の相対パスを指定)
    sound_msg = {
        "action": "play_sound",
        "src": "ai_voice.mp3",
        "volume": 0.9
    }
    await websocket.send(json.dumps(sound_msg))
    print("音声再生コマンド（ai_voice.mp3）を送信しました。")

    # 接続維持
    while True:
        await asyncio.sleep(1)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("音声テスト用サーバー待機中 (ws://localhost:8765)...")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
