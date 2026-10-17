"""
4 段階シナリオビルダー (地域 → 集落 → 旅路 → 遺跡) のプロンプト組み立て

整合性とマンネリ防止のしくみ:
- 上位段階の本文に加えて「確定事項 (canon)」と「未回収の伏線 (open_threads)」を引き継ぐ。
  確定事項は各段階の AI 出力に含まれ、GM が編集したうえで地図帳ジャーナルに保存される
- FVTT のワールド資料 (ワールド説明・GM が選んだジャーナル) を最優先の設定として渡す
- 既出の固有名詞 (地図帳・Actor) を台帳として渡し、別物への同名使用と同一物の別名化を防ぐ
- サーバーが引いた発想カード (tools/scenario_decks.py) を必須の制約として渡す
- 遺跡の部屋は、AI に聞く前にサーバーが全区画の役割を割り当てる (並列ジョブ間で中目標や空室の配分がぶれない)

辞書やリストはすべて JSON 文字列にしてからテンプレートに埋め込む (R4: Python の repr を混ぜない)。
"""

import json
import random
from typing import Dict, Iterable, List, Optional

from tools.prompt_manager import load_cached_prompt, scene_mode_label
from tools.scenario_decks import format_cards

STAGE_LABELS = {
    "region": "地域",
    "settlement": "集落",
    "wilderness": "旅路",
    "site": "遺跡・迷宮",
    "site_lore": "遺跡・迷宮",
}

STAGE_TEMPLATES = {
    "region": "builder_region_prompt.txt",
    "settlement": "builder_settlement_prompt.txt",
    "wilderness": "builder_wilderness_prompt.txt",
    "site_lore": "builder_site_lore_prompt.txt",
}

# プロンプトに入れる文章の上限 (文字数)。長いジャーナルでプロンプトが膨らまないようにする
MAX_TEXT = 1500
MAX_REFERENCES = 4
MAX_ATLAS_INDEX = 20
MAX_NAMES = 80


def clip(text, limit: int = MAX_TEXT) -> str:
    text = text if isinstance(text, str) else to_prompt_text(text)
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…(以下略)"


def to_prompt_text(value) -> str:
    """プロンプトに埋め込む文字列にする。dict / list は JSON (R4)"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _str_list(value) -> List[str]:
    if isinstance(value, str):
        value = [line for line in value.splitlines()]
    if not isinstance(value, list):
        return []
    return [str(v).strip().lstrip("・-").strip() for v in value if str(v).strip()]


def name_ledger(context: Dict) -> List[str]:
    """既出の固有名詞 (上位段階・地図帳エントリ・Actor) を重複なしで並べる"""
    names: List[str] = []
    for parent in context.get("parents") or []:
        names.append(parent.get("name"))
    for entry in context.get("atlas_index") or []:
        names.append(entry.get("name"))
    names.extend(context.get("actor_names") or [])
    seen = set()
    result = []
    for n in names:
        n = (n or "").strip() if isinstance(n, str) else ""
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result[:MAX_NAMES]


def build_context_block(context: Dict, cards: Optional[List[Dict]] = None, world_fallback: Optional[Dict] = None) -> str:
    """
    全段階で共通の文脈ブロック。優先順位の高い順に並べる。
    context は FVTT から届く builder_generate_stage / builder_build_scene の本体。
    """
    world = context.get("world") or {}
    fallback = world_fallback or {}
    world_title = world.get("title") or fallback.get("title") or "(未設定)"
    world_desc = world.get("description") or fallback.get("description") or "(未設定)"

    lines = ["【ワールド資料】(最優先の設定。矛盾禁止)", f"ワールド名: {world_title}", f"説明: {clip(world_desc)}"]
    refs = [r for r in (world.get("references") or []) if isinstance(r, dict)][:MAX_REFERENCES]
    for ref in refs:
        lines.append(f"- 参照ジャーナル『{ref.get('name', '')}』: {clip(ref.get('text'))}")

    parents = [p for p in (context.get("parents") or []) if isinstance(p, dict)]
    lines.append("")
    lines.append("【上位段階】")
    if not parents:
        lines.append("(なし。この段階が最初)")
    canon_lines: List[str] = []
    thread_lines: List[str] = []
    for p in parents:
        label = STAGE_LABELS.get(p.get("category"), p.get("category") or "")
        name = p.get("name", "")
        lines.append(f"- {label}『{name}』")
        if p.get("public_lore"):
            lines.append(f"  公開情報: {clip(p.get('public_lore'))}")
        if p.get("dm_secrets"):
            lines.append(f"  DM 情報: {clip(p.get('dm_secrets'))}")
        canon_lines += [f"- [{label}『{name}』] {c}" for c in _str_list(p.get("canon"))]
        thread_lines += [f"- [{label}『{name}』] {t}" for t in _str_list(p.get("open_threads"))]

    lines += ["", "【確定事項】(変更禁止)"] + (canon_lines or ["(なし)"])
    lines += ["", "【未回収の伏線】"] + (thread_lines or ["(なし)"])

    names = name_ledger(context)
    lines += ["", "【固有名詞台帳】(既出の名前。同じものを指すときだけ使う)", "、".join(names) if names else "(なし)"]

    index = [e for e in (context.get("atlas_index") or []) if isinstance(e, dict)][:MAX_ATLAS_INDEX]
    lines += ["", "【既存の地図帳エントリ】(主題や構図を繰り返さない)"]
    if index:
        for e in index:
            label = STAGE_LABELS.get(e.get("category"), e.get("category") or "")
            summary = " / ".join(_str_list(e.get("canon"))[:3])
            lines.append(f"- {label}『{e.get('name', '')}』" + (f": {clip(summary, 200)}" if summary else ""))
    else:
        lines.append("(なし)")

    lines += ["", "【GM指示】", (context.get("user_prompt") or "").strip() or "(なし)"]
    fixed_name = (context.get("fixed_name") or "").strip() if isinstance(context.get("fixed_name"), str) else ""
    if fixed_name:
        lines.append(f"名前は『{fixed_name}』にする (GM が決めた名前)")
    if cards is not None:
        lines += ["", "【発想カード】(今回の抽選。すべて使う)", format_cards(cards)]
    return "\n".join(lines)


def format_actors(actors_context) -> Dict[str, str]:
    """FVTT で選んだ PC / NPC を箇条書きにする"""
    actors_context = actors_context if isinstance(actors_context, dict) else {}
    pcs = [a for a in actors_context.get("pcs") or [] if isinstance(a, dict)]
    npcs = [a for a in actors_context.get("npcs") or [] if isinstance(a, dict)]
    pc_text = "\n".join(
        f"- {a.get('name', '')} ({a.get('info', '') or '経歴未設定'}): {clip(a.get('bio', ''), 350)}" for a in pcs
    ) or "(指定なし。特定の PC に依存しない導入にする)"
    npc_text = "\n".join(
        f"- {a.get('name', '')} ({a.get('role', '') or 'NPC'}): {clip(a.get('bio', ''), 350)}" for a in npcs
    ) or "(指定なし)"
    return {"pc_profiles": pc_text, "npc_profiles": npc_text}


def compact_topology(topologies: Iterable[Dict]) -> List[Dict]:
    return [
        {
            "id": t["id"],
            "deg": t["deg"],
            "depth": t["depth"],
            "start": t["start"],
            "goal": t["goal"],
            "in_cycle": t.get("in_cycle", False),
            "neighbors": t.get("neighbors", []),
            "area": t["area"],
        }
        for t in topologies
    ]


def build_stage_prompt(
    stage: str,
    context: Dict,
    cards: List[Dict],
    world_fallback: Optional[Dict] = None,
    topologies: Optional[List[Dict]] = None,
    light_presets: Optional[List[str]] = None,
    size_name: str = "小",
    room_count: int = 15,
) -> str:
    """builder_generate_stage の各段階のプロンプト"""
    template = load_cached_prompt(STAGE_TEMPLATES[stage])
    values = {
        "context_block": build_context_block(context, cards, world_fallback),
        "rules": load_cached_prompt("builder_rules.txt"),
    }
    if stage == "settlement":
        values["scale"] = context.get("settlement_scale") or "辺境の小村"
    elif stage == "wilderness":
        values["topology_json"] = json.dumps(compact_topology(topologies or []), ensure_ascii=False)
        values["light_presets"] = ", ".join(light_presets or [])
    elif stage == "site_lore":
        values.update(format_actors(context.get("actors_context")))
        values["site_mode_label"] = scene_mode_label(context.get("site_mode") or "ruins")
        values["size_name"] = size_name
        values["room_count"] = room_count
    return template.format(**values)


# -------------------------------------------------------------------------
# 遺跡の区画の役割の割り当て
# -------------------------------------------------------------------------
PLAN_LABELS = {
    "start": "入口 (侵入経路と退路。大目標を予感させる)",
    "goal": "最深部 (大目標の場所)",
    "sub_goal": "中目標「{title}」の場所",
    "rest": "安全に休める区画 (戦闘なし)",
    "empty": "空室・痕跡のみ (戦闘なし。隣の区画の気配で予兆を出す)",
    "encounter": "遭遇 (勢力の誰かがいる。交渉や回避の余地も)",
    "hazard": "罠か環境の危険",
    "treasure": "隠された財宝か補給",
    "clue": "真相の手がかり",
}
# 戦闘のない区画 (空室・休憩) の割合
QUIET_RATIO = 0.3


def _pick_by_depth(candidates: List[Dict], target_depth: float) -> Optional[Dict]:
    if not candidates:
        return None
    return min(candidates, key=lambda t: (abs(t["depth"] - target_depth), t["id"]))


def plan_site_rooms(topologies: List[Dict], sub_objectives: Optional[List[Dict]], rng: random.Random) -> Dict[int, Dict]:
    """
    区画ごとの役割を決める。返り値は id -> {"role", "label", "sub_index"(中目標のみ)}。
    - 入口と最深部は固定
    - 中目標は target_topology が hub ならハブ (deg>=3)、それ以外は袋小路 (deg=1) を優先し、
      深さが入口から最深部までに散らばるように置く
    - 残りの約 3 割を空室・休憩にし、残りを袋小路 = 財宝/罠、ハブ = 遭遇 のように地形から割り振る
    """
    plan: Dict[int, Dict] = {}
    if not topologies:
        return plan
    max_depth = max((t["depth"] for t in topologies), default=0) or 1

    for t in topologies:
        if t.get("start"):
            plan[t["id"]] = {"role": "start", "label": PLAN_LABELS["start"]}
        elif t.get("goal"):
            plan[t["id"]] = {"role": "goal", "label": PLAN_LABELS["goal"]}

    def free(pred=lambda t: True) -> List[Dict]:
        return [t for t in topologies if t["id"] not in plan and pred(t)]

    subs = [s for s in (sub_objectives or []) if isinstance(s, dict)][:3]
    for i, sub in enumerate(subs):
        target = max_depth * (i + 1) / (len(subs) + 1)
        wants_hub = "hub" in str(sub.get("target_topology", "")).lower() or "ハブ" in str(sub.get("target_topology", ""))
        first = free(lambda t: t["deg"] >= 3) if wants_hub else free(lambda t: t["deg"] == 1)
        second = free(lambda t: t["deg"] == 1) if wants_hub else free(lambda t: t["deg"] >= 3)
        room = _pick_by_depth(first, target) or _pick_by_depth(second, target) or _pick_by_depth(free(), target)
        if room is None:
            break
        title = str(sub.get("title") or f"中目標{i + 1}")
        plan[room["id"]] = {"role": "sub_goal", "label": PLAN_LABELS["sub_goal"].format(title=title), "sub_index": i}

    remaining = free()
    quiet_count = round(len(remaining) * QUIET_RATIO)
    if quiet_count:
        rest = _pick_by_depth(free(lambda t: t["deg"] == 2) or remaining, max_depth / 2)
        plan[rest["id"]] = {"role": "rest", "label": PLAN_LABELS["rest"]}
        pool = free(lambda t: t["deg"] == 2) or free()
        rng.shuffle(pool)
        for t in pool[:quiet_count - 1]:
            plan[t["id"]] = {"role": "empty", "label": PLAN_LABELS["empty"]}

    dead_end_roles = ["treasure", "hazard"]
    rng.shuffle(dead_end_roles)
    dead_end_count = 0
    for t in sorted(free(), key=lambda t: t["id"]):
        if t["deg"] == 1:
            role = dead_end_roles[dead_end_count % 2]
            dead_end_count += 1
        elif t["deg"] >= 3:
            role = "encounter"
        elif t.get("in_cycle"):
            role = rng.choice(["encounter", "clue"])
        else:
            role = rng.choice(["encounter", "clue", "hazard"])
        plan[t["id"]] = {"role": role, "label": PLAN_LABELS[role]}
    return plan


def format_plan_overview(plan: Dict[int, Dict]) -> str:
    return "\n".join(f"- 区画 {rid}: {p['label']}" for rid, p in sorted(plan.items()))


def build_site_rooms_prompt(
    site_name: str,
    scene_mode: str,
    context: Dict,
    site_public: str,
    site_dm: str,
    chunk: List[Dict],
    plan: Dict[int, Dict],
    light_presets: List[str],
    world_fallback: Optional[Dict] = None,
) -> str:
    """遺跡の区画 (1 ジョブ分) の情景プロンプト"""
    rooms = []
    for t in compact_topology(chunk):
        rooms.append({**t, "plan": plan.get(t["id"], {}).get("label", PLAN_LABELS["empty"])})
    return load_cached_prompt("builder_site_rooms_prompt.txt").format(
        site_name=site_name,
        site_mode_label=scene_mode_label(scene_mode),
        context_block=build_context_block(context, None, world_fallback),
        site_public=clip(site_public, 2500) or "(なし)",
        site_dm=clip(site_dm, 2500) or "(なし)",
        room_plan_overview=format_plan_overview(plan),
        rooms_json=json.dumps(rooms, ensure_ascii=False),
        light_presets=", ".join(light_presets),
        rules=load_cached_prompt("builder_rules.txt"),
    )
