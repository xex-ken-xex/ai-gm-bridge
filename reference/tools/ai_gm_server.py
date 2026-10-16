#!/usr/bin/env python3
"""
ai_gm_server.py - Ultimate Unified Server for Foundry VTT
=========================================================================
【対応イベント一覧】
1. builder_generate_stage : 4段階シナリオビルダー (1.地域 / 2.集落 / 3.旅路 / 4-A.廃墟前提)
2. builder_generate_bgm   : 集落の「楽しみBGM」「悲しみBGM」を別WSL(RTX 4070Ti)で生成＆Playlist登録
3. builder_build_scene    : 3モード空間構築 (underground / ruins[風見が丘型] / wilderness) + 3D環境音
4. npc_turn_request       : コンバット手番時のNPC自律戦術決定・移動・吹き出し・ダイス実行
5. npc_dialogue_request   : 聴取範囲NPCの知識境界・記憶鮮明度に基づく小説風会話＆個体別ボイスTTS
6. ai_command / ai_choice : GM自然言語コマンド & チャットカード互換フロー
7. room_entered           : トークン部屋進入時の自動TTSナレーション再生
8. player_message         : 通常プレイヤー行動に対するAI GM応答
"""

import asyncio
import json
import os
import re
import time
import urllib.request
from typing import Optional, Tuple, Dict, List
import websockets
import edge_tts
from edge_tts import constants, communicate
from google import genai
from google.genai import types
from google.genai.errors import APIError

# Microsoft Edge TTS エンドポイント移行 (api.msedgeservices.com) への恒久対応
constants.WSS_URL = (
    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
    f"?Ocp-Apim-Subscription-Key={constants.TRUSTED_CLIENT_TOKEN}"
)
communicate.WSS_URL = constants.WSS_URL

from tools.dungeon_generator import DungeonGenerator
from tools.prompt_manager import (
    get_active_world_info,
    build_region_stage_prompt,
    build_settlement_stage_prompt,
    build_wilderness_stage_prompt,
    build_site_lore_stage_prompt,
    build_site_rooms_stage_prompt,
    build_npc_tactics_prompt,
    build_npc_dialogue_prompt,
    extract_json_from_response
)

DATA_DIR = os.path.expanduser("~/foundrydata/Data")
API_KEY = os.environ.get("GEMINI_API_KEY")
# 別WSL (RTX 4070Ti) のオーディオ生成APIエンドポイント
AUDIO_WSL_URL = os.environ.get("AUDIO_WSL_URL", "http://127.0.0.1:8766/generate_audio")

client = genai.Client(api_key=API_KEY) if API_KEY else None

agy_session = {
    "conversation_id": None,
    "pending_dungeon": {},
    "last_wilderness_gen": None
}

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
    clean = re.compile(r"<.*?>")
    return re.sub(clean, "", text).strip()


def normalize_tts_rate(rate_str: str) -> str:
    """edge-tts 向けに話速パラメータ (+0%, -15% 等) を安全に正規化"""
    if not rate_str or not isinstance(rate_str, str):
        return "+0%"
    s = rate_str.strip()
    if re.match(r"^[+-]\d+%$", s):
        return s
    m = re.search(r"([+-]?\d+)", s)
    if m:
        val = int(m.group(1))
        return f"{val:+d}%"
    return "+0%"


def normalize_tts_pitch(pitch_str: str) -> str:
    """edge-tts 向けに声の高さパラメータ (+0Hz, -10Hz 等) を安全に正規化"""
    if not pitch_str or not isinstance(pitch_str, str):
        return "+0Hz"
    s = pitch_str.strip()
    if re.match(r"^[+-]\d+Hz$", s, re.IGNORECASE):
        return s[:-2] + "Hz"
    m = re.search(r"([+-]?\d+)", s)
    if m:
        val = int(m.group(1))
        return f"{val:+d}Hz"
    return "+0Hz"


async def generate_speech(
    text: str,
    file_name: str,
    voice: str = "ja-JP-NanamiNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz"
) -> bool:
    """NPCごとの声色（ボイス名・話す速さ・声の高さ）を反映して音声ファイルを生成"""
    if not text or not text.strip():
        return False
    try:
        file_path = os.path.join(DATA_DIR, file_name)
        safe_rate = normalize_tts_rate(rate)
        safe_pitch = normalize_tts_pitch(pitch)
        comm = edge_tts.Communicate(text.strip(), voice, rate=safe_rate, pitch=safe_pitch)
        await comm.save(file_path)
        await asyncio.sleep(0.3)
        return True
    except Exception as e:
        print(f"音声生成警告 (TTS一時スキップ): {e}")
        return False


async def generate_spatial_audio_if_available(
    prompt_en: str,
    duration: float = 22.0,
    file_prefix: str = "amb"
) -> Optional[str]:
    """別WSL (RTX 4070 Ti) のオーディオ生成サーバーが稼働していれば環境音/BGM(.ogg)を生成"""
    if not prompt_en or not isinstance(prompt_en, str) or prompt_en.lower() == "null":
        return None

    def _call_audio_api():
        req_data = json.dumps({
            "prompt": prompt_en,
            "duration": duration,
            "prefix": file_prefix
        }).encode("utf-8")
        req = urllib.request.Request(
            AUDIO_WSL_URL,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        res = await asyncio.to_thread(_call_audio_api)
        return res.get("relative_path")
    except Exception:
        # 別WSLのオーディオサーバーが未起動の場合は待ち時間ゼロでスキップ
        return None


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


async def query_llm_json(prompt: str) -> Optional[dict | list]:
    """Gemini API (優先) または agy CLI を使用して構造化JSONを取得"""
    if client:
        for attempt in range(2):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                parsed = extract_json_from_response(response.text)
                if parsed is not None:
                    return parsed
            except Exception as e:
                print(f"Gemini JSON生成警告 ({e}) - 再試行またはagyへフォールバック")
                await asyncio.sleep(1.5)

    resp_text, _ = await call_agy(prompt)
    return extract_json_from_response(resp_text)


async def query_ai_gm(player_input: str) -> dict:
    """通常プレイヤー発言に対するGM応答を生成"""
    if client:
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=f"プレイヤーの行動: {player_input}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Gemini API 失敗 ({e})。agy CLI へフォールバックします...")

    prompt = (
        f"あなたはファンタジーTRPGの熟練GMです。\n"
        f"プレイヤーの発言・行動: 「{player_input}」\n"
        f"以下のJSONのみを出力してください:\n"
        f'{{"narrative": "情景描写とセリフ", "speech": "音声読み上げ用の短い1〜2文", "dice": null}}'
    )
    resp_text, conv_id = await call_agy(prompt, agy_session["conversation_id"])
    agy_session["conversation_id"] = conv_id

    parsed = extract_json_from_response(resp_text)
    if isinstance(parsed, dict) and "narrative" in parsed:
        return parsed

    clean_resp = resp_text.replace("\n", " ").strip()
    sentences = [s.strip() for s in re.split(r"[。！!？?]", clean_resp) if s.strip()]
    return {
        "narrative": resp_text.replace("\n", "<br>"),
        "speech": sentences[0] if sentences else clean_resp[:50],
        "dice": None
    }


# =========================================================================
# 1. シナリオビルダーGUI Stage 1〜4-A ドラフト生成ハンドラ
# =========================================================================
async def handle_builder_generate_stage(websocket, data: dict):
    stage = data.get("stage", "region")
    user_prompt = data.get("user_prompt", "")
    world_info = get_active_world_info()

    print(f"\n[シナリオビルダー Stage生成] stage={stage}, prompt={user_prompt}")

    if stage == "region":
        prompt = build_region_stage_prompt(
            world_title=world_info.get("title", "The Antikythira"),
            world_description=world_info.get("description", ""),
            user_prompt=user_prompt
        )
        result = await query_llm_json(prompt)
        if not isinstance(result, dict):
            result = {
                "region_name": "灰霧の連峰と古王道",
                "public_lore": {
                    "climate_and_terrain": "冷涼な季節風が吹き抜ける石灰岩の高原地帯。深い霧と針葉樹の疎林が広がる。",
                    "natural_resources": [
                        {"name": "泥炭と黒鉄鉱", "location": "低地の湿原および古い露頭", "usage": "集落の暖房および鍛冶燃料"},
                        {"name": "耐寒性の香木と薬草", "location": "高地の斜面林", "usage": "保存食の燻製材、傷薬の原料"}
                    ],
                    "history_and_ruins": "かつての王国が築いた通信用の狼煙台や見張り塔が尾根沿いに点在するが、戦乱で放棄されている。",
                    "travel_impression": "湿った土と針葉樹の香りが冷たい風に乗って漂い、霧の切れ間に古い石積みの遺構が覗く。"
                },
                "dm_secrets": {
                    "hidden_threat": "放置された古塔の地下遺構に武装勢力が潜伏し、周辺の資源や旅人を狙っている。",
                    "faction_tensions": "遺構を占拠する勢力と、高地に棲む野生の怪物たちが縄張りを争っている。",
                    "ancient_secret": "古塔の地下には、かつて王国の防衛網を起動させていた魔力機関が眠っている。"
                },
                "audio_prompts": {
                    "region_ambience_en": "Cold wind across misty rocky highlands, distant nature ambience, seamless loop"
                }
            }

    elif stage == "settlement":
        region_ctx = data.get("region_context", {})
        scale = data.get("settlement_scale", "辺境の開拓村")
        prompt = build_settlement_stage_prompt(region_ctx, scale, user_prompt)
        result = await query_llm_json(prompt)
        if not isinstance(result, dict):
            result = {
                "settlement_name": "宿場村グレン",
                "public_lore": {
                    "architecture_and_scenery": "強風と寒さを防ぐため地元の石材を低く積んだ堅牢な家々が並び、煙突から香木の煙が立ち上る。",
                    "primary_industries": "高地での牧畜、寒冷地作物の栽培、および斜面林での木材・薬草採取。",
                    "secondary_industries": "香木で燻した保存肉、厚手の織物、旅人向けの携帯薬草酒の醸造。",
                    "micro_craft": {
                        "item_name": "端材の旅路コンパス・風見鈴",
                        "description": "加工の際に出る金属や木材の端材で作られる小さな風見鈴。霧の中で仲間とはぐれないよう旅人が外套に付ける。"
                    },
                    "daily_life_and_food": "夕暮れには囲炉裏のある酒場に人々が集まり、温かい根菜と燻製肉の煮込みで体を温める。",
                    "local_joys": "季節の交易市の賑わいと、炉端で奏でられる伝統的な弦楽器の合奏。",
                    "visible_sorrows": "街道の先にある古い廃墟の周辺で不穏な影が目撃され、交易路が途絶えかけていることへの不安。",
                    "key_npcs": [
                        {"name": "工房主エドガー", "role": "職人頭", "personality": "実直で寡黙。交易路の安全を誰よりも案じている。"}
                    ]
                },
                "dm_secrets": {
                    "sorrow_truth": "廃墟を占拠した勢力が街道の物資を略奪し、古代の防衛機関を悪用しようと企んでいる。",
                    "micro_craft_hook": "集落の職人が作った『風見鈴』が道中や廃墟の袋小路に落ちており、連れ去られた者や隠しルートの目印になる。",
                    "npc_hidden_agenda": "集落の一部の商人が、報復を恐れて密かに略奪者へ物資を流している。"
                },
                "audio_prompts": {
                    "joy_bgm_en": "Warm medieval acoustic lute and fiddle in a cozy tavern, crackling fireplace",
                    "sorrow_bgm_en": "Melancholic solo cello and soft flute in heavy mist, slow ambient"
                }
            }

    elif stage == "wilderness":
        region_ctx = data.get("region_context", {})
        settlement_ctx = data.get("settlement_context", {})
        w_gen = DungeonGenerator(width=100, height=100, density=0.4, loop_ratio=0.3, scene_mode="wilderness")
        w_gen.generate(target_room_count=9)
        agy_session["last_wilderness_gen"] = w_gen

        topos = w_gen.get_room_topologies()
        prompt = build_wilderness_stage_prompt(region_ctx, settlement_ctx, topos, user_prompt)
        result = await query_llm_json(prompt)
        if not isinstance(result, dict):
            result = {
                "wilderness_title": "古塔へ続く旧街道と獣道",
                "public_lore": {
                    "route_overview": "集落から尾根へと登る石畳の旧街道と、森を抜ける迂回路。時間帯によって霧の濃さが変化する。",
                    "weather_table": ["視界を奪う濃霧", "冷たい通り雨と強風", "雲間から差す淡い陽光"]
                },
                "dm_secrets": {
                    "stalking_threat": "メイン街道は斥候が監視しており、迂回路を通ることで奇襲を避けられる。",
                    "shortcut_secret": "迂回路の途中で集落の『生活財の遺留品』を発見すると、廃墟の崩落した裏口ルートが判明する。"
                },
                "landmarks": []
            }

    elif stage == "site_lore":
        settlement_ctx = data.get("settlement_context", {})
        wilderness_ctx = data.get("wilderness_context", {})
        actors_ctx = data.get("actors_context", {})
        site_mode = data.get("site_mode", "ruins")
        size_key = data.get("size_key", "small")
        size_label = {"small": "小", "medium": "中", "large": "大"}.get(size_key, "小")

        prompt = build_site_lore_stage_prompt(
            settlement_ctx=settlement_ctx,
            wilderness_ctx=wilderness_ctx,
            actors_ctx=actors_ctx,
            site_mode=site_mode,
            size_name=size_label,
            room_count=18,
            user_prompt=user_prompt
        )
        result = await query_llm_json(prompt)
        if not isinstance(result, dict):
            result = {
                "site_name": "風見の古塔",
                "mission_archetype": "④無敵ボスの弱点ギミック起動 ＆ 勢力瓦解",
                "public_lore": {
                    "exterior_and_history": "高地の尾根にそびえる古代の監視塔の廃墟。外壁の多くが崩落し、外の自然と内部の遺構が地続きになっている。",
                    "clear_main_goal": "古塔の最深部を占拠する首領を討ち、集落を脅かす危機を根絶せよ！",
                    "pc_narrative_hook": "入口の石畳に、集落で作られた見覚えのある工芸品の破片と新しい轍が残されている。",
                    "sensory_ambience": {
                        "visual": "風化した石垣、苔むした柱、揺らめく篝火の影",
                        "auditory": "崩れた壁の隙間を鳴らす風音と、奥から響く金属音",
                        "olfactory": "湿った土と焦げた油の匂い",
                        "air": "肌を刺す高地の冷気と張り詰めた空気"
                    }
                },
                "dm_secrets": {
                    "true_situation": "首領は古塔の古代機関を暴走させて周辺地域を支配しようとしているが、配下の2派閥は内部で反目している。",
                    "sub_objectives": [
                        {
                            "title": "袋小路の古代機関バルブの解放",
                            "target_topology": "浅層〜中層の袋小路 (deg=1)",
                            "connection_to_town_or_pc": "集落の遺留品が手がかりとなり、隠された制御室を発見できる。",
                            "effect": "最深部の首領を守っていた魔力障壁が完全に消滅する。"
                        },
                        {
                            "title": "対立する副官の密書奪取・離間工作",
                            "target_topology": "中層〜深層の袋小路またはハブ (deg>=3)",
                            "connection_to_town_or_pc": "PCの背景やNPCの情報から裏切り工作が可能になる。",
                            "effect": "敵の2勢力が同士討ちを始め、最深部の護衛が半減する。"
                        }
                    ],
                    "catharsis_payoff": "中目標を達成して最深部へ突入すると、絶対防御を誇っていた首領の障壁が砕け散り、さらに護衛たちが同士討ちで崩壊！狼狽する首領を圧倒的優位で討ち取れる！",
                    "factions": [
                        {"name": "外郭の傭兵部隊", "territory": "浅層〜中層の崩落石垣", "status": "待遇への不満から士気が揺らいでいる"},
                        {"name": "深部の首領親衛隊", "territory": "最深部の祭壇広間", "status": "古代機関の起動に集中している"}
                    ]
                }
            }

    await websocket.send(json.dumps({
        "action": "builder_stage_result",
        "stage": stage,
        "result": result
    }))
    print(f"[シナリオビルダー Stage生成完了] stage={stage} の結果をGUIへ返送しました。")


# =========================================================================
# 2. 集落BGMの生成＆プレイリスト自動登録ハンドラ
# =========================================================================
async def handle_generate_settlement_bgm(websocket, data: dict):
    settlement_name = data.get("settlement_name", "辺境の集落")
    joy_prompt = data.get("joy_bgm_en", "")
    sorrow_prompt = data.get("sorrow_bgm_en", "")

    print(f"\n[集落BGM生成開始] 『{settlement_name}』の2曲を並列生成中...")
    await websocket.send(json.dumps({
        "action": "chat",
        "speaker": "アギィ (Agy)",
        "content": f"🎵 <b>『{settlement_name}』のBGMを生成中...</b><br>RTX 4070 Ti で「くらしと楽しみ」「悲しみと静寂」の2曲を合成しています。"
    }))

    joy_task = generate_spatial_audio_if_available(joy_prompt, duration=24.0, file_prefix="bgm_joy") if joy_prompt else asyncio.sleep(0, result=None)
    sorrow_task = generate_spatial_audio_if_available(sorrow_prompt, duration=24.0, file_prefix="bgm_sorrow") if sorrow_prompt else asyncio.sleep(0, result=None)

    joy_path, sorrow_path = await asyncio.gather(joy_task, sorrow_task)

    if not joy_path and not sorrow_path:
        await websocket.send(json.dumps({
            "action": "chat",
            "speaker": "アギィ (Agy)",
            "content": "⚠️ 別WSLのオーディオサーバー(8766)に接続できなかったため、BGM生成をスキップしました（ジャーナル保存は完了しています）。"
        }))
        return

    await websocket.send(json.dumps({
        "action": "register_settlement_playlist",
        "settlement_name": settlement_name,
        "joy_src": joy_path,
        "sorrow_src": sorrow_path
    }))
    print(f"[集落BGM生成完了] joy={joy_path}, sorrow={sorrow_path}")


# =========================================================================
# 3. シーン一括構築ハンドラ (Stage 3 ワイルダネス / Stage 4-B 廃墟・迷宮)
# =========================================================================
async def handle_builder_build_scene(websocket, data: dict):
    scene_mode = data.get("scene_mode", "ruins")  # "ruins" | "underground" | "wilderness"
    scene_name = data.get("name", "風見の古塔")
    size_key = data.get("size_key", "small")
    density = float(data.get("density", 0.5))
    loop_ratio = float(data.get("loop_ratio", 0.25))

    size_map = {
        "small": (100, 100, "小", 14),
        "medium": (500, 500, "中", 6),
        "large": (1000, 1000, "大", 3),
        "xlarge": (2000, 2000, "特大", 2)
    }
    w, h, size_name, cell_size = size_map.get(size_key, (100, 100, "小", 14))

    if scene_mode == "wilderness":
        gen = agy_session.get("last_wilderness_gen")
        if not gen:
            gen = DungeonGenerator(width=100, height=100, density=0.4, loop_ratio=0.3, scene_mode="wilderness")
            gen.generate(target_room_count=9)
        w, h, cell_size = gen.width, gen.height, 14
    else:
        gen = DungeonGenerator(width=w, height=h, density=density, loop_ratio=loop_ratio, scene_mode=scene_mode)
        gen.generate()

    ts = int(time.time())
    png_filename = f"map_{scene_mode}_{w}x{h}_{ts}.png"
    png_filepath = os.path.join(DATA_DIR, png_filename)
    gen.render_to_png(png_filepath, cell_size=cell_size)

    walls = []
    if scene_mode != "wilderness" and w <= 1000:
        raw_walls = gen.extract_walls(grid_size=100)
        if len(raw_walls) <= 3500:
            walls = raw_walls

    site_public = data.get("site_public_lore") or data.get("public_lore", "")
    site_dm = data.get("site_dm_secrets") or data.get("dm_secrets", "")

    custom_flavors = None
    if scene_mode == "wilderness" and data.get("landmarks"):
        custom_flavors = data.get("landmarks")
    else:
        topologies = gen.get_room_topologies()
        rooms_prompt = build_site_rooms_stage_prompt(
            site_name=scene_name,
            site_mode=scene_mode,
            site_public_lore=site_public,
            site_dm_secrets=site_dm,
            topologies=topologies,
            max_rooms=18
        )
        print(f"[Stage 4-B 区画生成中] {scene_name} (mode={scene_mode}, 区画数={len(gen.rooms)})...")
        parsed_rooms = await query_llm_json(rooms_prompt)
        if isinstance(parsed_rooms, list):
            custom_flavors = parsed_rooms

    # 別WSL (RTX 4070 Ti) による主要区画の3D環境音並列生成
    if custom_flavors:
        audio_candidates = [
            cf for cf in custom_flavors
            if cf.get("ambient_sound_en") and str(cf.get("ambient_sound_en")).lower() != "null"
        ][:4]

        if audio_candidates:
            tasks = [
                generate_spatial_audio_if_available(
                    cf["ambient_sound_en"],
                    duration=20.0,
                    file_prefix=f"room_{cf.get('id', 0)}"
                )
                for cf in audio_candidates
            ]
            paths = await asyncio.gather(*tasks)
            for cf, rel_path in zip(audio_candidates, paths):
                if rel_path:
                    cf["ambient_sound_src"] = rel_path

    rooms = gen.get_room_flavor_notes(grid_size=100, custom_flavors=custom_flavors)

    lore_payload = {
        "dungeon_name": scene_name,
        "floor_title": f"{scene_name} ({'野外廃墟' if scene_mode == 'ruins' else ('旅路' if scene_mode == 'wilderness' else '地下迷宮')})",
        "history_summary": site_public.replace("\n", "<br>"),
        "floor_origin": "",
        "dm_secrets": site_dm.replace("\n", "<br>")
    }

    await websocket.send(json.dumps({
        "action": "create_dungeon_scene",
        "scene_mode": scene_mode,
        "name": scene_name,
        "background_src": png_filename,
        "grid_width": w,
        "grid_height": h,
        "grid_size": 100,
        "room_count": len(gen.rooms),
        "lore": lore_payload,
        "rooms": rooms,
        "density": density,
        "start_pos": gen.start_pos,
        "goal_pos": gen.goal_pos,
        "walls": walls
    }))

    voice_filename = f"voice_scene_{ts}.mp3"
    mode_ja = "野外廃墟" if scene_mode == "ruins" else ("ワイルダネス旅路" if scene_mode == "wilderness" else "ダンジョン")
    speech_text = f"{mode_ja}『{scene_name}』のシーンを構築しました。全{len(gen.rooms)}区画に目標ギミックと情景を設定しました。"
    if await generate_speech(speech_text, voice_filename):
        await websocket.send(json.dumps({
            "action": "play_sound",
            "src": voice_filename,
            "subtitle": speech_text,
            "volume": 1.0
        }))


# =========================================================================
# 4. NPC自律戦闘ターンハンドラ (npc_turn_request)
# =========================================================================
async def handle_npc_turn_request(websocket, data: dict):
    perc = data.get("perception", {})
    npc_name = perc.get("npc_name", "モンスター")
    print(f"\n[NPC自律戦闘思考] {npc_name} (HP: {perc.get('hp_current')}/{perc.get('hp_max')})")

    prompt = build_npc_tactics_prompt(perc)
    decision = await query_llm_json(prompt)

    if not isinstance(decision, dict):
        decision = {
            "move_intent": "approach_target",
            "target_token_id": (perc.get("perceived_targets") or [{}])[0].get("token_id"),
            "action_type": "attack",
            "action_name": "攻撃",
            "dice_formula": "1d20+4",
            "emote": f"{npc_name}は鋭い視線で間合いを詰める！",
            "speech": ""
        }

    decision["action"] = "execute_npc_turn"
    decision["token_id"] = perc.get("token_id")
    await websocket.send(json.dumps(decision))

    speech_text = (decision.get("speech") or "").strip()
    if speech_text:
        voice_file = f"voice_npc_atk_{int(time.time() * 1000)}.mp3"
        if await generate_speech(speech_text, voice_file, voice="ja-JP-KeitaNeural", rate="+5%", pitch="-5Hz"):
            await websocket.send(json.dumps({
                "action": "play_sound",
                "src": voice_file,
                "subtitle": f"⚔️ {npc_name}: 「{speech_text}」",
                "volume": 1.0
            }))


# =========================================================================
# 5. NPC小説風会話＆記憶・忘却ハンドラ (npc_dialogue_request)
# =========================================================================
async def handle_npc_dialogue_request(websocket, data: dict):
    speaker_name = data.get("speaker_name", "PC")
    player_input = data.get("player_input", "")
    listening_npcs = data.get("listening_npcs", [])

    if not listening_npcs:
        return

    print(f"\n[NPC会話システム] 発言者: {speaker_name} -> 「{player_input}」 (聴取NPC数: {len(listening_npcs)}名)")

    prompt = build_npc_dialogue_prompt(data)
    result = await query_llm_json(prompt)
    first_npc = listening_npcs[0]

    if not isinstance(result, dict) or "narrative_action" not in result:
        result = {
            "primary_npc_id": first_npc.get("token_id"),
            "primary_npc_name": first_npc.get("name", "NPC"),
            "internal_thought": "相手の発言の意図を測っている。",
            "narrative_action": f"{first_npc.get('name', 'NPC')}は手元の動きを止め、値踏みするような視線で{speaker_name}を見返した。",
            "speech": "……その話、ここで大声で続けるつもりか？",
            "ambient_reactions": [],
            "physical_action": "stay",
            "trust_delta": 0,
            "new_memory": {
                "summary": f"{speaker_name}から「{player_input[:25]}」と話しかけられた",
                "importance": 2
            },
            "check_offer": None
        }

    await websocket.send(json.dumps({
        "action": "npc_dialogue_response",
        "speaker_token_id": data.get("speaker_token_id"),
        "result": result
    }))

    speech_text = (result.get("speech") or "").strip()
    if speech_text:
        primary_id = result.get("primary_npc_id")
        matched_npc = next((n for n in listening_npcs if n.get("token_id") == primary_id), first_npc)
        voice_cfg = matched_npc.get("voice") or {}
        v_name = voice_cfg.get("name", "ja-JP-KeitaNeural")
        v_rate = voice_cfg.get("rate", "+0%")
        v_pitch = voice_cfg.get("pitch", "+0Hz")

        voice_filename = f"voice_dlg_{int(time.time() * 1000)}.mp3"
        if await generate_speech(speech_text, voice_filename, voice=v_name, rate=v_rate, pitch=v_pitch):
            await websocket.send(json.dumps({
                "action": "play_sound",
                "src": voice_filename,
                "subtitle": f"🗣️ {result.get('primary_npc_name')}: 「{speech_text}」",
                "volume": 1.0
            }))


# =========================================================================
# WebSocket メインハンドラ
# =========================================================================
async def handler(websocket):
    print("\nFoundry VTT が接続しました！(全機能統合モード: 4段階ビルダー / 自律NPC / 空間音響 待機中)")
    print("=" * 75)

    async for raw_message in websocket:
        try:
            data = json.loads(raw_message)
            event_type = data.get("event")

            # 1. 専用GUIからの各ステージ下書き生成 (Stage 1 / 2 / 3 / 4-A)
            if event_type == "builder_generate_stage":
                await handle_builder_generate_stage(websocket, data)
                continue

            # 2. 集落BGMの生成＆プレイリスト登録
            if event_type == "builder_generate_bgm":
                await handle_generate_settlement_bgm(websocket, data)
                continue

            # 3. 専用GUIからのシーン一括構築 (Stage 3 ワイルダネス / Stage 4-B 廃墟・迷宮)
            if event_type == "builder_build_scene":
                await handle_builder_build_scene(websocket, data)
                continue

            # 4. コンバット時のNPC自律戦闘ターン
            if event_type == "npc_turn_request":
                await handle_npc_turn_request(websocket, data)
                continue

            # 5. 非戦闘時のNPC小説風会話・記憶・判定提案
            if event_type == "npc_dialogue_request":
                await handle_npc_dialogue_request(websocket, data)
                continue

            # 6. GMからの /ai 自然言語コマンド
            if event_type == "ai_command":
                prompt = data.get("prompt", "").strip()
                print(f"\n[FVTT GM指示] /ai {prompt}")

                support_prompt = (
                    f"あなたはTRPG (D&D5e) の敏腕サポートGM『アギィ』です。"
                    f"メインGMからの指示や相談に創造的かつ簡潔に回答してください。\nGMからの指示: {prompt}"
                )
                agy_resp, conv_id = await call_agy(support_prompt, agy_session["conversation_id"])
                agy_session["conversation_id"] = conv_id

                await websocket.send(json.dumps({
                    "action": "chat",
                    "speaker": "アギィ (Agy)",
                    "content": agy_resp.replace("\n", "<br>")
                }))
                continue

            # 7. 旧チャットカード選択肢応答 (後方互換)
            if event_type == "ai_choice":
                val = data.get("value", "")
                if val.startswith("dungeon_loop:"):
                    parts = val.split(":")
                    size_key = parts[1]
                    density = float(parts[2]) if len(parts) >= 4 else 0.5
                    loop_ratio = float(parts[3]) if len(parts) >= 4 else 0.25
                    await handle_builder_build_scene(websocket, {
                        "scene_mode": "underground",
                        "name": "地下迷宮",
                        "size_key": size_key,
                        "density": density,
                        "loop_ratio": loop_ratio
                    })
                continue

            # 8. トークン部屋進入検知 (room_entered 自動音声ナレーション)
            if event_type == "room_entered":
                room_name = data.get("room_name", "区画")
                flavor = data.get("flavor", "")
                if flavor:
                    voice_filename = f"voice_room_{int(time.time() * 1000)}.mp3"
                    speech_text = f"{room_name}。{flavor}"
                    if await generate_speech(speech_text, voice_filename):
                        await websocket.send(json.dumps({
                            "action": "play_sound",
                            "src": voice_filename,
                            "subtitle": f"📍 {room_name}: {flavor[:45]}...",
                            "volume": 0.95
                        }))
                continue

            # 9. 通常のプレイヤー発言 (AI GM 応答)
            if event_type == "player_message":
                player_input = clean_html(data.get("content", ""))
                if not player_input:
                    continue

                gm_response = await query_ai_gm(player_input)
                narrative = gm_response.get("narrative", "")
                speech = gm_response.get("speech", "")
                dice = gm_response.get("dice")

                voice_filename = None
                if speech:
                    voice_filename = f"voice_{int(time.time() * 1000)}.mp3"
                    if not await generate_speech(speech, voice_filename):
                        voice_filename = None

                await websocket.send(json.dumps({
                    "action": "chat",
                    "speaker": "アギィ (AI GM)",
                    "content": narrative
                }))

                if dice:
                    await asyncio.sleep(0.3)
                    formula = dice.replace("/r ", "").replace("/roll ", "").strip()
                    await websocket.send(json.dumps({
                        "action": "roll",
                        "speaker": "アギィ (判定)",
                        "formula": formula
                    }))

                if voice_filename:
                    await asyncio.sleep(0.4)
                    await websocket.send(json.dumps({
                        "action": "play_sound",
                        "src": voice_filename,
                        "subtitle": speech,
                        "volume": 1.0
                    }))

        except Exception as e:
            print(f"サーバー処理エラー: {e}")


async def main():
    print("AI GM Bridge Unified Server starting on ws://127.0.0.1:8765 ...")
    async with websockets.serve(handler, "127.0.0.1", 8765, max_size=15 * 1024 * 1024):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
