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


def interactive_card(title: str, message: str, choices: List[Dict], speaker: str = SPEAKER_SUPPORT) -> Dict:
    return {
        "action": "interactive_card",
        "title": title,
        "message": message,
        "speaker": speaker,
        "choices": [{"label": c["label"], "value": c["value"]} for c in choices],
    }
