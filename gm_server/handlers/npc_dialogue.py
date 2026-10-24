"""E. NPC 会話 (声が届く NPC が、知識の境界と記憶の鮮明度に沿って反応する)

FVTT (GM) が聴き手の NPC を集めて npc_dialogue_request を送る。サーバーは AI に反応を 1 つ書かせ、
npc_dialogue_response (GM のクライアントにだけ届く) と、NPC ごとの声色で読み上げた play_sound を返す。
秘密のヒント (check_offer.target_secret_hint) と内心 (internal_thought) は GM のクライアントだけが持ち、チャットには出さない。
"""

import json
import re
import time
from typing import Dict, List, Optional

from gm_server import protocol
from gm_server.progress import log_detail, log_group, log_step
from gm_server.text import clean_html
from gm_server.tts import voice_filename
from tools.prompt_manager import load_cached_prompt

PHYSICAL_ACTIONS = ("stay", "approach_speaker", "step_back", "leave_scene", "turn_hostile")
# 1 回の会話で AI に渡す聴き手の上限 (近い順)
MAX_LISTENERS = 4
TRUST_DELTA_LIMIT = 2
TRUST_LIMIT = 5
IMPORTANCE_MIN, IMPORTANCE_MAX, IMPORTANCE_DEFAULT = 1, 5, 3
DC_MIN, DC_MAX, DC_DEFAULT = 5, 30, 13
DEFAULT_NPC_VOICE = "ja-JP-KeitaNeural"
# edge-tts のボイス名 (例: ja-JP-NanamiNeural)。それ以外の値は既定の声にする
VOICE_NAME_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural$")
# AI に渡す NPC の項目 (声色などは渡さない)
PROMPT_FIELDS = ("id", "name", "distance_ft", "goal", "trust", "public_known", "secret_known", "ignorance",
                 "short_term_memory", "long_term_memory")


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _text(value, limit: int = 600) -> str:
    return clean_html(str(value)).strip()[:limit] if value not in (None, "") else ""


def normalize_listeners(raw) -> List[Dict]:
    """FVTT から届いた聴き手を検める。id の無いもの・重複は捨て、近い順に MAX_LISTENERS 人まで"""
    listeners: List[Dict] = []
    seen = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        npc_id = str(item.get("id") or item.get("token_id") or "").strip()
        if not npc_id or npc_id in seen:
            continue
        seen.add(npc_id)
        listeners.append({
            "id": npc_id,
            "name": _text(item.get("name"), 80) or "NPC",
            "distance_ft": _int(item.get("distance_ft"), 0, 0, 10_000),
            "goal": _text(item.get("goal")),
            "trust": _int(item.get("trust"), 0, -TRUST_LIMIT, TRUST_LIMIT),
            "public_known": _text(item.get("public_known"), 1500),
            "secret_known": _text(item.get("secret_known"), 1500),
            "ignorance": _text(item.get("ignorance")),
            "short_term_memory": [_text(m, 200) for m in item.get("short_term_memory") or [] if m][:10],
            "long_term_memory": [_text(m, 240) for m in item.get("long_term_memory") or [] if m][:30],
            "voice": item.get("voice") if isinstance(item.get("voice"), dict) else {},
        })
    listeners.sort(key=lambda n: n["distance_ft"])
    return listeners[:MAX_LISTENERS]


def build_npc_dialogue_prompt(data: dict, listeners: List[Dict]) -> str:
    npcs = [{k: n[k] for k in PROMPT_FIELDS} for n in listeners]
    return load_cached_prompt("npc_dialogue_prompt.txt").format(
        room_name=_text(data.get("room_name"), 120) or "現在の場所",
        room_flavor=_text(data.get("room_flavor"), 800) or "(特記なし)",
        speaker_name=_text(data.get("speaker_name"), 80) or "PC",
        player_input=_text(data.get("player_input"), 1000),
        check_result_context=_text(data.get("check_result_context"), 800) or "なし (通常の会話)",
        listening_npcs_json=json.dumps(npcs, ensure_ascii=False, indent=1),
    )


def _strip_quotes(speech: str) -> str:
    return re.sub(r"^[「『\"]+|[」』\"]+$", "", speech.strip()).strip()


def normalize_dialogue_result(raw, listeners: List[Dict], speaker_name: str, player_input: str) -> Dict:
    """AI の応答を検める。読めなければ、最も近い NPC が様子をうかがう既定の反応にする"""
    first = listeners[0]
    if not isinstance(raw, dict) or not (raw.get("narrative_action") or raw.get("speech")):
        return {
            "primary_npc_id": first["id"],
            "primary_npc_name": first["name"],
            "internal_thought": "相手の発言の意図を測っている。",
            "narrative_action": f"{first['name']}は手を止め、値踏みするような視線で{speaker_name}を見返した。",
            "speech": "……その話、ここで続けるつもりか？",
            "ambient_reactions": [],
            "physical_action": "stay",
            "trust_delta": 0,
            "new_memory": {"summary": f"{speaker_name}に「{player_input[:25]}」と話しかけられた", "importance": 2},
            "check_offer": None,
            "fallback": True,
        }

    by_id = {n["id"]: n for n in listeners}
    primary = by_id.get(str(raw.get("primary_npc_id") or "")) or next(
        (n for n in listeners if n["name"] == raw.get("primary_npc_name")), first)
    ambient = []
    for item in raw.get("ambient_reactions") or []:
        if not isinstance(item, dict):
            continue
        npc = by_id.get(str(item.get("npc_id") or ""))
        emote = _text(item.get("silent_emote"), 200)
        if npc and npc is not primary and emote:
            ambient.append({"npc_id": npc["id"], "npc_name": npc["name"], "silent_emote": emote})

    memory = raw.get("new_memory") if isinstance(raw.get("new_memory"), dict) else {}
    summary = _text(memory.get("summary"), 200)
    offer = raw.get("check_offer") if isinstance(raw.get("check_offer"), dict) else {}
    skill = _text(offer.get("skill"), 20)
    if skill.lower() in ("null", "none", "なし"):
        skill = ""
    action = str(raw.get("physical_action") or "stay")
    return {
        "primary_npc_id": primary["id"],
        "primary_npc_name": primary["name"],
        "internal_thought": _text(raw.get("internal_thought"), 400),
        "narrative_action": _text(raw.get("narrative_action"), 800),
        "speech": _strip_quotes(_text(raw.get("speech"), 400)),
        "ambient_reactions": ambient,
        "physical_action": action if action in PHYSICAL_ACTIONS else "stay",
        "trust_delta": _int(raw.get("trust_delta"), 0, -TRUST_DELTA_LIMIT, TRUST_DELTA_LIMIT),
        "new_memory": {
            "summary": summary,
            "importance": _int(memory.get("importance"), IMPORTANCE_DEFAULT, IMPORTANCE_MIN, IMPORTANCE_MAX),
        } if summary else None,
        "check_offer": {
            "skill": skill,
            "dc": _int(offer.get("dc"), DC_DEFAULT, DC_MIN, DC_MAX),
            "target_secret_hint": _text(offer.get("target_secret_hint"), 400),
        } if skill else None,
        "fallback": False,
    }


def npc_voice(listener: Optional[Dict]) -> Dict[str, str]:
    """NPC の声色 (ボイス名・速さ・高さ)。不正な値は既定に戻す (速さと高さは TTS 側でも正規化する)"""
    voice = (listener or {}).get("voice") or {}
    name = str(voice.get("name") or "")
    return {
        "voice": name if VOICE_NAME_RE.match(name) else DEFAULT_NPC_VOICE,
        "rate": str(voice.get("rate") or "+0%"),
        "pitch": str(voice.get("pitch") or "+0Hz"),
    }


async def handle(ctx, data: dict) -> None:
    listeners = normalize_listeners(data.get("listening_npcs"))
    speaker_name = _text(data.get("speaker_name"), 80) or "PC"
    player_input = _text(data.get("player_input"), 1000)
    if not listeners or not player_input:
        log_step("NPC 会話: 聴き手か発言が無いため何もしません")
        return

    started = time.monotonic()
    log_group(f"NPC 会話: {speaker_name}「{player_input[:40]}」"
              f" (聴き手 {len(listeners)} 名: {'・'.join(n['name'] for n in listeners)})")
    if data.get("check_result_context"):
        log_detail(f"判定の結果: {_text(data.get('check_result_context'), 200)}")
    prompt = build_npc_dialogue_prompt(data, listeners)
    log_step(f"AI に問い合わせます (プロンプト {len(prompt)} 文字)")
    try:
        raw = await ctx.llm.query_json(prompt, task="npc_dialogue")
    except Exception as e:
        log_step(f"AI の問い合わせに失敗しました ({e})")
        raw = None
    result = normalize_dialogue_result(raw, listeners, speaker_name, player_input)
    log_step(f"{result['primary_npc_name']}が反応します"
             f"{' (AI の応答を読めなかったため既定の反応)' if result['fallback'] else ''}"
             f" / 信頼 {result['trust_delta']:+d} / 行動 {result['physical_action']}"
             f"{' / 判定の提案: ' + result['check_offer']['skill'] if result['check_offer'] else ''}")

    await ctx.send(protocol.npc_dialogue_response(
        result, speaker_token_id=data.get("speaker_token_id"), scene_id=data.get("scene_id"),
    ))

    speech = result["speech"]
    if speech:
        primary = next((n for n in listeners if n["id"] == result["primary_npc_id"]), None)
        voice = npc_voice(primary)
        file_name = voice_filename("voice_dlg")
        log_detail(f"{result['primary_npc_name']}の声 ({voice['voice']} {voice['rate']} {voice['pitch']}) を作ります")
        if await ctx.tts.synthesize(speech, file_name, **voice):
            await ctx.send(protocol.play_sound(file_name, 1.0, subtitle=f"🗣️ {result['primary_npc_name']}: 「{speech}」"))
    log_group(f"NPC 会話を終えました ({int(time.monotonic() - started)}秒)")
