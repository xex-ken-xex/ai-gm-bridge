"""テキスト処理の純粋関数 (I/O なし・テスト容易)"""

import html
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


# AI の文章で残してよい飾りのタグ (属性なし)。ほかは文字として見せる
_ALLOWED_TAG_RE = re.compile(r"&lt;(/?)(b|i|em|strong|br)\s*/?&gt;", re.IGNORECASE)


def ai_text_to_html(text: str) -> str:
    """
    AI の文章をチャットの HTML にする。AI の文章はプレイヤーの発言に操られうるため、
    いったん全部エスケープしてから、属性なしの b / i / em / strong / br だけを戻す
    (img の外部 URL で閲覧者の IP を集める、on* 属性でスクリプトを動かす、などを防ぐ)
    """
    escaped = html.escape(text or "", quote=True)
    return newlines_to_br(_ALLOWED_TAG_RE.sub(lambda m: f"<{m.group(1)}{m.group(2).lower()}>", escaped))


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
    return formula if is_safe_dice_formula(formula) else None


# ダイスの個数・面数の上限 (AI がプレイヤーに操られて 99999d99999 を返すと、GM のブラウザが固まる)
MAX_DICE_COUNT = 100
MAX_DICE_FACES = 1000
_DICE_FORMULA_RE = re.compile(r"^[0-9dDkKhHlL+\-*/() ]{1,60}$")
_DICE_TERM_RE = re.compile(r"(\d*)[dD](\d+)")


def is_safe_dice_formula(formula: str) -> bool:
    """数字・d・kh/kl・四則演算・括弧だけで、ダイスの数と面数が上限以内か (@ の参照やフレーバー文は断る)"""
    if not formula or not _DICE_FORMULA_RE.match(formula):
        return False
    total = 0
    for count, faces in _DICE_TERM_RE.findall(formula):
        total += int(count or 1)
        if int(faces) > MAX_DICE_FACES or total > MAX_DICE_COUNT:
            return False
    return True


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
