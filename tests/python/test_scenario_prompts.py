import json
import random

import pytest

from tools.dungeon_generator import DungeonGenerator
from tools.scenario_decks import DECKS, draw_cards
from tools.scenario_prompts import (
    PLAN_LABELS,
    build_context_block,
    build_site_rooms_prompt,
    build_stage_prompt,
    name_ledger,
    plan_site_rooms,
    to_prompt_text,
)

CONTEXT = {
    "user_prompt": "塩の交易で栄えた土地",
    "world": {
        "title": "アンティキティラ",
        "description": "ゴブリン王と黒竜の地",
        "references": [{"name": "世界の暦", "text": "一年は十三か月"}],
    },
    "parents": [{
        "category": "region", "name": "白塩の野", "public_lore": "塩の平原", "dm_secrets": "塩の下に墓",
        "canon": ["命名の作法: 柔らかい母音", "資源: 岩塩は南の干潟"], "open_threads": ["塩の下の墓は誰のものか"],
    }],
    "atlas_index": [{"category": "settlement", "name": "ミラ村", "canon": ["悲しみ: 出稼ぎから戻らない"]}],
    "actor_names": ["アリア", "ミラ村"],
}


def wilderness_topologies():
    gen = DungeonGenerator(width=100, height=100, density=0.4, loop_ratio=0.3, seed=5, scene_mode="wilderness")
    gen.generate(target_room_count=9)
    return gen.get_room_topologies()


def test_draw_cards_is_reproducible_and_covers_every_axis():
    for stage in DECKS:
        a = draw_cards(stage, random.Random(42))
        b = draw_cards(stage, random.Random(42))
        assert a == b
        assert len(a) == len(DECKS[stage])
        assert all(c["id"].startswith(f"{stage}.") for c in a)


def test_draw_cards_avoids_recent_cards_until_axis_runs_out():
    axis_id, _, entries = DECKS["region"][0]
    recent = [f"region.{axis_id}.{cid}" for cid, _ in entries[:-1]]
    for seed in range(20):
        assert draw_cards("region", random.Random(seed), recent)[0]["id"] == f"region.{axis_id}.{entries[-1][0]}"
    every = [f"region.{axis_id}.{cid}" for cid, _ in entries]
    assert draw_cards("region", random.Random(1), every)[0]["id"] in every


def test_context_block_orders_world_canon_threads_and_cards():
    cards = draw_cards("settlement", random.Random(1))
    block = build_context_block({**CONTEXT, "fixed_name": "塩見の里"}, cards)
    order = ["【ワールド資料】", "【上位段階】", "【確定事項】", "【未回収の伏線】", "【固有名詞台帳】", "【既存の地図帳エントリ】", "【GM指示】", "【発想カード】"]
    positions = [block.index(h) for h in order]
    assert positions == sorted(positions)
    assert "世界の暦" in block and "ゴブリン王と黒竜の地" in block
    assert "[地域『白塩の野』] 資源: 岩塩は南の干潟" in block
    assert "塩の下の墓は誰のものか" in block
    assert "名前は『塩見の里』にする" in block
    assert cards[0]["text"] in block


def test_context_block_uses_server_world_when_fvtt_sends_none():
    block = build_context_block({}, None, {"title": "W", "description": "D"})
    assert "ワールド名: W" in block and "(なし。この段階が最初)" in block
    assert "【発想カード】" not in block


def test_name_ledger_deduplicates():
    assert name_ledger(CONTEXT) == ["白塩の野", "ミラ村", "アリア"]


def test_dict_context_is_json_not_python_repr():
    """R4: dict を str.format にそのまま渡して {'key': ...} が混ざらない"""
    assert to_prompt_text({"a": "あ"}) == '{"a": "あ"}'
    block = build_context_block({**CONTEXT, "parents": [{**CONTEXT["parents"][0], "public_lore": {"気候": "乾燥"}}]})
    assert '{"気候": "乾燥"}' in block and "{'気候'" not in block


@pytest.mark.parametrize("stage", ["region", "settlement", "wilderness", "site_lore"])
def test_every_stage_prompt_renders(stage):
    ctx = {**CONTEXT, "actors_context": {"pcs": [{"name": "アリア", "info": "エルフ", "bio": "塩の研究者"}]}}
    prompt = build_stage_prompt(
        stage, ctx, draw_cards(stage, random.Random(3)),
        topologies=wilderness_topologies() if stage == "wilderness" else None,
        light_presets=["dark", "torch_warm"],
    )
    assert "【共通ルール: 整合性】" in prompt and "【出力前の自己点検】" in prompt
    assert '"canon"' in prompt and '"open_threads"' in prompt
    if stage == "site_lore":
        assert "アリア (エルフ): 塩の研究者" in prompt


def test_wilderness_prompt_carries_zero_based_graph():
    topos = wilderness_topologies()
    prompt = build_stage_prompt("wilderness", CONTEXT, [], topologies=topos, light_presets=["dark"])
    graph = json.loads(prompt.split("【旅路の地点グラフ】(地点の数と接続は確定済み。変えない)\n")[1].split("\n")[0])
    assert [g["id"] for g in graph] == list(range(len(topos)))
    assert all(n in range(len(topos)) for g in graph for n in g["neighbors"])


def test_plan_site_rooms_places_fixed_roles_and_sub_goals():
    gen = DungeonGenerator(width=100, height=100, density=0.5, loop_ratio=0.2, seed=11)
    gen.generate()
    topos = gen.get_room_topologies()[:15]
    subs = [{"title": "閂を外す", "target_topology": "dead_end"}, {"title": "副官と取引", "target_topology": "hub"}]
    plan = plan_site_rooms(topos, subs, random.Random(1))
    assert set(plan) == {t["id"] for t in topos}
    by_id = {t["id"]: t for t in topos}
    roles = [p["role"] for p in plan.values()]
    for t in topos:
        if t["start"]:
            assert plan[t["id"]]["role"] == "start"
        if t["goal"]:
            assert plan[t["id"]]["role"] == "goal"
    sub_rooms = [rid for rid, p in plan.items() if p["role"] == "sub_goal"]
    assert len(sub_rooms) == 2
    assert any("閂を外す" in plan[rid]["label"] for rid in sub_rooms)
    dead_end_sub = next(rid for rid in sub_rooms if plan[rid]["sub_index"] == 0)
    if any(t["deg"] == 1 and not t["start"] and not t["goal"] for t in topos):
        assert by_id[dead_end_sub]["deg"] == 1
    quiet = roles.count("empty") + roles.count("rest")
    others = len(topos) - roles.count("start") - roles.count("goal") - len(sub_rooms)
    assert quiet == round(others * 0.3)
    assert roles.count("rest") == (1 if quiet else 0)
    assert plan == plan_site_rooms(topos, subs, random.Random(1))


def test_plan_site_rooms_without_sub_objectives():
    topos = wilderness_topologies()
    plan = plan_site_rooms(topos, None, random.Random(2))
    assert "sub_goal" not in [p["role"] for p in plan.values()]
    assert all(p["label"] for p in plan.values())
    assert plan_site_rooms([], None, random.Random(2)) == {}


def test_site_rooms_prompt_lists_whole_plan_and_only_the_chunk():
    topos = wilderness_topologies()
    plan = plan_site_rooms(topos, [{"title": "鐘を鳴らす", "target_topology": "hub"}], random.Random(4))
    prompt = build_site_rooms_prompt("塔", "ruins", CONTEXT, "公開", "秘密", topos[:3], plan, ["dark"])
    assert all(f"- 区画 {t['id']}:" in prompt for t in topos)
    rooms = json.loads(prompt.split("【あなたが書く区画】\n")[1].split("\n")[0])
    assert [r["id"] for r in rooms] == [t["id"] for t in topos[:3]]
    assert all(r["plan"] in prompt for r in rooms)
    assert "廃墟" in prompt and PLAN_LABELS["start"] in prompt
