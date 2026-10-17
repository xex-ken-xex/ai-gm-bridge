"""
4 段階シナリオビルダー (FVTT のビルダー画面から届く event)

- builder_generate_stage: 地域 / 集落 / 旅路 / 遺跡の前提 の下書きを AI に作らせ、builder_stage_result で返す
  集落と旅路は 1 回で書かせると応答が長くタイムアウトしやすいため、骨組み → 細部 の 2 回に分ける
  (旅路の地点の細部は遺跡の区画と同じく数地点ずつ並列に問い合わせる)
- builder_build_scene: 旅路または遺跡のシーンを構築する (create_dungeon_scene)

旅路の地点グラフは seed から再生成できるので、下書きと一緒に seed を返し、FVTT 側が地図帳ジャーナルの
flag に保存する (L11: ブラウザのリロードでランドマークが消えない)。
"""

import asyncio
import random
from typing import Dict, List, Optional

from gm_server import protocol
from gm_server.config import SPEAKER_SUPPORT
from gm_server.deadline import run_with_deadline
from gm_server.dungeon_scene import (
    MAX_ROOMS_FOR_AI,
    ROOMS_PER_JOB,
    SIZE_PRESETS,
    WILDERNESS_POINT_COUNT,
    _normalize_chunk_ids,
    build_map,
    llm_step_limit,
    normalize_scene_mode,
    query_room_flavors,
    scene_mode_label,
    send_scene,
)
from gm_server.progress import log
from tools.dungeon_generator import LIGHT_PRESET_KEYS, DungeonGenerator
from tools.prompt_manager import get_active_world_info, prioritize_key_topologies
from tools.scenario_decks import draw_cards
from tools.scenario_prompts import (
    STAGE_TEMPLATES,
    build_settlement_secrets_prompt,
    build_site_rooms_prompt,
    build_stage_prompt,
    build_wilderness_landmarks_prompt,
    plan_site_rooms,
)

# stage -> (表示名, 名前のキー)
STAGE_INFO = {
    "region": ("地域", "region_name"),
    "settlement": ("集落", "settlement_name"),
    "wilderness": ("旅路", "wilderness_title"),
    "site_lore": ("遺跡の前提", "site_name"),
}

# ビルダーで選べるマップ規模 (M12: grid 100px 固定のため、大・特大はシーンが巨大になりすぎる)
BUILDER_SIZE_KEYS = ("small", "medium")
# 旅路の地点グラフの生成条件 (seed と合わせて再現に使う)
WILDERNESS_MAP = {"size_key": "small", "density": 0.4, "loop_ratio": 0.3, "points": WILDERNESS_POINT_COUNT}

LIST_KEYS = ("canon", "open_threads", "resolved_threads", "design_notes")


def new_seed() -> int:
    return random.randint(1, 99_999_999)


def parse_seed(value) -> Optional[int]:
    try:
        seed = int(value)
    except (TypeError, ValueError):
        return None
    return seed if seed > 0 else None


def builder_size_key(value) -> str:
    return value if value in BUILDER_SIZE_KEYS else BUILDER_SIZE_KEYS[0]


def _clamp(value, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _str_list(value) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def normalize_stage_result(stage: str, result) -> Optional[Dict]:
    """AI の下書きの形をそろえる。使えない形 (名前や公開情報が無い) なら None"""
    if not isinstance(result, dict) or stage not in STAGE_INFO:
        return None
    name_key = STAGE_INFO[stage][1]
    if not str(result.get(name_key) or "").strip() or not isinstance(result.get("public_lore"), dict):
        return None
    result = dict(result)
    if not isinstance(result.get("dm_secrets"), dict):
        result["dm_secrets"] = {}
    for key in LIST_KEYS:
        result[key] = _str_list(result.get(key))
    notes = result.get("consistency_notes")
    result["consistency_notes"] = notes.strip() if isinstance(notes, str) else ""
    return result


def normalize_landmarks(landmarks, topologies: List[Dict]) -> List[Dict]:
    """旅路のランドマークの id を地点グラフ (0 始まり) にそろえ、グラフに無い地点を捨てる"""
    items = [lm for lm in landmarks if isinstance(lm, dict)] if isinstance(landmarks, list) else []
    by_id = {lm["id"]: lm for lm in _normalize_chunk_ids(items, [t["id"] for t in topologies])}
    result = []
    for t in topologies:
        lm = by_id.get(t["id"])
        if lm is None:
            continue
        role = "start" if t.get("start") else ("goal" if t.get("goal") else "waypoint")
        result.append({**lm, "role": role, "is_empty": False, "safe_rest": bool(lm.get("safe_rest"))})
    return result


def apply_room_plan(flavors: Optional[List[Dict]], plan: Dict[int, Dict]) -> List[Dict]:
    """
    サーバーが決めた役割を部屋データに反映する (AI の書いた role より優先)。
    AI の情景が無い中目標の部屋も、バッジと GM メモだけは付ける。
    """
    by_id = {f["id"]: dict(f) for f in flavors or []}
    for rid, p in plan.items():
        role = p["role"]
        item = by_id.get(rid)
        if item is None:
            if role not in ("sub_goal", "goal"):
                continue
            item = {"id": rid, "title": p["label"], "gm_notes": p["label"]}
        item["role"] = role
        item["is_empty"] = role in ("empty", "rest")
        item["safe_rest"] = role in ("rest", "start")
        by_id[rid] = item
    return [by_id[k] for k in sorted(by_id)]


def make_wilderness_generator(seed: int) -> DungeonGenerator:
    w, h, _, _ = SIZE_PRESETS[WILDERNESS_MAP["size_key"]]
    gen = DungeonGenerator(
        width=w, height=h, density=WILDERNESS_MAP["density"], loop_ratio=WILDERNESS_MAP["loop_ratio"],
        seed=seed, scene_mode="wilderness",
    )
    gen.generate(target_room_count=WILDERNESS_MAP["points"])
    return gen


# -------------------------------------------------------------------------
# AI 応答が得られなかったときの下書き (GM が手直しする前提の最小限の骨組み)
# -------------------------------------------------------------------------
def fallback_stage_result(stage: str) -> Dict:
    base = {"open_threads": [], "resolved_threads": [], "design_notes": [], "consistency_notes": ""}
    if stage == "region":
        return {
            **base,
            "region_name": "灰石の高原",
            "naming_style": "硬い子音の短い二語 (例: グラン・ドル、ヘス・ガルド)",
            "public_lore": {
                "terrain_and_climate": "石灰岩の高原と針葉樹の疎林。冬は長く、夏の短い時期だけ谷で麦が育つ。",
                "natural_resources": [
                    {"name": "黒鉄鉱", "location": "北の古い露頭", "usage": "鍛冶と交易"},
                    {"name": "香木", "location": "高地の斜面林", "usage": "燻製と薬"},
                ],
                "history_layers": [
                    {"era": "狼煙の時代", "what_happened": "王国が尾根に見張り塔を並べた", "what_remains": "崩れた塔の土台"},
                    {"era": "空白の百年", "what_happened": "戦乱で塔が放棄された", "what_remains": "誰も直さない道標"},
                ],
                "travel_impression": "乾いた風に香木の煙が混じり、尾根の上に石積みの影が並ぶ。",
            },
            "dm_secrets": {
                "hidden_threat": "放棄された塔の一つに武装した一団が住み着いている。",
                "faction_tensions": "塔の一団と高地の獣たちが縄張りを争っている。",
                "ancient_secret": "塔どうしは地下の連絡路でつながっていた。",
            },
            "audio_prompts": {"region_ambience_en": "Dry wind over rocky highlands, sparse pine forest, seamless loop"},
            "canon": ["命名の作法: 硬い子音の短い二語", "資源: 黒鉄鉱は北の露頭で採れる", "歴史: 尾根の見張り塔は戦乱で放棄された"],
        }
    if stage == "settlement":
        return {
            **base,
            "settlement_name": "宿場村グレン",
            "public_lore": {
                "architecture_and_scenery": "風を避けるため地元の石を低く積んだ家々。煙突から香木の煙が上がる。",
                "primary_industries": "高地の牧畜と、斜面林での香木の採取。",
                "secondary_industries": "香木で燻した保存肉と薬草酒。",
                "micro_craft": {"item_name": "香木の鈴", "description": "端材を削った小さな鈴。家ごとに刻みの模様が違う。", "made_from": "香木の端材"},
                "daily_life_and_food": "夕方には炉端に集まり、燻製肉と根菜の煮込みを食べる。",
                "local_joys": "季節の交易市と、炉端での弦楽器の合奏。",
                "visible_sorrows": "街道の先の塔の近くで荷車が戻らなくなった。",
                "key_npcs": [{"name": "工房主エドガー", "role": "職人頭", "personality": "実直で寡黙", "want": "交易路を取り戻したい"}],
            },
            "dm_secrets": {
                "sorrow_truth": "塔の一団が荷を奪い、村の商人の一部が裏で物資を流している。",
                "micro_craft_hook": "奪われた荷の跡に香木の鈴が落ちており、刻みの模様で持ち主の家が分かる。",
                "npc_hidden_agenda": "商人の一人が報復を恐れて一団と取引している。",
            },
            "audio_prompts": {
                "joy_bgm_en": "Warm medieval lute and fiddle in a cozy tavern, crackling fireplace",
                "sorrow_bgm_en": "Melancholic solo cello and soft flute at night, slow ambient",
            },
            "canon": ["集落: グレンは街道沿いの宿場村", "手仕事: 香木の鈴は家ごとに刻みが違う", "人物: エドガーは職人頭"],
        }
    if stage == "wilderness":
        return {
            **base,
            "wilderness_title": "塔へ続く旧街道",
            "public_lore": {
                "route_overview": "村から尾根へ登る石畳の旧街道と、森を抜ける獣道の迂回路。",
                "weather_table": ["冷たい通り雨", "乾いた強風", "雲間から差す淡い陽光"],
            },
            "dm_secrets": {
                "stalking_threat": "本道は塔の一団の見張りが監視している。野営すると夜半に偵察が来る。",
                "shortcut_secret": "獣道の途中で香木の鈴を見つけると、塔の崩れた裏口の場所が分かる。",
            },
            "landmarks": [],
            "canon": ["旅路: 本道は見張られている", "旅路: 獣道は塔の裏口に通じる"],
        }
    return {
        **base,
        "site_name": "尾根の見張り塔",
        "mission_archetype": "奪われたものを取り返す",
        "era": "狼煙の時代",
        "original_purpose": "見張り塔",
        "public_lore": {
            "exterior_and_history": "尾根に立つ古い見張り塔の廃墟。外壁の多くが崩れ、内と外が地続きになっている。",
            "clear_main_goal": "塔に運び込まれた村の荷を取り返し、一団を追い払う。",
            "pc_narrative_hook": "入口の石畳に、見覚えのある香木の鈴の破片と新しい轍が残っている。",
            "sensory_ambience": {"visual": "苔むした柱と篝火の影", "auditory": "崩れた壁を鳴らす風", "olfactory": "湿った土と焦げた油", "air": "高地の冷気"},
        },
        "dm_secrets": {
            "true_situation": "一団の頭目は奪った物資で冬を越すつもりだが、配下は待遇に不満を抱いている。",
            "sub_objectives": [
                {"title": "裏口の閂を外す", "target_topology": "dead_end", "connection_to_town_or_pc": "香木の鈴の持ち主が裏口を知っていた", "effect": "最深部へ見張りに気付かれず入れる"},
                {"title": "配下と取引する", "target_topology": "hub", "connection_to_town_or_pc": "村の商人の裏取引の証拠", "effect": "最深部の護衛が半分になる"},
            ],
            "catharsis_payoff": "裏口から入り、取引で寝返った配下が頭目を見捨てる。孤立した頭目は荷を差し出して降伏する。",
            "factions": [
                {"name": "塔の一団", "territory": "浅層〜中層", "status": "冬を越すために奪っているが、士気は低い"},
                {"name": "頭目と側近", "territory": "最深部", "status": "物資を独り占めしている"},
            ],
        },
        "audio_prompts": {
            "exploration_bgm_en": "Tense low strings and wind, ruined tower exploration",
            "catharsis_boss_bgm_en": "Triumphant drums and brass, climactic battle",
        },
        "canon": ["遺跡: 見張り塔は狼煙の時代のもの", "遺跡: 頭目と配下は不仲"],
    }


# -------------------------------------------------------------------------
# builder_generate_stage
# -------------------------------------------------------------------------
async def query_stage(ctx, label: str, prompt: str):
    """AI に 1 回問い合わせる。打ち切り・失敗なら None"""
    try:
        return await run_with_deadline(ctx, label, ctx.llm.query_json(prompt, unbounded=True), limit=llm_step_limit(ctx))
    except asyncio.TimeoutError:
        log(f"[{label}] 打ち切りました。")
    except Exception as e:
        log(f"[{label}] 生成に失敗しました ({e})")
    return None


def _merge_unique(*lists) -> List[str]:
    result: List[str] = []
    for items in lists:
        for item in _str_list(items):
            if item not in result:
                result.append(item)
    return result


def merge_settlement(core: Dict, extra) -> Dict:
    """集落の骨組みに 2 回目の応答 (人柄・隠し事・BGM・伏線) を足す。骨組みの内容は上書きしない"""
    result = dict(core)
    extra = extra if isinstance(extra, dict) else {}
    public = dict(result.get("public_lore") or {})
    details = {
        str(n.get("name")): n for n in extra.get("key_npcs") or [] if isinstance(n, dict) and n.get("name")
    }
    npcs = []
    for npc in public.get("key_npcs") or []:
        if isinstance(npc, dict):
            more = details.get(str(npc.get("name")), {})
            npc = {**npc, **({"personality": more["personality"]} if more.get("personality") else {})}
        npcs.append(npc)
    public["key_npcs"] = npcs
    result["public_lore"] = public
    dm = dict(result.get("dm_secrets") or {}) if isinstance(result.get("dm_secrets"), dict) else {}
    extra_dm = extra.get("dm_secrets") if isinstance(extra.get("dm_secrets"), dict) else {}
    result["dm_secrets"] = {**extra_dm, **dm}
    if isinstance(extra.get("audio_prompts"), dict):
        result["audio_prompts"] = extra["audio_prompts"]
    result["canon"] = _merge_unique(result.get("canon"), extra.get("canon"))
    result["open_threads"] = _merge_unique(result.get("open_threads"), extra.get("open_threads"))
    return result


async def generate_settlement(ctx, data: dict, label: str, core_prompt: str):
    """集落: 骨組み (表の情報・人物の顔ぶれ・真相の要点) → 人柄・隠し事・BGM。(応答, 一部欠けたか) を返す"""
    core = await query_stage(ctx, f"{label} 1/2 骨組み", core_prompt)
    if normalize_stage_result("settlement", core) is None:
        return core, False
    prompt = build_settlement_secrets_prompt(data, core, world_fallback=get_active_world_info())
    extra = await query_stage(ctx, f"{label} 2/2 人物と秘密", prompt)
    return merge_settlement(core, extra), not isinstance(extra, dict)


def landmark_plan(core: Dict, topologies: List[Dict]) -> Dict[int, Dict]:
    """旅路の骨組みの landmark_plan を地点グラフの id (0 始まり) にそろえる"""
    items = [p for p in core.get("landmark_plan") or [] if isinstance(p, dict)]
    return {p["id"]: p for p in _normalize_chunk_ids(items, [t["id"] for t in topologies])}


def merge_landmarks(plan: Dict[int, Dict], details: Optional[List[Dict]]) -> List[Dict]:
    """骨組みの名前・役割と、地点ごとの細部を合わせる。細部が無い地点は役割を GM メモにする"""
    by_id = {d["id"]: d for d in details or []}
    result = []
    for pid, p in sorted(plan.items()):
        d = by_id.get(pid, {})
        item = {**d, "id": pid, "title": p.get("title") or d.get("title") or f"地点 {pid + 1}",
                "safe_rest": bool(p.get("safe_rest"))}
        if not item.get("gm_notes"):
            item["gm_notes"] = p.get("purpose", "")
        result.append(item)
    return result


async def generate_wilderness(ctx, data: dict, label: str, core_prompt: str, topologies: List[Dict]):
    """旅路: 骨組み (全体像・秘密・各地点の名前と役割) → 地点ごとの読み上げと GM メモ (並列)"""
    core = await query_stage(ctx, f"{label} 1/2 骨組み", core_prompt)
    if normalize_stage_result("wilderness", core) is None:
        return core, False
    plan = landmark_plan(core, topologies)
    if not plan:
        # 骨組みに地点の一覧が無い (1 回で landmarks まで書いてきた場合はそれを使う)
        return core, not core.get("landmarks")
    world = get_active_world_info()
    presets = list(LIGHT_PRESET_KEYS)

    def prompt_for_chunk(chunk: List[Dict]) -> str:
        return build_wilderness_landmarks_prompt(data, core, plan, chunk, presets, world_fallback=world)

    targets = [t for t in topologies if t["id"] in plan]
    details = await query_room_flavors(
        ctx, None, targets, "wilderness", prompt_for_chunk=prompt_for_chunk, label=f"{label} 2/2 地点の描写",
    )
    result = {k: v for k, v in core.items() if k != "landmark_plan"}
    result["landmarks"] = merge_landmarks(plan, details)
    got = {d["id"] for d in details or []}
    return result, not set(plan) <= got


async def handle_generate_stage(ctx, data: dict) -> None:
    stage = data.get("stage")
    if stage not in STAGE_TEMPLATES:
        log(f"未対応のビルダー段階です: {stage}")
        return
    stage_label = STAGE_INFO[stage][0]
    seed = parse_seed(data.get("seed")) or new_seed()
    rng = random.Random(seed)
    cards = draw_cards(stage, rng, data.get("recent_cards"))
    print(f"\n[シナリオビルダー] {stage_label} の下書きを生成 (seed={seed}, カード: {' / '.join(c['text'] for c in cards)})")

    map_info = None
    topologies = None
    if stage == "wilderness":
        gen = await asyncio.to_thread(make_wilderness_generator, seed)
        topologies = gen.get_room_topologies()
        map_info = {"seed": seed, **WILDERNESS_MAP}

    size_key = builder_size_key(data.get("size_key"))
    prompt = build_stage_prompt(
        stage, data, cards,
        world_fallback=get_active_world_info(),
        topologies=topologies,
        light_presets=list(LIGHT_PRESET_KEYS),
        size_name=SIZE_PRESETS[size_key][2],
        room_count=MAX_ROOMS_FOR_AI,
    )

    label = f"ビルダー: {stage_label}"
    partial = False
    if stage == "settlement":
        raw, partial = await generate_settlement(ctx, data, label, prompt)
    elif stage == "wilderness":
        raw, partial = await generate_wilderness(ctx, data, label, prompt, topologies)
    else:
        raw = await query_stage(ctx, label, prompt)

    result = normalize_stage_result(stage, raw)
    fallback = result is None
    if fallback:
        result = fallback_stage_result(stage)
        await ctx.send(protocol.chat(
            f"⚠️ {stage_label}の下書きを AI から受け取れなかったため、手直し用の骨組みを入れました。",
            speaker=SPEAKER_SUPPORT,
        ))
    elif partial:
        await ctx.send(protocol.chat(
            f"⚠️ {stage_label}の細部の一部を AI から受け取れませんでした。骨組みは届いているので、空いた所を手直ししてください。",
            speaker=SPEAKER_SUPPORT,
        ))
    if stage == "wilderness":
        result["landmarks"] = normalize_landmarks(result.get("landmarks"), topologies)

    await ctx.send(protocol.builder_stage_result(stage, result, cards=cards, seed=seed, fallback=fallback, map_info=map_info))
    print(f"[シナリオビルダー] {stage_label} の下書きを送信しました。")


# -------------------------------------------------------------------------
# builder_build_scene
# -------------------------------------------------------------------------
async def handle_build_scene(ctx, data: dict) -> None:
    scene_mode = normalize_scene_mode(data.get("scene_mode") or "ruins")
    mode_name = scene_mode_label(scene_mode)
    name = str(data.get("name") or "").strip() or ("旅路" if scene_mode == "wilderness" else "名もなき遺跡")
    site_public = str(data.get("public_lore") or "")
    site_dm = str(data.get("dm_secrets") or "")

    if scene_mode == "wilderness":
        map_info = data.get("map") if isinstance(data.get("map"), dict) else {}
        seed = parse_seed(map_info.get("seed"))
        landmarks = data.get("landmarks")
        if seed is None:
            # 下書きの地点グラフが分からないので、ランドマークは使えない
            seed, landmarks = new_seed(), []
            await ctx.send(protocol.chat(
                "⚠️ 旅路の地図情報 (seed) が無いため、新しい地形で構築します。ランドマークの下書きは使いません。",
                speaker=SPEAKER_SUPPORT,
            ))
        size_key = WILDERNESS_MAP["size_key"]
        density, loop_ratio = WILDERNESS_MAP["density"], WILDERNESS_MAP["loop_ratio"]
    else:
        seed = parse_seed(data.get("seed"))
        size_key = builder_size_key(data.get("size_key"))
        density = _clamp(data.get("density"), 0.5, 0.2, 1.0)
        loop_ratio = _clamp(data.get("loop_ratio"), 0.25, 0.0, 0.5)
    print(f"\n[シナリオビルダー] シーン構築: {name} ({mode_name}, {size_key}, seed={seed})")

    built = await build_map(ctx, size_key, density, loop_ratio, seed, scene_mode)
    if built is None:
        return
    gen, walls, png_filename = built
    topologies = gen.get_room_topologies()

    if scene_mode == "wilderness":
        custom = normalize_landmarks(landmarks, topologies)
    else:
        await ctx.send(protocol.chat(
            f"🏰 <b>{mode_name}の構造を算出しました</b> (区画数: {len(gen.rooms)})<br>各区画の役割を割り当て、情景を AI 補完中...",
            speaker=SPEAKER_SUPPORT,
        ))
        selected = prioritize_key_topologies(topologies, max_count=MAX_ROOMS_FOR_AI)
        sub_objectives = data.get("sub_objectives") if isinstance(data.get("sub_objectives"), list) else []
        plan = plan_site_rooms(selected, sub_objectives, random.Random(gen.seed))
        world_fallback = get_active_world_info()

        def prompt_for_chunk(chunk: List[Dict]) -> str:
            return build_site_rooms_prompt(
                name, scene_mode, data, site_public, site_dm, chunk, plan,
                list(LIGHT_PRESET_KEYS), world_fallback=world_fallback,
            )

        flavors = await query_room_flavors(ctx, None, selected, scene_mode, prompt_for_chunk=prompt_for_chunk)
        custom = apply_room_plan(flavors, plan)

    rooms = gen.get_room_flavor_notes(grid_size=100, custom_flavors=custom or None, id_base=0)
    lore = {
        "dungeon_name": name,
        "floor_title": f"{name} ({mode_name})",
        "history_summary": site_public,
        "floor_origin": "",
        "dm_secrets": site_dm,
    }
    speech_text = f"{mode_name}『{name}』のシーンを構築しました。全{len(gen.rooms)}区画に情景を設定しました。"
    await send_scene(ctx, name, scene_mode, png_filename, gen, walls, lore, rooms, density, speech_text)
    print("[シナリオビルダー] シーン構築指示を送信しました。")
