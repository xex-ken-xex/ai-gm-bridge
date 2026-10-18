"""
FVTT (bridge.js) へ送信する action メッセージの組み立て

プロトコルを変更する場合は scripts/actions.js 側も同時に更新すること。
"""

from typing import Dict, List, Optional

from gm_server.config import SPEAKER_GM, SPEAKER_GM_ROLL, SPEAKER_SUPPORT


def chat(content: str, speaker: str = SPEAKER_GM) -> Dict:
    return {"action": "chat", "speaker": speaker, "content": content}


def roll(formula: str, speaker: str = SPEAKER_GM_ROLL) -> Dict:
    return {"action": "roll", "speaker": speaker, "formula": formula}


# play_sound の話者: Geminiちゃん自身の声 (マスコットが「話し中」になる)。無ければナレーションか NPC
SPEAKER_SOUND_SUPPORT = "support"


def play_sound(src: str, volume: float = 1.0, subtitle: Optional[str] = None, gm_only: bool = False,
               speaker: Optional[str] = None) -> Dict:
    """gm_only なら GM のクライアントでだけ鳴らす (声のテストなど、プレイヤーに聞かせない音)"""
    msg = {"action": "play_sound", "src": src, "volume": volume}
    if subtitle:
        msg["subtitle"] = subtitle
    if speaker:
        msg["speaker"] = speaker
    if gm_only:
        msg["gm_only"] = True
    return msg


def progress(message: str) -> Dict:
    """待ち時間の長い処理の途中経過 (GM の画面に通知として出す。チャットには残さない)"""
    return {"action": "progress", "message": message}


def interactive_card(title: str, message: str, choices: List[Dict], speaker: str = SPEAKER_SUPPORT) -> Dict:
    return {
        "action": "interactive_card",
        "title": title,
        "message": message,
        "speaker": speaker,
        "choices": [{"label": c["label"], "value": c["value"]} for c in choices],
    }


def builder_stage_result(
    stage: str,
    result: Dict,
    cards: Optional[List[Dict]] = None,
    seed: Optional[int] = None,
    fallback: bool = False,
    map_info: Optional[Dict] = None,
) -> Dict:
    """シナリオビルダーの段階ごとの下書き。cards / seed は地図帳ジャーナルに保存され、次回の抽選の除外と再現に使う"""
    msg = {
        "action": "builder_stage_result",
        "stage": stage,
        "result": result,
        "cards": cards or [],
        "seed": seed,
        "fallback": fallback,
    }
    if map_info:
        msg["map"] = map_info
    return msg


def builder_progress(stage: str, step: int, steps: int, step_label: str, elapsed: float, remaining: float,
                     detail: str = "", kind: str = "draft", finished: bool = False) -> Dict:
    """
    シナリオビルダーの進捗 (画面の「作成中」の行に出す)。remaining はタイムアウトまでの残り秒。
    kind: draft (下書き作成) / build (シーン構築)。finished: シーン構築が終わった (画面の「構築中」を消す)
    """
    return {
        "action": "builder_progress",
        "kind": kind,
        "finished": finished,
        "stage": stage,
        "step": step,
        "steps": steps,
        "step_label": step_label,
        "detail": detail,
        "elapsed": int(elapsed),
        "remaining": max(0, int(remaining)),
    }


def update_dungeon_rooms(scene_key: str, rooms: List[Dict]) -> Dict:
    """create_dungeon_scene (scene_key 付き) で作ったシーンの区画を書き換える (AI の情景ができた区画から順に)"""
    return {"action": "update_dungeon_rooms", "scene_key": scene_key, "rooms": rooms}


def npc_dialogue_response(result: Dict, speaker_token_id=None, scene_id=None) -> Dict:
    """
    NPC 会話の反応。GM のクライアントだけが受け取る (内心と秘密のヒントを含むため、チャットに出すのは
    地の文・セリフ・脇役の仕草と判定ボタンだけ)
    """
    return {"action": "npc_dialogue_response", "speaker_token_id": speaker_token_id, "scene_id": scene_id, "result": result}


def execute_npc_turn(decision: Dict, token_id: str, scene_id=None, combat: Optional[Dict] = None) -> Dict:
    """
    NPC の手番の行動。combat は頼まれたときの手番 ({id, round, turn}) をそのまま返し、
    FVTT は受け取ったときに手番が進んでいたら実行しない (M6)。内心 (internal_thought) は GM 宛てのメモにだけ出す
    """
    combat = combat or {}
    return {
        "action": "execute_npc_turn",
        "token_id": token_id,
        "scene_id": scene_id,
        "combat": {"id": combat.get("id"), "round": combat.get("round"), "turn": combat.get("turn")},
        "decision": decision,
    }


def adventure_log_collect(mode: str) -> Dict:
    """冒険記録: FVTT (GM) にチャットの記録・所持品の差分・場所の名前を集めて adventure_log_request を送ってもらう"""
    return {"action": "adventure_log_collect", "mode": mode}


def adventure_log_write(result: Dict) -> Dict:
    """冒険記録: 冒険日誌ジャーナルに書く内容 (一覧の統合とページの作成は FVTT 側)"""
    return {"action": "adventure_log_write", **result}
