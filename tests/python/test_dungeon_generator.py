import pytest

from tools.dungeon_generator import DungeonGenerator


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
    for x0, y0, x1, y1 in walls:
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
