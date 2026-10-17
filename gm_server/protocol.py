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


def play_sound(src: str, volume: float = 1.0, subtitle: Optional[str] = None) -> Dict:
    msg = {"action": "play_sound", "src": src, "volume": volume}
    if subtitle:
        msg["subtitle"] = subtitle
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
