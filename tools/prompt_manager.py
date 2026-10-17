#!/usr/bin/env python3
"""
prompt_manager.py
ダンジョン生成用のプロンプト管理・テンプレート読み込み・キャッシュ・ワールド情報取得モジュール
ユーザーへの応答速度向上のため、プロンプトテンプレートを事前キャッシュして管理します。
"""

import os
import re
import json
import glob
from typing import Dict, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(MODULE_DIR, "prompts")
DATA_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", ".."))

# プロンプトキャッシュ (メモリ保持)
_PROMPT_CACHE: Dict[str, str] = {}


def load_cached_prompt(filename: str) -> str:
    """プロンプトテンプレートをメモリにキャッシュして高速返却"""
    if filename in _PROMPT_CACHE:
        return _PROMPT_CACHE[filename]

    filepath = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"プロンプトファイルが見つかりません: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    _PROMPT_CACHE[filename] = content
    return content


def get_active_world_info() -> Dict[str, str]:
    """
    Foundry VTTのワールド設定 (world.json) からワールド名およびワールド説明（ダンジョン設定）を取得
    """
    worlds_dir = os.path.join(DATA_DIR, "worlds")
    if not os.path.exists(worlds_dir):
        return {
            "title": "ダンジョン世界",
            "description": "古代の地下城塞",
            "world_id": "default"
        }

    # 各ワールドディレクトリ内の world.json を探索
    world_candidates = glob.glob(os.path.join(worlds_dir, "*", "world.json"))
    if not world_candidates:
        return {
            "title": "ダンジョン世界",
            "description": "古代の地下城塞",
            "world_id": "default"
        }

    # 最新更新された world.json または the-antikythira を優先
    target_json = world_candidates[0]
    for wc in world_candidates:
        if "the-antikythira" in wc:
            target_json = wc
            break

    try:
        with open(target_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_desc = data.get("description", "")
        # HTMLタグ除去
        clean_desc = re.sub(r"<[^>]*>", " ", raw_desc)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

        return {
            "title": data.get("title", "ダンジョン世界"),
            "description": clean_desc or "ゴブリンキングとブラックドラゴンの地下城塞",
            "world_id": data.get("id", os.path.basename(os.path.dirname(target_json)))
        }
    except Exception as e:
        print(f"world.json 読み込みエラー ({e})")
        return {
            "title": "The Antikythira",
            "description": "ゴブリンキングとブラックドラゴンの地下城塞",
            "world_id": "the-antikythira"
        }


def build_lore_prompt(
    world_title: str,
    world_description: str,
    user_prompt: str = "",
    floor_number: int = 1,
    size_name: str = "小",
    room_count: int = 10
) -> str:
    """Tier 1/2: ダンジョン全体の歴史・伝承とフロア生態系を生成するプロンプトを構築"""
    template = load_cached_prompt("dungeon_lore_prompt.txt")
    return template.format(
        world_title=world_title or "The Antikythira",
        world_description=world_description or "ゴブリンキングとブラックドラゴンの地下城塞",
        user_prompt=user_prompt or "なし（ワールド設定に忠実な城塞ダンジョン）",
        floor_number=floor_number,
        size_name=size_name,
        room_count=room_count
    )


def prioritize_key_topologies(topologies: List[Dict], max_count: int = 15) -> List[Dict]:
    """TRPGのストーリーテリングにおいて最重要となる主要部屋（入口・最深部・袋小路・ハブ）を優先選定して高速化"""
    if len(topologies) <= max_count:
        return topologies

    # 1. 必須部屋: スタートとゴール
    must_have = []
    # 2. 高優先部屋: 次数1 (袋小路: 宝/罠/独房)、次数3以上 (ハブ: 広場/交差点)
    high_priority = []
    # 3. 通常部屋: 通過路 (次数2)
    normal = []

    for topo in topologies:
        if topo.get("start") or topo.get("goal"):
            must_have.append(topo)
        elif topo.get("deg", 2) == 1 or topo.get("deg", 2) >= 3:
            high_priority.append(topo)
        else:
            normal.append(topo)

    # 優先度順にマージして最大 max_count 件まで取得
    selected = list(must_have)
    needed = max_count - len(selected)
    if len(high_priority) <= needed:
        selected.extend(high_priority)
        needed = max_count - len(selected)
        selected.extend(normal[:needed])
    else:
        selected.extend(high_priority[:needed])

    # 元の部屋ID順にソートして返却
    selected.sort(key=lambda x: x["id"])
    return selected


def build_rooms_prompt(
    lore: Dict,
    topologies: List[Dict],
    max_rooms: int = 15
) -> str:
    """Tier 3: 部屋のトポロジーに基づくマイクロフレーバー生成プロンプトを構築"""
    template = load_cached_prompt("dungeon_rooms_prompt.txt")

    dungeon_name = lore.get("dungeon_name", "古代の地下城塞")
    floor_title = lore.get("floor_title", "第1層")
    floor_origin = lore.get("floor_origin", "旧城塞の遺構")

    factions_list = lore.get("factions", [])
    if isinstance(factions_list, list):
        factions_summary = "; ".join([f"{f.get('name')}: {f.get('status')}" for f in factions_list if isinstance(f, dict)])
    else:
        factions_summary = str(factions_list)

    floor_ambience = lore.get("floor_ambience", {})
    if isinstance(floor_ambience, dict):
        floor_ambience_summary = f"視覚: {floor_ambience.get('visual', '')}, 聴覚: {floor_ambience.get('auditory', '')}, 嗅覚: {floor_ambience.get('olfactory', '')}, 空気: {floor_ambience.get('air', '')}"
    else:
        floor_ambience_summary = str(floor_ambience)

    # 応答速度向上のため、主要な部屋を優先選定
    sampled_topos = prioritize_key_topologies(topologies, max_count=max_rooms)
    compact_topologies = []
    for topo in sampled_topos:
        compact_topologies.append({
            "id": topo["id"],
            "deg": topo["deg"],
            "depth": topo["depth"],
            "start": topo["start"],
            "goal": topo["goal"],
            "area": topo["area"]
        })

    rooms_topology_json = json.dumps(compact_topologies, ensure_ascii=False)

    return template.format(
        dungeon_name=dungeon_name,
        floor_title=floor_title,
        floor_origin=floor_origin,
        factions_summary=factions_summary,
        floor_ambience_summary=floor_ambience_summary,
        rooms_topology_json=rooms_topology_json
    )


def extract_json_from_response(resp_text: str) -> Optional[dict | list]:
    """AIの応答テキストからJSON部分（オブジェクトまたは配列）を安全に抽出・パース"""
    if not resp_text or not isinstance(resp_text, str):
        return None
    cleaned = resp_text.strip()
    # Markdownコードブロックの除去
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # 1. 直接パース
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. 前後に説明文が付いている場合: 最初に現れる { または [ から JSON を読み取る
    #    (オブジェクトを先に探すと、要素1件の配列 `[{...}]` が dict として返ってしまうため出現順に試す)
    decoder = json.JSONDecoder()
    for attempts, match in enumerate(re.finditer(r"[\{\[]", cleaned)):
        if attempts >= 50:
            break
        try:
            value, _ = decoder.raw_decode(cleaned, match.start())
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value

    return None
