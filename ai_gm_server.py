import asyncio
import json
import os
import re
import time
from typing import Optional, Tuple
import websockets
import edge_tts
from google import genai
from google.genai import types
from google.genai.errors import APIError

from tools.dungeon_generator import DungeonGenerator, parse_size
from tools.prompt_manager import (
    get_active_world_info,
    build_lore_prompt,
    build_rooms_prompt,
    extract_json_from_response
)

DATA_DIR = os.path.expanduser("~/foundrydata/Data")
API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=API_KEY) if API_KEY else None

# agy 会話セッション管理
agy_session = {
    "conversation_id": None,
    "pending_dungeon": {}
}

SYSTEM_INSTRUCTION = """
あなたファンタジーTRPGの熟練ゲームマスター(GM)です。
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

async def generate_speech(text: str, file_name: str, voice: str = "ja-JP-NanamiNeural") -> bool:
    """音声ファイルを生成"""
    try:
        file_path = os.path.join(DATA_DIR, file_name)
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(file_path)
        await asyncio.sleep(0.4)
        return True
    except Exception as e:
        print(f"音声生成警告 (TTS一時スキップ): {e}")
        return False

async def query_gemini_with_retry(player_input: str, retries: int = 3) -> dict:
    """503エラーなどの一時的な混雑に備えて指数バックオフで再試行"""
    if not client:
        raise ValueError("GEMINI_API_KEY が環境変数に設定されていません。")
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

async def query_ai_gm(player_input: str) -> dict:
    """Gemini API または agy CLI を使用して TRPG GM の応答を生成"""
    if client:
        try:
            return await query_gemini_with_retry(player_input)
        except Exception as e:
            print(f"Gemini API 呼び出し失敗 ({e})。agy CLI へフォールバックします...")

    print(f"agy CLI (サポートGM) にプレイヤー発言を問い合わせ中...")
    prompt = (
        f"あなたはファンタジーTRPGの熟練ゲームマスター(GM)です。\n"
        f"プレイヤーの発言・行動: 「{player_input}」\n"
        f"状況の描写とGMとしての返答をしてください。\n"
        f"以下のJSONフォーマットのみを出力してください（Markdownコードブロックは不要です）:\n"
        f'{{"narrative": "チャット欄に表示する情景描写（セリフ含む）", "speech": "音声読み上げ用テキスト（簡潔に1〜2文。記号や括弧は不要）", "dice": null}}'
    )
    resp_text, conv_id = await call_agy(prompt, agy_session["conversation_id"])
    agy_session["conversation_id"] = conv_id

    try:
        cleaned = re.sub(r"^```(?:json)?\s*", "", resp_text.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        data = json.loads(cleaned)
        if isinstance(data, dict) and "narrative" in data:
            return data
    except Exception:
        pass

    clean_resp = resp_text.replace("\n", " ").strip()
    sentences = [s.strip() for s in re.split(r"[。！!？?]", clean_resp) if s.strip()]
    speech = sentences[0] if sentences else clean_resp[:50]
    return {
        "narrative": resp_text.replace("\n", "<br>"),
        "speech": speech,
        "dice": None
    }

async def call_agy(prompt: str, conversation_id: Optional[str] = None) -> Tuple[str, str]:
    """Antigravity Agent (agy) CLI を非同期呼び出し"""
    cmd = ["agy", "-p", prompt, "--output-format", "json"]
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])

    print(f"[agy 実行中] プロンプト長: {len(prompt)}文字")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    out_str = stdout.decode("utf-8").strip()

    try:
        res = json.loads(out_str)
        response_text = res.get("response", "").strip()
        conv_id = res.get("conversation_id", conversation_id or "")
        return response_text, conv_id
    except Exception:
        return out_str or stderr.decode("utf-8").strip(), conversation_id or ""

async def generate_and_create_dungeon_scene(websocket, size_key: str, loop_ratio: float, density: float = 0.5, user_prompt: str = ""):
    """ダンジョンを生成し、ワールド設定から伝承・生態系・部屋フレーバーをリアルタイム補完してFVTTに新シーン作成命令を送信"""
    size_map = {
        "small": (100, 100, "小", 14),
        "medium": (500, 500, "中", 6),
        "large": (1000, 1000, "大", 3),
        "xlarge": (2000, 2000, "特大", 2)
    }
    w, h, size_name, cell_size = size_map.get(size_key, (100, 100, "小", 14))

    density_label = "標準" if density <= 0.5 else ("密集" if density <= 0.8 else "ギッシリ")
    print(f"\n[ダンジョン自動生成開始] サイズ: {size_name} ({w}x{h}), 密度: {density_label} ({density}), ループ率: {loop_ratio}")

    # 1. ダンジョン幾何学生成
    gen = DungeonGenerator(width=w, height=h, density=density, loop_ratio=loop_ratio)
    gen.generate()

    # 2. PNG画像の書き出し (Dataディレクトリ直下)
    ts = int(time.time())
    png_filename = f"dungeon_{w}x{h}_{ts}.png"
    png_filepath = os.path.join(DATA_DIR, png_filename)

    print(f"PNGマップ書き出し中: {png_filepath} (セル解像度: {cell_size}px)...")
    gen.render_to_png(png_filepath, cell_size=cell_size)
    print(f"PNGマップ作成完了 ({os.path.getsize(png_filepath):,} bytes)")

    # 3. 壁 (Wall) セグメントの自動抽出 (衝突・視界遮断用)
    walls = []
    if w <= 100:
        walls = gen.extract_walls(grid_size=100)
    elif w <= 1000:
        raw_walls = gen.extract_walls(grid_size=100)
        if len(raw_walls) <= 3000:
            walls = raw_walls
        else:
            print(f"壁総数({len(raw_walls)}本)が上限(3000本)を超過したためパフォーマンス保護のためスキップ")
    print(f"壁セグメント抽出完了: {len(walls)} 本 (部屋数: {len(gen.rooms)})")

    # 4. ワールド設定の読み込み
    world_info = get_active_world_info()
    world_title = world_info.get("title", "The Antikythira")
    world_desc = world_info.get("description", "ゴブリンキングとブラックドラゴンの地下城塞")

    # 進行状況をチャットへ即時通知（ユーザーへの応答速度と安心感向上）
    await websocket.send(json.dumps({
        "action": "chat",
        "speaker": "サポートGM (agy)",
        "content": f"🏰 <b>ダンジョン構造を算出しました</b> (部屋数: {len(gen.rooms)}部屋)<br>📜 ワールド設定『{world_desc}』に基づき、伝承・生態系と各部屋の情景をAI補完中..."
    }))

    # 5. Tier 1 & 2: ダンジョン全体の歴史・伝承とフロア生態系を生成
    lore_prompt = build_lore_prompt(
        world_title=world_title,
        world_description=world_desc,
        user_prompt=user_prompt,
        floor_number=1,
        size_name=size_name,
        room_count=len(gen.rooms)
    )
    print("[Lore生成中] agy に問い合わせ...")
    lore_data = None
    try:
        lore_resp, _ = await call_agy(lore_prompt)
        lore_data = extract_json_from_response(lore_resp)
    except Exception as e:
        print(f"Lore生成失敗 ({e})。フォールバック伝承を使用します。")

    if not isinstance(lore_data, dict) or "floor_title" not in lore_data:
        lore_data = {
            "dungeon_name": "黒水城塞 (Blackwater Bastion)",
            "floor_title": f"第1層: 酸蝕の外郭城塞 ({size_name})",
            "floor_origin": "古代の堅牢な要塞がブラックドラゴンの酸ブレスで崩壊し、現在はゴブリンキングの軍勢が占拠している。",
            "history_summary": "沼沢地の要衝として建造されたが、竜の襲来により壊滅。現在は竜とゴブリンが共生・支配する。",
            "factions": [
                {"name": "泥爪ゴブリン軍団", "territory": "外郭検問所および詰所", "status": "侵入者を警戒し略奪を企む"},
                {"name": "酸蝕の竜血信徒", "territory": "水路および深部通路", "status": "ブラックドラゴンに仕え酸の罠を設置"}
            ],
            "floor_ambience": {
                "visual": "酸で溶解した切石の壁面と黒苔、粗末な獣脂松明の赤い光",
                "auditory": "天井から滴る酸性水が石を焦がす音と遠くの獣の唸り声",
                "olfactory": "鼻腔を突く刺激的な硫黄臭と獣の体臭",
                "air": "生ぬるく肌にまとわりつく高湿度の酸性蒸気"
            }
        }

    # 進行状況通知 2: 伝承確定
    await websocket.send(json.dumps({
        "action": "chat",
        "speaker": "サポートGM (agy)",
        "content": f"⚔️ <b>伝承決定:</b> 『{lore_data.get('dungeon_name')}』 - {lore_data.get('floor_title')}<br>各部屋のトポロジー（接続数・深さ・空室配分）に基づき情景とGMメモを補完中..."
    }))

    # 6. Tier 3: トポロジーに基づく部屋フレーバー補完生成
    topologies = gen.get_room_topologies()
    rooms_prompt = build_rooms_prompt(lore_data, topologies, max_rooms=15)
    print("[Room補完中] agy に問い合わせ...")
    custom_flavors = None
    try:
        rooms_resp, _ = await call_agy(rooms_prompt)
        parsed_rooms = extract_json_from_response(rooms_resp)
        if isinstance(parsed_rooms, list):
            custom_flavors = parsed_rooms
    except Exception as e:
        print(f"Roomフレーバー生成失敗 ({e})。ベースプロシージャルノートを使用します。")

    # 7. 各部屋フレーバーの統合
    rooms = gen.get_room_flavor_notes(grid_size=100, custom_flavors=custom_flavors)
    print(f"フレーバーノート生成完了: {len(rooms)} 部屋 (AI補完反映済)")

    # 8. FVTTへシーン作成コマンド送信
    scene_title = f"{lore_data.get('dungeon_name', '地下城塞')} - {lore_data.get('floor_title', f'第1層 ({size_name})')}"
    await websocket.send(json.dumps({
        "action": "create_dungeon_scene",
        "name": scene_title,
        "background_src": png_filename,
        "grid_width": w,
        "grid_height": h,
        "grid_size": 100,
        "room_count": len(gen.rooms),
        "lore": lore_data,
        "rooms": rooms,
        "density": density,
        "start_pos": gen.start_pos,
        "goal_pos": gen.goal_pos,
        "walls": walls
    }))

    # 9. 音声報告
    voice_filename = f"voice_dungeon_{ts}.mp3"
    speech_text = f"ダンジョン『{lore_data.get('dungeon_name', '地下城塞')}』のシーンを生成しました。全{len(gen.rooms)}部屋に生態系と情景ノートを設定しました。"
    ok = await generate_speech(speech_text, voice_filename, voice="ja-JP-NanamiNeural")
    if ok:
        await websocket.send(json.dumps({
            "action": "play_sound",
            "src": voice_filename,
            "volume": 1.0
        }))
    print("ダンジョンシーン構築指示送信完了！")

async def handler(websocket):
    print("\nFoundry VTT が接続しました！(サポートGM agy 連携モード待機中)")
    print("=" * 65)

    async for raw_message in websocket:
        try:
            data = json.loads(raw_message)
            event_type = data.get("event")

            # -------------------------------------------------------------
            # A. GMからの /ai 指示コマンド (agy 連携)
            # -------------------------------------------------------------
            if event_type == "ai_command":
                user = data.get("user", "GM")
                prompt = data.get("prompt", "").strip()
                print(f"\n[FVTT GM指示] {user}: /ai {prompt}")

                # ダンジョン生成のキーワード検知
                if not prompt or "ダンジョン" in prompt or "マップ" in prompt or "迷宮" in prompt:
                    agy_session["pending_dungeon"]["user_prompt"] = prompt
                    # ステップ1: サイズ選択肢カードを返送
                    card_msg = {
                        "action": "interactive_card",
                        "title": "ダンジョン自動生成",
                        "message": "サポートGM (agy) です。生成するダンジョンの規模を選択してください：",
                        "choices": [
                            {"label": "小 (100x100マス)", "value": "dungeon_size:small"},
                            {"label": "中 (500x500マス)", "value": "dungeon_size:medium"},
                            {"label": "大 (1000x1000マス)", "value": "dungeon_size:large"},
                            {"label": "特大 (2000x2000マス)", "value": "dungeon_size:xlarge"}
                        ]
                    }
                    await websocket.send(json.dumps(card_msg))
                    print("サイズ選択肢カードをFVTTへ送信しました。")
                    continue

                # その他の自然言語指示は agy CLI へルーティング
                print(f"agyへプロンプトを委譲中: {prompt}")
                agy_resp, conv_id = await call_agy(prompt, agy_session["conversation_id"])
                agy_session["conversation_id"] = conv_id

                await websocket.send(json.dumps({
                    "action": "chat",
                    "speaker": "サポートGM (agy)",
                    "content": agy_resp.replace("\n", "<br>")
                }))
                continue

            # -------------------------------------------------------------
            # B. GMによるチャット内ボタン選択結果 (ai_choice)
            # -------------------------------------------------------------
            if event_type == "ai_choice":
                val = data.get("value", "")
                label = data.get("label", "")
                print(f"\n[FVTT GM選択] {label} ({val})")

                # ステップ2: サイズ選択完了 -> 部屋の密度選択肢カードを返送
                if val.startswith("dungeon_size:"):
                    size_key = val.split(":")[1]
                    card_msg = {
                        "action": "interactive_card",
                        "title": "ダンジョン構造 (部屋の密度)",
                        "message": f"サイズ「{label}」を承りました。部屋の密集度（部屋数）を選んでください：",
                        "choices": [
                            {"label": "標準 (適度な部屋数 / 20部屋前後)", "value": f"dungeon_density:{size_key}:0.4"},
                            {"label": "密集 (部屋多め / 35部屋前後)", "value": f"dungeon_density:{size_key}:0.7"},
                            {"label": "ギッシリ (超高密度 / 50部屋前後)", "value": f"dungeon_density:{size_key}:1.0"}
                        ]
                    }
                    await websocket.send(json.dumps(card_msg))
                    print("部屋密度選択肢カードをFVTTへ送信しました。")
                    continue

                # ステップ3: 部屋密度選択完了 -> ループ率選択肢カードを返送
                if val.startswith("dungeon_density:"):
                    parts = val.split(":")
                    size_key = parts[1]
                    density = float(parts[2])
                    card_msg = {
                        "action": "interactive_card",
                        "title": "ダンジョン構造 (周回路)",
                        "message": f"部屋密度「{label}」を承りました。周回路（ループ・迂回ルート）の多さを選んでください：",
                        "choices": [
                            {"label": "袋小路のみ (純木構造 / 0%)", "value": f"dungeon_loop:{size_key}:{density}:0.0"},
                            {"label": "標準 (適度な周回路 / 20%)", "value": f"dungeon_loop:{size_key}:{density}:0.2"},
                            {"label": "迷路多め (複雑な周回 / 40%)", "value": f"dungeon_loop:{size_key}:{density}:0.4"}
                        ]
                    }
                    await websocket.send(json.dumps(card_msg))
                    print("ループ率選択肢カードをFVTTへ送信しました。")
                    continue

                # ステップ4: ループ率選択完了 -> 実際のダンジョン生成 & シーン作成を実行！
                if val.startswith("dungeon_loop:"):
                    parts = val.split(":")
                    size_key = parts[1]
                    if len(parts) >= 4:
                        density = float(parts[2])
                        loop_ratio = float(parts[3])
                    else:
                        density = 0.5
                    user_prompt = agy_session["pending_dungeon"].get("user_prompt", "")
                    await generate_and_create_dungeon_scene(websocket, size_key, loop_ratio, density, user_prompt=user_prompt)
                    continue

            # -------------------------------------------------------------
            # C. トークン部屋進入検知 (room_entered 自動音声読み上げ)
            # -------------------------------------------------------------
            if event_type == "room_entered":
                room_name = data.get("room_name", "部屋")
                flavor = data.get("flavor", "")
                token_name = data.get("token_name", "プレイヤー")
                print(f"\n[部屋進入検知] {token_name} -> {room_name}: {flavor[:40]}...")
                if flavor:
                    voice_filename = f"voice_room_{int(time.time())}.mp3"
                    speech_text = f"{room_name}。{flavor}"
                    ok = await generate_speech(speech_text, voice_filename, voice="ja-JP-NanamiNeural")
                    if ok:
                        await websocket.send(json.dumps({
                            "action": "play_sound",
                            "src": voice_filename,
                            "volume": 0.95
                        }))
                        print(f"部屋進入音声再生指示送信: {voice_filename}")
                continue

            # -------------------------------------------------------------
            # D. 通常のプレイヤー発言 (Gemini TRPG GM 演出)
            # -------------------------------------------------------------
            if event_type == "player_message":
                user = data.get("user", "Player")
                raw_input = data.get("content", "")
                player_input = clean_html(raw_input)

                if not player_input:
                    continue

                print(f"\n[FVTTプレイヤー発言] {user}: {player_input}")
                print("AI GMが思考中...")

                gm_response = await query_ai_gm(player_input)
                narrative = gm_response.get("narrative", "")
                speech = gm_response.get("speech", "")
                dice = gm_response.get("dice")

                # 音声生成
                voice_filename = None
                if speech:
                    voice_filename = f"voice_{int(time.time())}.mp3"
                    print(f"音声生成中 ({voice_filename}): 「{speech}」")
                    ok = await generate_speech(speech, voice_filename)
                    if not ok:
                        voice_filename = None

                # チャット描写送信
                await websocket.send(json.dumps({
                    "action": "chat",
                    "speaker": "AI GM",
                    "content": narrative
                }))

                # ダイスロール送信
                if dice:
                    await asyncio.sleep(0.3)
                    formula = dice.replace("/r ", "").replace("/roll ", "").strip()
                    await websocket.send(json.dumps({
                        "action": "roll",
                        "speaker": "AI GM (判定)",
                        "formula": formula
                    }))
                    print(f"ダイスロール送信: {formula}")

                # 音声再生指示送信
                if voice_filename:
                    await asyncio.sleep(0.5)
                    await websocket.send(json.dumps({
                        "action": "play_sound",
                        "src": voice_filename,
                        "volume": 1.0
                    }))

        except Exception as e:
            print(f"エラー: {e}")

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        engine = "Gemini API" if client else "agy (Antigravity Agent) [GEMINI_API_KEY 不要]"
        print("=" * 65)
        print("  AI GM Bridge サーバー起動完了 (ws://0.0.0.0:8765)")
        print(f"  AIエンジン: {engine}")
        print("=" * 65)
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
