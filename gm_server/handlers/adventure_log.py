"""F. 冒険記録スキル (セッションのログから冒険日誌を書く)

GM が `/ai 記録を作って` / `/ai 記録を更新して` と頼むと、サーバーは adventure_log_collect を返す。
FVTT (GM) はチャットの記録・PC の所持品の差分・場所の名前を集めて adventure_log_request を送る。
サーバーは AI にリプレイ風の本文と取得物・知り合った人々・訪れた場所を書かせ、adventure_log_write で返す。
冒険日誌ジャーナルへの書き込み (一覧の統合を含む) は FVTT 側で行う。
"""

import time
from typing import Dict, List

from gm_server import protocol
from gm_server.config import SPEAKER_SUPPORT
from gm_server.progress import log_group, log_step
from gm_server.text import clean_html
from tools.prompt_manager import load_cached_prompt

MODES = ("create", "update")
# AI に渡すチャットの記録の上限 (新しい方を残す)
MAX_TRANSCRIPT_CHARS = 60_000
MAX_LINE_CHARS = 600
MAX_PREVIOUS_CHARS = 3000
MAX_ITEMS, MAX_PEOPLE, MAX_PLACES = 40, 30, 20

_LOG_WORDS = ("記録", "日誌", "リプレイ", "冒険録")
_SESSION_WORDS = ("セッション", "今日の冒険", "今回の冒険")
_ACT_WORDS = ("作", "つく", "更新", "まとめ", "書", "つけ", "残", "追記")
_UPDATE_WORDS = ("更新", "追記", "続き", "追加")


def is_adventure_log_intent(prompt: str) -> bool:
    """`/ai 記録を作って`・`/ai 日誌を更新して`・`/ai 今日のセッションをまとめて` など"""
    text = prompt or ""
    if any(w in text for w in _LOG_WORDS):
        return any(w in text for w in _ACT_WORDS)
    # 「セッション」だけでは NPC 作りなどと区別できないので「まとめ」とセットのときだけ
    return any(w in text for w in _SESSION_WORDS) and "まとめ" in text


def log_mode(prompt: str) -> str:
    """「更新」「追記」なら最新の回に書き足す (update)。それ以外は新しい回を作る (create)"""
    return "update" if any(w in (prompt or "") for w in _UPDATE_WORDS) else "create"


def _text(value, limit: int) -> str:
    return clean_html(str(value)).strip()[:limit] if value not in (None, "") else ""


def normalize_transcript(raw) -> List[str]:
    """「名前: 発言」の行にする。空行は捨て、合計が上限を超えたら古い方から削る"""
    lines: List[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"), MAX_LINE_CHARS)
        if not text:
            continue
        speaker = _text(item.get("speaker"), 60)
        lines.append(f"{speaker}: {text}" if speaker else text)
    total = 0
    kept: List[str] = []
    for line in reversed(lines):
        total += len(line) + 1
        if total > MAX_TRANSCRIPT_CHARS:
            break
        kept.append(line)
    kept.reverse()
    return kept


def normalize_inventory_changes(raw) -> List[Dict]:
    changes = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 80)
        if not name:
            continue
        try:
            quantity = max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            quantity = 1
        changes.append({"name": name, "holder": _text(item.get("holder"), 60), "quantity": quantity})
    return changes[:MAX_ITEMS]


def _names(raw, limit: int) -> List[str]:
    names = []
    for name in raw if isinstance(raw, list) else []:
        clean = _text(name, 80)
        if clean and clean not in names:
            names.append(clean)
    return names[:limit]


def mode_instruction(mode: str, session_number: int) -> str:
    if mode == "update":
        return (f"第{session_number}回の続きを書きます。前回までの本文の後に続く部分だけを書いてください "
                f"(前回の内容を繰り返さない)。`title` は第{session_number}回全体に合う見出しにしてください。")
    return f"第{session_number}回の記録を新しく書きます。この回の出来事を最初から最後までまとめてください。"


def build_adventure_log_prompt(data: dict, mode: str, session_number: int, transcript: List[str],
                               inventory_changes: List[Dict]) -> str:
    pcs = _names(data.get("pcs"), 12)
    places = _names(data.get("place_hints"), 30)
    previous = _text(data.get("previous_replay"), 100_000)[-MAX_PREVIOUS_CHARS:]
    changes = [f"- {c['holder'] or '(不明)'}: {c['name']}" + (f" ×{c['quantity']}" if c["quantity"] > 1 else "")
               for c in inventory_changes]
    return load_cached_prompt("adventure_log_prompt.txt").format(
        mode_instruction=mode_instruction(mode, session_number),
        pcs="、".join(pcs) or "(不明)",
        place_hints="、".join(places) or "(なし)",
        previous_replay=previous if mode == "update" and previous else "(なし)",
        inventory_changes="\n".join(changes) or "(なし)",
        transcript="\n".join(transcript),
    )


def _entries(raw, keys, limit: int) -> List[Dict]:
    out: List[Dict] = []
    seen = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        entry = {k: _text(item.get(k), 80 if k in ("name", "holder") else 200) for k in keys}
        if not entry["name"] or (entry["name"], entry.get("holder", "")) in seen:
            continue
        seen.add((entry["name"], entry.get("holder", "")))
        out.append(entry)
    return out[:limit]


def normalize_log_result(raw, mode: str, session_number: int, transcript: List[str],
                         inventory_changes: List[Dict], place_hints: List[str]) -> Dict:
    """AI の応答を検める。読めなければログの抜粋と所持品の差分だけの記録にする"""
    ok = isinstance(raw, dict) and _text(raw.get("replay"), 10)
    data = raw if ok else {}
    items = _entries(data.get("items"), ("name", "holder", "note"), MAX_ITEMS)
    # 所持品の差分は AI が書き漏らしても必ず載せる
    have = {i["name"] for i in items}
    for change in inventory_changes:
        if change["name"] not in have:
            items.append({"name": change["name"], "holder": change["holder"], "note": ""})
            have.add(change["name"])
    if ok:
        replay = _text(data.get("replay"), 8000)
        title = _text(data.get("title"), 40)
        people = _entries(data.get("people"), ("name", "note"), MAX_PEOPLE)
        places = _entries(data.get("places"), ("name", "note"), MAX_PLACES)
    else:
        excerpt = "\n".join(transcript[-20:])
        replay = f"(AI のまとめを作れなかったので、チャットの記録の終わりの方をそのまま残します)\n{excerpt}"
        title = ""
        people = []
        places = [{"name": p, "note": ""} for p in place_hints][:MAX_PLACES]
    return {
        "mode": mode,
        "session_number": session_number,
        "title": title or f"第{session_number}回の記録",
        "replay": replay,
        "items": items[:MAX_ITEMS],
        "people": people,
        "places": places,
        "fallback": not ok,
    }


async def request_log(ctx, prompt: str, mode: str = "") -> None:
    """`/ai 記録を作って` から: FVTT に記録の材料を集めてもらう (mode が無ければ指示の文から決める)"""
    mode = mode if mode in MODES else log_mode(prompt)
    log_step(f"冒険記録スキル: FVTT に記録の材料を頼みます ({mode})")
    await ctx.send(protocol.chat(
        "📖 了解！冒険日誌" + ("の続きを書くね" if mode == "update" else "を書くね") + "。チャットの記録を読んでまとめるから、ちょっと待ってて！",
        speaker=SPEAKER_SUPPORT,
    ))
    await ctx.send(protocol.adventure_log_collect(mode))


async def handle(ctx, data: dict) -> None:
    mode = data.get("mode") if data.get("mode") in MODES else "create"
    try:
        session_number = max(1, int(data.get("session_number") or 1))
    except (TypeError, ValueError):
        session_number = 1
    transcript = normalize_transcript(data.get("transcript"))
    if not transcript:
        log_step("冒険記録: チャットの記録が空のため何もしません")
        return
    inventory_changes = normalize_inventory_changes(data.get("inventory_changes"))
    place_hints = _names(data.get("place_hints"), 30)

    started = time.monotonic()
    log_group(f"冒険記録: 第{session_number}回 ({mode}) / ログ {len(transcript)} 行 / 所持品の差分 {len(inventory_changes)} 件")
    prompt = build_adventure_log_prompt(data, mode, session_number, transcript, inventory_changes)
    log_step(f"AI に問い合わせます (プロンプト {len(prompt)} 文字)")
    try:
        raw = await ctx.llm.query_json(prompt)
    except Exception as e:
        log_step(f"AI の問い合わせに失敗しました ({e})")
        raw = None
    result = normalize_log_result(raw, mode, session_number, transcript, inventory_changes, place_hints)
    log_step(f"「{result['title']}」: 取得物 {len(result['items'])}・人物 {len(result['people'])}・場所 {len(result['places'])}"
             f"{' (AI の応答を読めなかったため抜粋のみ)' if result['fallback'] else ''}")
    await ctx.send(protocol.adventure_log_write(result))
    log_group(f"冒険記録を送りました ({int(time.monotonic() - started)}秒)")

