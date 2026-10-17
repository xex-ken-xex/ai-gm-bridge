"""ダンジョン自動生成ウィザードとシーン構築命令 (create_dungeon_scene)"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from gm_server import protocol
from gm_server.config import AGY_TIMEOUT, GEMINI_TIMEOUT, LLM_CONCURRENCY, MAPGEN_TIMEOUT, SPEAKER_SUPPORT
from gm_server.deadline import run_with_deadline
from gm_server.progress import log, log_detail, log_group, log_step
from gm_server.tts import voice_filename
from tools.dungeon_generator import LIGHT_PRESET_KEYS, SCENE_MODES, DungeonGenerator
from tools.prompt_manager import (
    build_lore_prompt,
    build_rooms_prompt,
    get_active_world_info,
    prioritize_key_topologies,
)

# size_key -> (幅, 高さ, 表示名, PNG 1マスあたりのピクセル数)
SIZE_PRESETS: Dict[str, Tuple[int, int, str, int]] = {
    "small": (100, 100, "小", 14),
    "medium": (500, 500, "中", 6),
    "large": (1000, 1000, "大", 3),
    "xlarge": (2000, 2000, "特大", 2),
}
DEFAULT_SIZE_KEY = "small"
DEFAULT_DENSITY = 0.5
DEFAULT_LOOP_RATIO = 0.2
DEFAULT_SCENE_MODE = "underground"

# scene_mode -> (表示名, シーン名の既定値)
SCENE_MODE_INFO: Dict[str, Tuple[str, str]] = {
    "underground": ("地下迷宮", "地下城塞"),
    "ruins": ("地上の廃墟", "廃墟"),
    "wilderness": ("野外", "旅路"),
}
# 野外は部屋数 = 道中の地点数。密度ではなく固定数で生成する
WILDERNESS_POINT_COUNT = 9

# FVTT のパフォーマンス保護のための壁本数上限
MAX_WALLS = 3000
MAX_ROOMS_FOR_AI = 15
# 部屋の情景は 1 回の問い合わせを小さくするため、この部屋数ずつに分けて並列に問い合わせる
ROOMS_PER_JOB = 5

# GM が明示的にテーマを指定していないとみなすプロンプト
GENERIC_DUNGEON_PROMPTS = ("ダンジョン", "ダンジョン生成", "マップ", "迷宮")

_NPC_OR_DIALOGUE_KEYWORDS = ["npc", "キャラクター", "敵", "モンスター", "会話", "セリフ", "ロールプレイ", "動か", "操作", "判定", "ダイス", "ルール"]
_DUNGEON_KEYWORDS = ["ダンジョン", "マップ", "迷宮"]
_STRUCTURE_KEYWORDS = ["遺跡", "城塞", "寺院", "洞窟", "要塞", "塔", "地下"]
_CREATE_KEYWORDS = ["生成", "作っ", "作成", "ビルド"]


def is_dungeon_intent(prompt: str) -> bool:
    """
    /ai の指示がダンジョン生成の意図かどうか。
    NPCや会話指示など他の指示が含まれる場合は通常のサポートGMへルーティングする。
    """
    if not prompt:
        return True
    lowered = prompt.lower()
    is_npc_or_dialogue = any(k in lowered for k in _NPC_OR_DIALOGUE_KEYWORDS)
    dungeon_explicit = any(k in prompt for k in _DUNGEON_KEYWORDS)
    structure_create = any(k in prompt for k in _STRUCTURE_KEYWORDS) and any(k in prompt for k in _CREATE_KEYWORDS)
    return (dungeon_explicit or structure_create) and not is_npc_or_dialogue


# -------------------------------------------------------------------------
# ウィザードの選択肢カード (モード → サイズ → 部屋密度 → ループ率)
# 野外モードは部屋密度を使わないためサイズの次にループ率へ進む
# -------------------------------------------------------------------------
def mode_card() -> Dict:
    return protocol.interactive_card(
        title="ダンジョン自動生成",
        message="サポートGM アギィ です。生成する舞台の種類を選択してください：",
        choices=[
            {"label": "地下迷宮 (石壁・扉・隠し扉)", "value": "dungeon_mode:underground"},
            {"label": "地上の廃墟 (崩れた壁・瓦礫越しに見通せる)", "value": "dungeon_mode:ruins"},
            {"label": f"野外 (壁なし・{WILDERNESS_POINT_COUNT}地点前後の旅路)", "value": "dungeon_mode:wilderness"},
        ],
    )


def size_card(mode_label: str = "") -> Dict:
    lead = f"舞台「{mode_label}」を承りました。" if mode_label else "サポートGM アギィ です。"
    return protocol.interactive_card(
        title="ダンジョン自動生成",
        message=f"{lead}生成するマップの規模を選択してください：",
        choices=[
            {"label": "小 (100x100マス)", "value": "dungeon_size:small"},
            {"label": "中 (500x500マス)", "value": "dungeon_size:medium"},
            {"label": "大 (1000x1000マス)", "value": "dungeon_size:large"},
            {"label": "特大 (2000x2000マス)", "value": "dungeon_size:xlarge"},
        ],
    )


def density_card(size_key: str, size_label: str) -> Dict:
    return protocol.interactive_card(
        title="ダンジョン構造 (部屋の密度)",
        message=f"サイズ「{size_label}」を承りました。部屋の密集度（部屋数）を選んでください：",
        choices=[
            {"label": "標準 (適度な部屋数 / 20部屋前後)", "value": f"dungeon_density:{size_key}:0.4"},
            {"label": "密集 (部屋多め / 35部屋前後)", "value": f"dungeon_density:{size_key}:0.7"},
            {"label": "ギッシリ (超高密度 / 50部屋前後)", "value": f"dungeon_density:{size_key}:1.0"},
        ],
    )


def loop_card(size_key: str, density: float, density_label: str, prev_kind: str = "部屋密度") -> Dict:
    return protocol.interactive_card(
        title="ダンジョン構造 (周回路)",
        message=f"{prev_kind}「{density_label}」を承りました。周回路（ループ・迂回ルート）の多さを選んでください：",
        choices=[
            {"label": "袋小路のみ (純木構造 / 0%)", "value": f"dungeon_loop:{size_key}:{density}:0.0"},
            {"label": "標準 (適度な周回路 / 20%)", "value": f"dungeon_loop:{size_key}:{density}:0.2"},
            {"label": "迷路多め (複雑な周回 / 40%)", "value": f"dungeon_loop:{size_key}:{density}:0.4"},
        ],
    )


@dataclass
class DungeonChoice:
    kind: str  # "mode" | "size" | "density" | "loop"
    size_key: str
    density: float = DEFAULT_DENSITY
    loop_ratio: float = DEFAULT_LOOP_RATIO
    scene_mode: str = DEFAULT_SCENE_MODE


def _to_float(value: str, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def parse_dungeon_choice(value: str) -> Optional[DungeonChoice]:
    """`dungeon_mode:ruins` / `dungeon_size:small` / `dungeon_density:small:0.4` / `dungeon_loop:small:0.4:0.2` を解釈"""
    if not value or not value.startswith("dungeon_"):
        return None
    parts = value.split(":")
    head = parts[0]
    if head == "dungeon_mode":
        mode = parts[1] if len(parts) >= 2 and parts[1] in SCENE_MODES else DEFAULT_SCENE_MODE
        return DungeonChoice(kind="mode", size_key=DEFAULT_SIZE_KEY, scene_mode=mode)
    size_key = parts[1] if len(parts) >= 2 and parts[1] in SIZE_PRESETS else DEFAULT_SIZE_KEY
    density = _to_float(parts[2], DEFAULT_DENSITY) if len(parts) >= 3 else DEFAULT_DENSITY
    loop_ratio = _to_float(parts[3], DEFAULT_LOOP_RATIO) if len(parts) >= 4 else DEFAULT_LOOP_RATIO
    kinds = {"dungeon_size": "size", "dungeon_density": "density", "dungeon_loop": "loop"}
    if head not in kinds:
        return None
    return DungeonChoice(kind=kinds[head], size_key=size_key, density=density, loop_ratio=loop_ratio)


def normalize_scene_mode(scene_mode: Optional[str]) -> str:
    return scene_mode if scene_mode in SCENE_MODES else DEFAULT_SCENE_MODE


def scene_mode_label(scene_mode: str) -> str:
    return SCENE_MODE_INFO[normalize_scene_mode(scene_mode)][0]


def density_label(density: float) -> str:
    return "標準" if density <= 0.5 else ("密集" if density <= 0.8 else "ギッシリ")


# -------------------------------------------------------------------------
# フォールバック伝承 (AI 応答が得られなかった場合)
# -------------------------------------------------------------------------
def fallback_lore(size_name: str) -> Dict:
    return {
        "dungeon_name": "黒水城塞 (Blackwater Bastion)",
        "floor_title": f"第1層: 酸蝕の外郭城塞 ({size_name})",
        "floor_origin": "古代の堅牢な要塞がブラックドラゴンの酸ブレスで崩壊し、現在はゴブリンキングの軍勢が占拠している。",
        "history_summary": "沼沢地の要衝として建造されたが、竜の襲来により壊滅。現在は竜とゴブリンが共生・支配する。",
        "factions": [
            {"name": "泥爪ゴブリン軍団", "territory": "外郭検問所および詰所", "status": "侵入者を警戒し略奪を企む"},
            {"name": "酸蝕の竜血信徒", "territory": "水路および深部通路", "status": "ブラックドラゴンに仕え酸の罠を設置"},
        ],
        "floor_ambience": {
            "visual": "酸で溶解した切石の壁面と黒苔、粗末な獣脂松明の赤い光",
            "auditory": "天井から滴る酸性水が石を焦がす音と遠くの獣の唸り声",
            "olfactory": "鼻腔を突く刺激的な硫黄臭と獣の体臭",
            "air": "生ぬるく肌にまとわりつく高湿度の酸性蒸気",
        },
    }


def select_walls(gen: DungeonGenerator, max_walls: int = MAX_WALLS) -> List[list]:
    """壁セグメントを抽出。上限超過または特大マップではパフォーマンス保護のため送らない"""
    if gen.width > 1000:
        return []
    walls = gen.extract_walls(grid_size=100)
    if len(walls) > max_walls:
        print(f"壁総数({len(walls)}本)が上限({max_walls}本)を超過したためパフォーマンス保護のためスキップ")
        return []
    return walls


def _normalize_chunk_ids(items: List[Dict], expected_ids: List[int]) -> List[Dict]:
    """
    1 ジョブ分の応答 id を 0 始まりにそろえ、そのジョブで頼んだ部屋だけ残す。
    AI が 1 始まりで返すこともあるため、0 / 1 始まりのどちらがより多く一致するかで判定する。
    """
    expected = set(expected_ids)
    ids = []
    for item in items:
        try:
            ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            ids.append(None)
    hits0 = sum(1 for i in ids if i in expected)
    hits1 = sum(1 for i in ids if i is not None and i - 1 in expected)
    offset = 1 if hits1 > hits0 else 0
    result = []
    for item, cid in zip(items, ids):
        if cid is not None and cid - offset in expected:
            result.append({**item, "id": cid - offset})
    return result


async def query_room_flavors(
    ctx,
    lore: Optional[Dict],
    topologies: List[Dict],
    scene_mode: str,
    prompt_for_chunk: Optional[Callable[[List[Dict]], str]] = None,
    label: str = "部屋の情景",
    on_status=None,
    auto_extend: bool = False,
    on_chunk: Optional[Callable[[List[Dict]], Awaitable[None]]] = None,
) -> Optional[List[Dict]]:
    """
    部屋の情景を ROOMS_PER_JOB 部屋ずつのジョブに分け、ctx.llm.concurrency 本まで並列に問い合わせる
    (Gemini は LLM_CONCURRENCY、agy は AGY_CONCURRENCY。agy の既定は 1 本ずつ)。
    返す id は 0 始まり。全ジョブ失敗なら None (呼び出し側は既定のノートを使う)。
    prompt_for_chunk を渡すと、1 ジョブ分の部屋からプロンプトを作る処理を差し替えられる (シナリオビルダー用)。
    label は進捗表示とログに使う名前 (旅路の地点にも使うため)。
    on_chunk を渡すと、ジョブが 1 つ終わるたびにその結果 (0 始まりの id) で呼ぶ (できた区画から順にシーンへ反映する)。
    """
    selected = prioritize_key_topologies(topologies, max_count=MAX_ROOMS_FOR_AI)
    if prompt_for_chunk is None:
        def prompt_for_chunk(chunk: List[Dict]) -> str:
            return build_rooms_prompt(
                lore, chunk, max_rooms=len(chunk),
                scene_mode=scene_mode, light_presets=list(LIGHT_PRESET_KEYS),
            )
    chunks = [selected[i:i + ROOMS_PER_JOB] for i in range(0, len(selected), ROOMS_PER_JOB)]
    if not chunks:
        return None
    concurrency = max(1, int(getattr(ctx.llm, "concurrency", LLM_CONCURRENCY)))
    sem = asyncio.Semaphore(concurrency)
    done = 0
    # 進捗とログに「区画 3・5・8 の情景を作成中」と出すため、今書いているジョブと書き終えた部屋数を数える
    unit, what = room_unit_of(label)
    total_rooms = len(selected)
    rooms_done = 0
    working: Dict[int, str] = {}
    # 打ち切りになっても、それまでに終わったジョブの結果は使う
    flavors: List[Dict] = []

    async def run(chunk: List[Dict]) -> None:
        nonlocal done, rooms_done
        names = room_numbers(chunk)
        async with sem:
            working[id(chunk)] = names
            log_detail(f"[{label}] {unit} {names} の{what}を作成中 ({total_rooms} {unit}中 {rooms_done} {unit}済み)")
            try:
                prompt = prompt_for_chunk(chunk)
                parsed = await ctx.llm.query_json(prompt, unbounded=True)
            except Exception as e:
                log_detail(f"[{label}] 生成失敗 ({e})。この部屋群は既定のノートを使用します。")
                parsed = None
            finally:
                done += 1
                rooms_done += len(chunk)
                working.pop(id(chunk), None)
        items = [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []
        got = _normalize_chunk_ids(items, [t["id"] for t in chunk])
        log_detail(f"[{label}] {unit} {names} の{what}を受け取りました ({len(got)}/{len(chunk)} 件)")
        flavors.extend(got)
        if on_chunk and got:
            try:
                await on_chunk(got)
            except Exception as e:
                log_detail(f"[{label}] 途中結果の反映に失敗しました ({e})")

    # タイムアウト: 1 ジョブの上限 × 並列で回す段数
    rounds = -(-len(chunks) // concurrency)
    try:
        await run_with_deadline(
            ctx, label, asyncio.gather(*(run(c) for c in chunks)),
            limit=llm_step_limit(ctx) * rounds, on_status=on_status,
            detail=lambda: progress_detail(unit, what, list(working.values()), rooms_done, total_rooms),
            auto_extend=auto_extend,
        )
    except asyncio.TimeoutError:
        log_step(f"{label}を打ち切りました (完了 {done}/{len(chunks)})。残りは既定のノートを使用します。")
    return sorted(flavors, key=lambda r: r["id"]) or None


def room_unit_of(label: str) -> Tuple[str, str]:
    """進捗の名前 (例: 「区画の情景」「旅路 2/2 地点の描写」) から単位と書く内容を取り出す → ("区画", "情景")"""
    unit, _, what = label.split()[-1].partition("の")
    return unit, what or "情景"


def room_numbers(chunk: List[Dict]) -> str:
    """ジョブの部屋番号 (マップのノートと同じ 1 始まり) を「3・5・8」の形にする"""
    return "・".join(str(t["id"] + 1) for t in chunk)


def progress_detail(unit: str, what: str, working: List[str], rooms_done: int, total_rooms: int) -> str:
    """進捗の行: 「区画 3・5・8 の情景を作成中 (12 区画中 4 区画済み)」"""
    head = " / ".join(f"{unit} {names}" for names in working)
    count = f"{total_rooms} {unit}中 {rooms_done} {unit}済み"
    return f"{head} の{what}を作成中 ({count})" if head else count


def llm_step_limit(ctx) -> float:
    """AI 問い合わせ 1 回の待ち上限 (Gemini があれば Gemini、無ければ agy の上限)"""
    return GEMINI_TIMEOUT if getattr(ctx.llm, "client", None) else AGY_TIMEOUT


def _build_geometry(w: int, h: int, density: float, loop_ratio: float, png_path: str, cell_size: int,
                    seed: Optional[int], scene_mode: str = DEFAULT_SCENE_MODE):
    """CPU 負荷の高い幾何生成と PNG 書き出し (別スレッドで実行)"""
    gen = DungeonGenerator(width=w, height=h, density=density, loop_ratio=loop_ratio, seed=seed, scene_mode=scene_mode)
    gen.generate(target_room_count=WILDERNESS_POINT_COUNT if scene_mode == "wilderness" else None)
    gen.render_to_png(png_path, cell_size=cell_size)
    walls = select_walls(gen)
    return gen, walls


async def generate_and_create_dungeon_scene(
    ctx,
    size_key: str,
    loop_ratio: float,
    density: float = DEFAULT_DENSITY,
    user_prompt: str = "",
    seed: Optional[int] = None,
    scene_mode: str = DEFAULT_SCENE_MODE,
) -> Optional[Dict]:
    """ダンジョンを生成し、ワールド設定から伝承・生態系・部屋フレーバーを補完してFVTTに新シーン作成命令を送信"""
    scene_mode = normalize_scene_mode(scene_mode)
    mode_name, default_scene_name = SCENE_MODE_INFO[scene_mode]
    w, h, size_name, cell_size = SIZE_PRESETS.get(size_key, SIZE_PRESETS[DEFAULT_SIZE_KEY])
    log("")
    log_group(f"ダンジョン自動生成を始めます: モード {mode_name}, サイズ: {size_name} ({w}x{h}), 密度: {density_label(density)} ({density}), ループ率: {loop_ratio}")

    # 1-3. 幾何生成・PNG 書き出し・壁抽出 (イベントループを止めないよう別スレッド)
    built = await build_map(ctx, size_key, density, loop_ratio, seed, scene_mode)
    if built is None:
        return None
    gen, walls, png_filename = built
    log_step(f"マップを作りました: 壁 {len(walls)} 本、部屋 {len(gen.rooms)}")

    # 4. ワールド設定の読み込み
    world_info = get_active_world_info()
    world_title = world_info.get("title", "The Antikythira")
    world_desc = world_info.get("description", "ゴブリンキングとブラックドラゴンの地下城塞")

    is_custom_theme = bool(user_prompt and user_prompt not in GENERIC_DUNGEON_PROMPTS)
    target_theme_desc = f"GM指定テーマ『{user_prompt}』" if is_custom_theme else f"ワールド設定『{world_desc}』"
    await ctx.send(protocol.chat(
        f"🏰 <b>{mode_name}の構造を算出しました</b> (部屋数: {len(gen.rooms)}部屋)<br>📜 {target_theme_desc} に基づき、伝承・生態系と各部屋の情景をAI補完中...",
        speaker=SPEAKER_SUPPORT,
    ))

    # 5. Tier 1 & 2: ダンジョン全体の歴史・伝承とフロア生態系
    lore_prompt = build_lore_prompt(
        world_title=world_title,
        world_description=world_desc,
        user_prompt=user_prompt,
        floor_number=1,
        size_name=size_name,
        room_count=len(gen.rooms),
        scene_mode=scene_mode,
    )
    lore_data = None
    try:
        lore_data = await run_with_deadline(
            ctx, "伝承", ctx.llm.query_json(lore_prompt, unbounded=True), limit=llm_step_limit(ctx)
        )
    except Exception as e:
        print(f"Lore生成失敗 ({e})。フォールバック伝承を使用します。")
    if not isinstance(lore_data, dict) or "floor_title" not in lore_data:
        lore_data = fallback_lore(size_name)

    await ctx.send(protocol.chat(
        f"⚔️ <b>伝承決定:</b> 『{lore_data.get('dungeon_name')}』 - {lore_data.get('floor_title')}<br>各部屋のトポロジー（接続数・深さ・空室配分）に基づき情景とGMメモを補完中...",
        speaker=SPEAKER_SUPPORT,
    ))

    # 6. Tier 3: トポロジーに基づく部屋フレーバー補完
    custom_flavors = await query_room_flavors(ctx, lore_data, gen.get_room_topologies(), scene_mode)

    # 7. 各部屋フレーバーの統合 (id は query_room_flavors で 0 始まりに正規化済み)
    rooms = gen.get_room_flavor_notes(grid_size=100, custom_flavors=custom_flavors, id_base=0)

    # 8. FVTTへシーン作成コマンド送信 & 9. 音声報告
    scene_title = f"{lore_data.get('dungeon_name', default_scene_name)} - {lore_data.get('floor_title', f'第1層 ({size_name})')}"
    speech_text = f"{mode_name}『{lore_data.get('dungeon_name', default_scene_name)}』のシーンを生成しました。全{len(gen.rooms)}部屋に生態系と情景ノートを設定しました。"
    scene_msg = await send_scene(ctx, scene_title, scene_mode, png_filename, gen, walls, lore_data, rooms, density, speech_text)
    log_group("ダンジョンシーンの構築指示を送りました")
    return scene_msg


async def build_map(
    ctx, size_key: str, density: float, loop_ratio: float, seed: Optional[int], scene_mode: str,
    on_status=None, auto_extend: bool = False,
) -> Optional[Tuple[DungeonGenerator, List[list], str]]:
    """幾何生成・PNG 書き出し・壁抽出。打ち切られたら GM に知らせて None"""
    w, h, _, cell_size = SIZE_PRESETS.get(size_key, SIZE_PRESETS[DEFAULT_SIZE_KEY])
    ts = int(time.time())
    png_filename = f"dungeon_{scene_mode}_{w}x{h}_{ts}.png"
    png_filepath = os.path.join(ctx.data_dir, png_filename)
    try:
        gen, walls = await run_with_deadline(
            ctx, "マップ生成",
            asyncio.to_thread(_build_geometry, w, h, density, loop_ratio, png_filepath, cell_size, seed, scene_mode),
            limit=MAPGEN_TIMEOUT, on_status=on_status, auto_extend=auto_extend,
        )
    except asyncio.TimeoutError:
        # 別スレッドの計算は止められないが、結果は使わずに終える
        log_step("マップ生成を打ち切りました (上限は AI_GM_MAPGEN_TIMEOUT で変更可)")
        await ctx.send(protocol.chat(
            "⚠️ マップ生成を打ち切りました。小さいサイズか低い密度でやり直してください。",
            speaker=SPEAKER_SUPPORT,
        ))
        return None
    return gen, walls, png_filename


async def send_scene(
    ctx, name: str, scene_mode: str, png_filename: str, gen: DungeonGenerator, walls: List[list],
    lore: Dict, rooms: List[Dict], density: float, speech_text: Optional[str], scene_key: Optional[str] = None,
) -> Dict:
    """
    FVTT へ create_dungeon_scene を送り、完了を音声で報告する (speech_text が None なら報告しない)。
    scene_key を付けると、あとから update_dungeon_rooms で区画を書き換えられる
    """
    scene_msg = {
        "action": "create_dungeon_scene",
        "name": name,
        "scene_mode": scene_mode,
        "background_src": png_filename,
        "grid_width": gen.width,
        "grid_height": gen.height,
        "grid_size": 100,
        "room_count": len(gen.rooms),
        "lore": lore,
        "rooms": rooms,
        "density": density,
        "start_pos": list(gen.start_pos) if gen.start_pos else None,
        "goal_pos": list(gen.goal_pos) if gen.goal_pos else None,
        "walls": walls,
    }
    if scene_key:
        scene_msg["scene_key"] = scene_key
    await ctx.send(scene_msg)
    log_step(f"『{name}』のシーンを FVTT に送りました (区画 {len(rooms)}、壁 {len(walls)} 本"
        f"{'、区画はあとから差し替え' if scene_key else ''})")
    if speech_text is None:
        return scene_msg

    file_name = voice_filename("voice_dungeon")
    if await ctx.tts.synthesize(speech_text, file_name):
        await ctx.send(protocol.play_sound(file_name, 1.0, subtitle=speech_text))
    return scene_msg
