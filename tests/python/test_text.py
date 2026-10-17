import pytest

from gm_server.text import (
    clean_html,
    dice_formula_from_command,
    first_sentence,
    normalize_tts_pitch,
    normalize_tts_rate,
)


def test_clean_html_strips_tags_and_whitespace():
    assert clean_html("<p>部屋を<b>調べる</b></p>\n") == "部屋を調べる"
    assert clean_html("") == ""
    assert clean_html(None) == ""


def test_clean_html_handles_multiline_tags():
    assert clean_html('<span\nclass="x">扉</span>') == "扉"


@pytest.mark.parametrize("raw, expected", [
    ("/r 1d20", "1d20"),
    ("/roll 1d20+3", "1d20+3"),
    ("/R 2d6", "2d6"),
    ("1d8", "1d8"),
    ("/r ", None),
    (None, None),
    ("", None),
    (123, None),
])
def test_dice_formula_from_command(raw, expected):
    assert dice_formula_from_command(raw) == expected


def test_dice_formula_keeps_roll_prefix_inside_formula():
    # 旧実装の str.replace("/r ", "") は式の途中も消していた
    assert dice_formula_from_command("/r 1d20 /r") == "1d20 /r"


def test_first_sentence():
    assert first_sentence("扉が開いた。中は暗い！") == "扉が開いた"
    assert first_sentence("") == ""
    assert first_sentence("句点なし" * 20, fallback_len=5).startswith("句点なし")


@pytest.mark.parametrize("raw, expected", [
    ("+10%", "+10%"),
    ("-15%", "-15%"),
    ("10", "+10%"),
    ("速め 20%", "+20%"),
    ("", "+0%"),
    (None, "+0%"),
    ("fast", "+0%"),
])
def test_normalize_tts_rate(raw, expected):
    assert normalize_tts_rate(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("+5Hz", "+5Hz"),
    ("-10hz", "-10Hz"),
    ("3", "+3Hz"),
    ("", "+0Hz"),
    (None, "+0Hz"),
    ("low", "+0Hz"),
])
def test_normalize_tts_pitch(raw, expected):
    assert normalize_tts_pitch(raw) == expected
