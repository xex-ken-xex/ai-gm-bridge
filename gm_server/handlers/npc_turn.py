"""F. NPC 自律戦闘 (戦闘の手番を迎えた NPC が、知覚した状況から行動を決める)

FVTT (GM) が手番の NPC の知覚 (HP・行動の一覧・区画・PC との距離と視線) を集めて npc_turn_request を送る。
サーバーは AI に行動を 1 つ決めさせ、execute_npc_turn と、NPC の声色で読み上げた play_sound を返す。
execute_npc_turn には頼まれたときの手番 (combat の id / round / turn) をそのまま付けて返し、
FVTT は受け取ったときに手番が進んでいたら実行しない (M6)。内心 (internal_thought) は GM 宛てのメモにだけ出す。
"""

import json
import re
import time
from typing import Dict, List, Optional

from gm_server import protocol
from gm_server.handlers.npc_dialogue import npc_voice
from gm_server.progress import log_detail, log_group, log_step
from gm_server.text import clean_html
from gm_server.tts import voice_filename
from tools.prompt_manager import load_cached_prompt

MOVE_INTENTS = ("approach_target", "flank_target", "retreat_from_enemies", "seek_cover_or_gimmick", "hold_position")
ACTION_TYPES = ("attack", "skill_or_spell", "interact_environment", "dodge_or_flee", "parley")
MAX_TARGETS = 8
MAX_ACTIONS = 8
# ダイス式として受け付ける文字 (scripts/lib/npc-tactics.js の sanitizeFormula と揃える)
FORMULA_RE = re.compile(r"^[0-9dkhl+\-*/() ]{1,60}$", re.IGNORECASE)
DICE_RE = re.compile(r"\d*d\d+", re.IGNORECASE)


def _text(value, limit: int = 600) -> str:
    return clean_html(str(value)).strip()[:limit] if value not in (None, "") else ""


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def sanitize_formula(formula) -> Optional[str]:
    """AI のダイス式を検める (M5)。式として読めなければ None"""
    f = str(formula or "").strip()
    if not f or not FORMULA_RE.match(f) or not DICE_RE.search(f):
        return None
    depth = 0
    for ch in f:
        depth += 1 if ch == "(" else -1 if ch == ")" else 0
        if depth < 0:
            return None
    return f if depth == 0 else None


def normalize_perception(raw) -> Optional[Dict]:
    """FVTT から届いた知覚を検める。手番の NPC の token_id が無ければ None"""
    if not isinstance(raw, dict):
        return None
    token_id = str(raw.get("token_id") or "").strip()
    if not token_id:
        return None
    targets: List[Dict] = []
    seen = set()
    for t in raw.get("perceived_targets") or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("token_id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        los = t.get("has_los")
        targets.append({
            "token_id": tid,
            "name": _text(t.get("name"), 80) or "PC",
            "distance_ft": _int(t.get("distance_ft"), 0, 0, 10_000),
            "adjacent": bool(t.get("adjacent")),
            "has_los": los if isinstance(los, bool) else None,
            "health_status": _text(t.get("health_status"), 20),
        })
    targets.sort(key=lambda t: t["distance_ft"])
    actions = []
    for a in raw.get("available_actions") or []:
        name = _text(a.get("name") if isinstance(a, dict) else a, 80)
        if name and all(x["name"] != name for x in actions):
            actions.append({"name": name, "type": _text(a.get("type"), 20) if isinstance(a, dict) else ""})
    return {
        "token_id": token_id,
        "npc_name": _text(raw.get("npc_name"), 80) or "モンスター",
        "npc_role": _text(raw.get("npc_role"), 80) or "NPC",
        "npc_bio": _text(raw.get("npc_bio"), 400) or "この領域の住人。警戒している",
        "npc_goal": _text(raw.get("npc_goal"), 300) or "侵入者を退ける",
        "hp_current": _int(raw.get("hp_current"), 10, -10_000, 100_000),
        "hp_max": _int(raw.get("hp_max"), 10, 0, 100_000),
        "health_status": _text(raw.get("health_status"), 20) or "不明",
        "move_speed": _int(raw.get("move_speed"), 30, 0, 1_000),
        "available_actions": actions[:MAX_ACTIONS],
        "room_name": _text(raw.get("room_name"), 120) or "周辺区画",
        "room_flavor": _text(raw.get("room_flavor"), 800),
        "room_gm_notes": _text(raw.get("room_gm_notes"), 800),
        "perceived_targets": targets[:MAX_TARGETS],
        "voice": raw.get("voice") if isinstance(raw.get("voice"), dict) else {},
    }


def build_npc_tactics_prompt(perc: Dict, round_number) -> str:
    actions = "\n".join(f"・{a['name']}{' (' + a['type'] + ')' if a['type'] else ''}"
                        for a in perc["available_actions"]) or "・(一覧なし。素手や爪牙などの基本攻撃)"
    return load_cached_prompt("npc_tactics_prompt.txt").format(
        npc_name=perc["npc_name"], npc_role=perc["npc_role"], npc_bio=perc["npc_bio"], npc_goal=perc["npc_goal"],
        hp_current=perc["hp_current"], hp_max=perc["hp_max"], health_status=perc["health_status"],
        move_speed=perc["move_speed"], round=round_number or "?", available_actions=actions,
        room_name=perc["room_name"], room_flavor=perc["room_flavor"] or "(特記なし)",
        room_gm_notes=perc["room_gm_notes"] or "(特記なし)",
        perceived_targets_json=json.dumps(perc["perceived_targets"], ensure_ascii=False, indent=1) or "[]",
    )


def _strip_quotes(speech: str) -> str:
    return re.sub(r"^[「『\"]+|[」』\"]+$", "", speech.strip()).strip()


def _default_target(perc: Dict) -> Optional[Dict]:
    targets = perc["perceived_targets"]
    standing = [t for t in targets if t["health_status"] != "倒れている"]
    return (standing or targets or [None])[0]


def normalize_turn_decision(raw, perc: Dict) -> Dict:
    """AI の応答を検める。狙いと行動は知覚の一覧にあるものだけ、式は検めたものだけを通す。読めなければ最寄りの PC に詰め寄る"""
    names = {a["name"]: a["name"] for a in perc["available_actions"]}
    names_lower = {n.lower(): n for n in names}
    by_id = {t["token_id"]: t for t in perc["perceived_targets"]}

    if not isinstance(raw, dict) or not (raw.get("emote") or raw.get("move_intent") or raw.get("action_name")):
        target = _default_target(perc)
        first = next((a["name"] for a in perc["available_actions"] if a["type"] == "weapon"), None)
        return {
            "internal_thought": "目の前の敵を排除する。",
            "move_intent": "approach_target" if target else "hold_position",
            "target_token_id": target["token_id"] if target else None,
            "action_type": "attack",
            "action_name": first,
            "dice_formula": None if first else "1d20",
            "emote": f"{perc['npc_name']}は鋭い視線で間合いを詰める！",
            "speech": "",
            "fallback": True,
        }

    target = by_id.get(str(raw.get("target_token_id") or ""))
    intent = str(raw.get("move_intent") or "hold_position")
    if intent not in MOVE_INTENTS:
        intent = "hold_position"
    # 接近・挟み撃ちに狙いが無ければ最寄りの PC を狙う (知覚の外の id は使わない)
    if intent in ("approach_target", "flank_target") and not target:
        target = _default_target(perc)
        if not target:
            intent = "hold_position"
    action_name = _text(raw.get("action_name"), 80)
    action_name = names.get(action_name) or names_lower.get(action_name.lower()) if action_name else None
    action_type = str(raw.get("action_type") or "attack")
    return {
        "internal_thought": _text(raw.get("internal_thought"), 400),
        "move_intent": intent,
        "target_token_id": target["token_id"] if target else None,
        "action_type": action_type if action_type in ACTION_TYPES else "attack",
        "action_name": action_name,
        "dice_formula": sanitize_formula(raw.get("dice_formula")),
        "emote": _text(raw.get("emote"), 400),
        "speech": _strip_quotes(_text(raw.get("speech"), 300)),
        "fallback": False,
    }


async def handle(ctx, data: dict) -> None:
    perc = normalize_perception(data.get("perception"))
    if perc is None:
        log_step("NPC の手番: 手番の NPC が分からないため何もしません")
        return
    combat = data.get("combat") if isinstance(data.get("combat"), dict) else {}

    started = time.monotonic()
    log_group(f"NPC の手番: {perc['npc_name']} (ラウンド {combat.get('round', '?')} / HP {perc['hp_current']}/{perc['hp_max']}"
              f" / 見えている PC {len(perc['perceived_targets'])} 名)")
    prompt = build_npc_tactics_prompt(perc, combat.get("round"))
    log_step(f"AI に戦術を問い合わせます (プロンプト {len(prompt)} 文字)")
    try:
        raw = await ctx.llm.query_json(prompt, task="npc_turn")
    except Exception as e:
        log_step(f"AI の問い合わせに失敗しました ({e})")
        raw = None
    decision = normalize_turn_decision(raw, perc)
    target = next((t["name"] for t in perc["perceived_targets"] if t["token_id"] == decision["target_token_id"]), "なし")
    log_step(f"{perc['npc_name']}の行動: 移動 {decision['move_intent']} / 狙い {target}"
             f" / {decision['action_name'] or decision['dice_formula'] or '行動なし'}"
             f"{' (AI の応答を読めなかったため既定の行動)' if decision['fallback'] else ''}")

    await ctx.send(protocol.execute_npc_turn(
        decision, token_id=perc["token_id"], scene_id=data.get("scene_id"), combat=combat,
    ))

    speech = decision["speech"]
    if speech:
        voice = npc_voice(perc)
        file_name = voice_filename("voice_npc_atk")
        log_detail(f"{perc['npc_name']}の声 ({voice['voice']} {voice['rate']} {voice['pitch']}) を作ります")
        if await ctx.tts.synthesize(speech, file_name, **voice):
            await ctx.send(protocol.play_sound(file_name, 1.0, subtitle=f"⚔️ {perc['npc_name']}: 「{speech}」"))
    log_group(f"NPC の手番を終えました ({int(time.monotonic() - started)}秒)")
