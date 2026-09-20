from app.core.parser.font_resolver import resolve_font_path


def test_unknown_typeface_returns_fallback():
    result = resolve_font_path("SomeFontThatDefinitelyDoesNotExist12345")

    assert result.endswith("DejaVuSans.ttf")


def test_empty_typeface_skips_lookup():
    assert resolve_font_path("").endswith("DejaVuSans.ttf")
    assert resolve_font_path(None).endswith("DejaVuSans.ttf")
