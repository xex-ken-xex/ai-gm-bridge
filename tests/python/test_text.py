import pytest

from gm_server.text import (
    ai_text_to_html,
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


def test_dice_formula_strips_roll_prefix_only_at_start():
    # 旧実装の str.replace("/r ", "") は式の途中も消していた。途中に残った /r は式として断る
    assert dice_formula_from_command("/r 1d20 /r") is None
    assert dice_formula_from_command("/r 4d6kh3 + (2 * 3)") == "4d6kh3 + (2 * 3)"


@pytest.mark.parametrize("raw", [
    "/r 99999d6",          # GM のブラウザが固まる個数
    "/r 1d100000",         # 面数が大きすぎる
    "/r 60d6 + 50d6",      # 合計の個数が上限を超える
    "/r 1d20 + @abilities.str.mod",  # キャラクターの値の参照
    "/r 1d20[<img src=x>]",          # フレーバー文
    "/r (9999*9999)d6",    # 個数を括弧で書いて上限をすり抜ける
    "/r (99d1000)d1000",
    "/r (2) d6",
    "/r 1d(1000*1000)",    # 面数を括弧で書く
    "/r 1dd6",
])
def test_dice_formula_rejects_unsafe_formulas(raw):
    assert dice_formula_from_command(raw) is None


def test_ai_text_to_html_escapes_markup_but_keeps_plain_emphasis():
    text = '扉が<b>開いた</b>。\n<img src="https://evil.example/x.png" onerror="alert(1)"><B class="x">罠</B>'
    assert ai_text_to_html(text) == (
        "扉が<b>開いた</b>。<br>&lt;img src=&quot;https://evil.example/x.png&quot; onerror=&quot;alert(1)&quot;&gt;"
        "&lt;B class=&quot;x&quot;&gt;罠</b>"
    )
    assert ai_text_to_html("<br/>次へ<script>alert(1)</script>") == "<br>次へ&lt;script&gt;alert(1)&lt;/script&gt;"
    assert ai_text_to_html(None) == ""


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
