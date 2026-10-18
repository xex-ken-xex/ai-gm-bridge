

def test_room_progress_names_the_rooms_being_written():
    from gm_server.dungeon_scene import progress_detail, room_numbers, room_unit_of
    assert room_unit_of("区画の情景") == ("区画", "情景")
    assert room_unit_of("ビルダー: 旅路 2/2 地点の描写") == ("地点", "描写")
    assert room_numbers([{"id": 2}, {"id": 4}, {"id": 7}]) == "3・5・8"
    assert progress_detail("区画", "情景", ["3・5・8"], 4, 12) == "区画 3・5・8 の情景を作成中 (12 区画中 4 区画済み)"
    assert progress_detail("区画", "情景", ["1・2", "3・4"], 0, 4) == "区画 1・2 / 区画 3・4 の情景を作成中 (4 区画中 0 区画済み)"
    assert progress_detail("区画", "情景", [], 4, 4) == "4 区画中 4 区画済み"


def test_size_card_lists_20_50_100_200_squares():
    from gm_server.dungeon_scene import size_card
    labels = [c["label"] for c in size_card()["choices"]]
    assert labels == ["小 (20x20マス)", "中 (50x50マス)", "大 (100x100マス)", "巨大 (200x200マス)"]


def test_density_card_room_hints_follow_the_size():
    from gm_server.dungeon_scene import density_card
    small = [c["label"] for c in density_card("small", "小")["choices"]]
    xlarge = [c["label"] for c in density_card("xlarge", "巨大")["choices"]]
    assert "4部屋前後" in small[0] and "7部屋前後" in small[2]
    assert "40部屋前後" in xlarge[0] and "100部屋前後" in xlarge[2]


def test_wilderness_skips_small_size():
    """野外は 9 地点前後が入る中 (50x50) から。古いカードで小が来ても中にする"""
    from gm_server.dungeon_scene import size_card, size_key_for_mode
    values = [c["value"] for c in size_card("野外", "wilderness")["choices"]]
    assert values == ["dungeon_size:medium", "dungeon_size:large", "dungeon_size:xlarge"]
    assert size_key_for_mode("small", "wilderness") == "medium"
    assert size_key_for_mode("small", "ruins") == "small"
