import json

import pytest

from tools import prompt_manager as pm


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('[1, 2]', [1, 2]),
    ('以下が結果です:\n{"a": {"b": 2}}\n以上', {"a": {"b": 2}}),
    ('結果:\n[{"id": 0}, {"id": 1}]', [{"id": 0}, {"id": 1}]),
])
def test_extract_json_variants(text, expected):
    assert pm.extract_json_from_response(text) == expected


def test_extract_json_single_element_array_with_preamble_stays_list():
    # 旧実装はオブジェクトを先に探すため、要素1件の配列が dict として返っていた
    text = '部屋フレーバーです:\n[{"id": 0, "title": "入口"}]'
    assert pm.extract_json_from_response(text) == [{"id": 0, "title": "入口"}]


def test_extract_json_skips_non_json_brackets():
    text = '[注意] 以下を参照\n{"ok": true}'
    assert pm.extract_json_from_response(text) == {"ok": True}


@pytest.mark.parametrize("text", ["", None, "JSONなし", "{壊れた"])
def test_extract_json_returns_none(text):
    assert pm.extract_json_from_response(text) is None


def _topos(n):
    return [
        {"id": i, "deg": (1 if i % 3 == 0 else 2), "depth": i, "start": i == 0, "goal": i == n - 1,
         "in_cycle": False, "neighbors": [], "area": 25}
        for i in range(n)
    ]


def test_prioritize_key_topologies_keeps_start_goal_and_sorts():
    topos = _topos(30)
    selected = pm.prioritize_key_topologies(topos, max_count=8)
    ids = [t["id"] for t in selected]
    assert len(selected) == 8
    assert 0 in ids and 29 in ids
    assert ids == sorted(ids)


def test_prioritize_key_topologies_returns_all_when_small():
    topos = _topos(5)
    assert pm.prioritize_key_topologies(topos, max_count=8) == topos


def test_build_prompts_format_without_missing_placeholders():
    lore = {
        "dungeon_name": "黒水城塞",
        "floor_title": "第1層",
        "floor_origin": "崩落した要塞",
        "factions": [{"name": "ゴブリン", "status": "警戒"}],
        "floor_ambience": {"visual": "苔"},
    }
    lore_prompt = pm.build_lore_prompt("世界", "説明", "テーマ", 1, "小", 12)
    rooms_prompt = pm.build_rooms_prompt(lore, _topos(20), max_rooms=10)
    assert "世界" in lore_prompt
    assert "黒水城塞" in rooms_prompt and "ゴブリン: 警戒" in rooms_prompt
    payload, _ = json.JSONDecoder().raw_decode(rooms_prompt[rooms_prompt.index('[{"id"'):])
    assert payload[0]["id"] == 0
    assert {"in_cycle", "neighbors"} <= set(payload[0])
    assert "bonfire_bright" in rooms_prompt
    assert "地下迷宮" in lore_prompt and "地下迷宮" in rooms_prompt


@pytest.mark.parametrize("mode, word", [("ruins", "廃墟"), ("wilderness", "野外"), ("bogus", "地下迷宮")])
def test_build_prompts_mention_scene_mode(mode, word):
    lore_prompt = pm.build_lore_prompt("世界", "説明", scene_mode=mode)
    rooms_prompt = pm.build_rooms_prompt({}, _topos(3), scene_mode=mode)
    assert word in lore_prompt and word in rooms_prompt


def test_get_active_world_info_reads_world_json(tmp_path, monkeypatch):
    world_dir = tmp_path / "worlds" / "my-world"
    world_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(
        json.dumps({"id": "my-world", "title": "灰の大地", "description": "<p>古い <b>竜</b>の巣</p>"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pm, "DATA_DIR", str(tmp_path))
    info = pm.get_active_world_info()
    assert info == {"title": "灰の大地", "description": "古い 竜 の巣", "world_id": "my-world"}


def test_get_active_world_info_defaults_without_worlds(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "DATA_DIR", str(tmp_path))
    assert pm.get_active_world_info()["world_id"] == "default"
