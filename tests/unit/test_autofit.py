from app.core.builder.autofit import autofit_font_size
from app.core.parser.font_resolver import resolve_font_path

FONT_PATH = resolve_font_path(None)

BOX_WIDTH_EMU = 5_000_000
BOX_HEIGHT_EMU = 3_000_000

LONG_PARAGRAPH = " ".join(["word"] * 60)


def test_long_text_gets_smaller_font_than_short_text():
    short_result = autofit_font_size(["OK"], BOX_WIDTH_EMU, BOX_HEIGHT_EMU, FONT_PATH)
    long_result = autofit_font_size(
        [LONG_PARAGRAPH], BOX_WIDTH_EMU, BOX_HEIGHT_EMU, FONT_PATH
    )

    assert long_result <= short_result


def test_result_never_below_min_or_above_max():
    result = autofit_font_size([LONG_PARAGRAPH], 1000, 1000, FONT_PATH)

    assert result == 10


def test_result_never_above_max_size():
    result = autofit_font_size(["hi"], 50_000_000, 50_000_000, FONT_PATH)

    assert result == 44
