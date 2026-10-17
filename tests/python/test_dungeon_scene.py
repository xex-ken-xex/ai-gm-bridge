

def test_room_progress_names_the_rooms_being_written():
    from gm_server.dungeon_scene import progress_detail, room_numbers, room_unit_of
    assert room_unit_of("区画の情景") == ("区画", "情景")
    assert room_unit_of("ビルダー: 旅路 2/2 地点の描写") == ("地点", "描写")
    assert room_numbers([{"id": 2}, {"id": 4}, {"id": 7}]) == "3・5・8"
    assert progress_detail("区画", "情景", ["3・5・8"], 4, 12) == "区画 3・5・8 の情景を作成中 (12 区画中 4 区画済み)"
    assert progress_detail("区画", "情景", ["1・2", "3・4"], 0, 4) == "区画 1・2 / 区画 3・4 の情景を作成中 (4 区画中 0 区画済み)"
    assert progress_detail("区画", "情景", [], 4, 4) == "4 区画中 4 区画済み"
