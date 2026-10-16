"""テキスト処理の純粋関数 (I/O なし・テスト容易)"""

import re
from typing import Optional

_TAG_RE = re.compile(r"<.*?>", re.DOTALL)
_SENTENCE_SPLIT_RE = re.compile(r"[。！!？?]")


def clean_html(text: str) -> str:
    """FVTTから送られてくるHTMLタグ (<p>など) を除去"""
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def newlines_to_br(text: str) -> str:
    """改行をチャット表示用の <br> に変換"""
    return (text or "").replace("\n", "<br>")


def first_sentence(text: str, fallback_len: int = 50) -> str:
    """音声読み上げ用に最初の1文を取り出す"""
    clean = (text or "").replace("\n", " ").strip()
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(clean) if s.strip()]
    return sentences[0] if sentences else clean[:fallback_len]


def dice_formula_from_command(dice: Optional[str]) -> Optional[str]:
    """`/r 1d20` や `/roll 1d20+3` からダイス式だけを取り出す。空なら None"""
    if not dice or not isinstance(dice, str):
        return None
    formula = re.sub(r"^\s*/(?:r|roll)\b\s*", "", dice.strip(), flags=re.IGNORECASE).strip()
    return formula or None


def normalize_tts_rate(rate_str: Optional[str]) -> str:
    """edge-tts 向けに話速パラメータ (+0%, -15% 等) を安全に正規化"""
    if not rate_str or not isinstance(rate_str, str):
        return "+0%"
    s = rate_str.strip()
    if re.fullmatch(r"[+-]\d+%", s):
        return s
    m = re.search(r"([+-]?\d+)", s)
    if m:
        return f"{int(m.group(1)):+d}%"
    return "+0%"


def normalize_tts_pitch(pitch_str: Optional[str]) -> str:
    """edge-tts 向けに声の高さパラメータ (+0Hz, -10Hz 等) を安全に正規化"""
    if not pitch_str or not isinstance(pitch_str, str):
        return "+0Hz"
    s = pitch_str.strip()
    if re.fullmatch(r"[+-]\d+hz", s, re.IGNORECASE):
        return s[:-2] + "Hz"
    m = re.search(r"([+-]?\d+)", s)
    if m:
        return f"{int(m.group(1)):+d}Hz"
    return "+0Hz"
