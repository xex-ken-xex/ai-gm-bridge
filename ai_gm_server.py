import asyncio
import json
import os
import re
import time
import websockets
import edge_tts
from google import genai
from google.genai import types
from google.genai.errors import APIError

DATA_DIR = os.path.expanduser("~/foundrydata/Data")
API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=API_KEY)

SYSTEM_INSTRUCTION = """
あなたはファンタジーTRPGの熟練ゲームマスター(GM)です。
プレイヤーの宣言を受け、以下のJSONフォーマットのみを出力してください。Markdownのコードブロック(```json)は不要です。

{
  "narrative": "チャット欄に表示する情景描写（セリフ含むリッチテキスト）",
  "speech": "音声読み上げ用テキスト（簡潔に1〜2文。記号や括弧は含めないこと）",
  "dice": "/r 1d20 などのダイスコマンド（判定が必要な場合のみ。不要なら null）"
}
"""

def clean_html(text: str) -> str:
    """FVTTから送られてくるHTMLタグ (<p>など) を除去"""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text).strip()

async def generate_speech(text: str, file_name: str):
    file_path = os.path.join(DATA_DIR, file_name)
    voice = "ja-JP-NanamiNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(file_path)
    await asyncio.sleep(0.5)

async def query_gemini_with_retry(player_input: str, retries: int = 3) -> dict:
    """503エラーなどの一時的な混雑に備えて指数バックオフで再試行"""
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=f"プレイヤーの行動: {player_input}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except APIError as e:
            if "503" in str(e) and attempt < retries - 1:
                wait_sec = (attempt + 1) * 2
                print(f"Geminiサーバー混雑中(503)。{wait_sec}秒後に再試行します... (試行 {attempt + 1}/{retries})")
                await asyncio.sleep(wait_sec)
            else:
                raise e

async def handler(websocket):
    print("\nFoundry VTT が接続しました！FVTTのチャット欄からの入力を待機中...")
    print("=" * 60)

    async for raw_message in websocket:
        try:
            data = json.loads(raw_message)
            if data.get("event") != "player_message":
                continue

            user = data.get("user", "Player")
            raw_input = data.get("content", "")
            player_input = clean_html(raw_input)

            if not player_input:
                continue

            print(f"\n[FVTT受信] {user}: {player_input}")
            print("Geminiが思考中...")

            gm_response = await query_gemini_with_retry(player_input)
            narrative = gm_response.get("narrative", "")
            speech = gm_response.get("speech", "")
            dice = gm_response.get("dice")

            # 1. 音声生成
            voice_filename = None
            if speech:
                voice_filename = f"voice_{int(time.time())}.mp3"
                print(f"音声生成中 ({voice_filename}): 「{speech}」")
                await generate_speech(speech, voice_filename)

            # 2. チャット描写送信
            await websocket.send(json.dumps({
                "action": "chat",
                "speaker": "AI GM",
                "content": narrative
            }))

            # 3. ダイスロール送信
            if dice:
                await asyncio.sleep(0.3)
                formula = dice.replace("/r ", "").replace("/roll ", "").strip()
                await websocket.send(json.dumps({
                    "action": "roll",
                    "speaker": "AI GM (判定)",
                    "formula": formula
                }))
                print(f"ダイスロール送信: {formula}")

            # 4. 音声再生コマンド送信
            if voice_filename:
                await asyncio.sleep(0.5)
                await websocket.send(json.dumps({
                    "action": "play_sound",
                    "src": voice_filename,
                    "volume": 1.0
                }))
                print(f"音声再生指示送信: {voice_filename}")

        except Exception as e:
            print(f"エラー: {e}")

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("AI GM サーバー待機中 (ws://localhost:8765)...")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
