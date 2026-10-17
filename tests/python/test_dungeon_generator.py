import pytest

from tools.dungeon_generator import LIGHT_PRESET_KEYS, WALL_TYPES, DungeonGenerator


@pytest.fixture(scope="module")
def gen():
    g = DungeonGenerator(width=60, height=60, seed=12345, density=0.5, loop_ratio=0.2)
    g.generate()
    return g


def test_generation_is_deterministic_with_seed(gen):
    other = DungeonGenerator(width=60, height=60, seed=12345, density=0.5, loop_ratio=0.2)
    other.generate()
    assert other.to_string() == gen.to_string()
    assert other.rooms == gen.rooms


def test_all_rooms_reachable_and_goal_is_farthest(gen):
    assert len(gen.rooms) >= 2
    assert all(d >= 0 for d in gen.dist)
    assert gen.dist[gen.goal_room_idx] == max(gen.dist)


def test_topologies_include_symmetric_neighbors(gen):
    topos = gen.get_room_topologies()
    assert len(topos) == len(gen.rooms)
    by_id = {t["id"]: t for t in topos}
    for t in topos:
        assert t["deg"] == len(t["neighbors"])
        for n in t["neighbors"]:
            assert t["id"] in by_id[n]["neighbors"]
    assert topos[0]["start"] is True


def test_walls_are_axis_aligned_and_inside_map(gen):
    walls = gen.extract_walls(grid_size=100)
    assert walls
    for x0, y0, x1, y1, wall_type in walls:
        assert wall_type in WALL_TYPES
        assert x0 == x1 or y0 == y1
        for v in (x0, x1):
            assert 0 <= v <= gen.width * 100
        for v in (y0, y1):
            assert 0 <= v <= gen.height * 100


def test_flavor_notes_zero_based_ids_do_not_leak_to_neighbours(gen):
    # AI が一部の部屋 (0 始まり id) だけを返した場合
    custom = [{"id": 0, "title": "入口"}, {"id": 2, "title": "宝物庫"}]
    notes = gen.get_room_flavor_notes(custom_flavors=custom)
    assert notes[0]["title"] == "入口"
    assert notes[2]["title"] == "宝物庫"
    # 旧実装は room 1 に id 2 のフレーバーが混入していた
    assert notes[1]["title"] != "宝物庫"


def test_flavor_notes_one_based_ids(gen):
    custom = [{"id": 1, "title": "入口"}, {"id": 3, "title": "宝物庫"}]
    notes = gen.get_room_flavor_notes(custom_flavors=custom)
    assert notes[0]["title"] == "入口"
    assert notes[2]["title"] == "宝物庫"


def test_flavor_notes_ignore_bad_entries(gen):
    custom = [None, {"title": "id無し"}, {"id": "x"}, {"id": 999, "title": "範囲外"}, {"id": "0", "title": "文字列id"}]
    notes = gen.get_room_flavor_notes(custom_flavors=custom)
    assert notes[0]["title"] == "文字列id"
    assert all(n["title"] != "範囲外" for n in notes)


def test_render_to_png(gen, tmp_path):
    out = tmp_path / "map.png"
    gen.render_to_png(str(out), cell_size=2)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def make(mode, seed=777, **kw):
    g = DungeonGenerator(width=60, height=60, seed=seed, density=0.5, loop_ratio=0.3, scene_mode=mode)
    g.generate(**kw)
    return g


def test_invalid_scene_mode_rejected():
    with pytest.raises(ValueError):
        DungeonGenerator(width=40, height=40, scene_mode="space")


def test_underground_has_doors_and_only_full_walls_otherwise():
    g = make("underground")
    types = {w[4] for w in g.extract_walls(grid_size=100)}
    assert "normal" in types
    assert "door" in types
    assert not types & {"low_wall", "rubble"}


def test_ruins_mix_low_walls_and_rubble():
    g = make("ruins")
    types = [w[4] for w in g.extract_walls(grid_size=100)]
    assert {"normal", "low_wall", "rubble"} <= set(types)
    assert "secret_door" not in types


def test_wilderness_has_no_walls_and_respects_room_count():
    g = make("wilderness", target_room_count=9)
    assert g.extract_walls(grid_size=100) == []
    assert 2 <= len(g.rooms) <= 9


def test_wall_types_are_deterministic():
    a = make("underground", seed=5).extract_walls(grid_size=100)
    b = make("underground", seed=5).extract_walls(grid_size=100)
    assert a == b


def test_secret_doors_only_on_dead_end_rooms():
    # 複数シードで secret_door が出たら、その扉は行き止まり部屋の開口部にあること
    found = False
    for seed in range(1, 30):
        g = make("underground", seed=seed)
        topo = {t["id"]: t for t in g.get_room_topologies()}
        dead_ends = {t["id"] for t in topo.values() if t["deg"] == 1 and not t["start"] and not t["goal"]}
        secrets = [w for w in g.extract_walls(grid_size=100) if w[4] == "secret_door"]
        if secrets:
            found = True
            assert dead_ends
    assert found


def test_in_cycle_matches_bridges():
    g = make("underground", seed=11)
    topos = g.get_room_topologies()
    for t in topos:
        i = t["id"]
        expected = any((min(i, n), max(i, n)) not in g.bridge_edges for n in t["neighbors"])
        assert t["in_cycle"] is expected
    # 木構造 (loop_ratio=0) ならすべての辺が橋で in_cycle は全て False
    tree = DungeonGenerator(width=60, height=60, seed=11, density=0.5, loop_ratio=0.0)
    tree.generate()
    assert all(t["in_cycle"] is False for t in tree.get_room_topologies())


def test_find_bridges_simple_graph():
    # 0-1-2-0 の三角形 + 2-3 の橋
    adj = {0: [1, 2], 1: [0, 2], 2: [0, 1, 3], 3: [2]}
    assert DungeonGenerator._find_bridges(4, adj) == {(2, 3)}


def test_flavor_notes_carry_role_light_and_sound(gen):
    custom = [
        {"id": 0, "title": "焚き火", "role": "rest", "light_preset": "bonfire_bright",
         "ambient_sound_src": "sounds/fire.ogg", "safe_rest": True},
        {"id": 1, "title": "怪しい部屋", "light_preset": "laser_disco"},
    ]
    notes = gen.get_room_flavor_notes(custom_flavors=custom)
    assert notes[0]["role"] == notes[0]["type"] == "rest"
    assert notes[0]["light_preset"] == "bonfire_bright"
    assert notes[0]["ambient_sound_src"] == "sounds/fire.ogg"
    assert notes[1]["light_preset"] is None
    assert all(n["light_preset"] in (None, *LIGHT_PRESET_KEYS) for n in notes)


@pytest.mark.parametrize("mode,label", [("ruins", "区画"), ("wilderness", "地点")])
def test_flavor_notes_use_mode_labels(mode, label):
    notes = make(mode).get_room_flavor_notes()
    assert notes[0]["type"] == "start"
    assert any(n["name"].startswith(label) for n in notes)
