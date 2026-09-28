from app.core.parser.theme_extractor import (
    FALLBACK_COLORS,
    extract_font_scheme,
    extract_theme_colors,
)

_NSDECL = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def test_extract_theme_colors_no_clr_scheme_returns_fallback():
    xml = f"<a:theme {_NSDECL}><a:themeElements/></a:theme>".encode()

    result = extract_theme_colors(xml)

    assert result == FALLBACK_COLORS


def test_extract_theme_colors_sys_clr_without_last_clr_keeps_fallback_others_extracted():
    xml = f"""
    <a:theme {_NSDECL}>
      <a:themeElements>
        <a:clrScheme name="Test">
          <a:dk1><a:sysClr val="windowText"/></a:dk1>
          <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
          <a:dk2><a:srgbClr val="112233"/></a:dk2>
          <a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>
          <a:accent1><a:srgbClr val="AABBCC"/></a:accent1>
          <a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
          <a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>
          <a:accent4><a:srgbClr val="FFC000"/></a:accent4>
          <a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>
          <a:accent6><a:srgbClr val="70AD47"/></a:accent6>
          <a:hlink><a:srgbClr val="0563C1"/></a:hlink>
          <a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
        </a:clrScheme>
      </a:themeElements>
    </a:theme>
    """.encode()

    result = extract_theme_colors(xml)

    assert result["dk1"] == FALLBACK_COLORS["dk1"]
    assert result["accent1"] == "AABBCC"


def test_extract_theme_colors_malformed_xml_returns_fallback():
    result = extract_theme_colors(b"<not valid xml")

    assert result == FALLBACK_COLORS


def test_extract_font_scheme_missing_returns_calibri_defaults():
    xml = f"<a:theme {_NSDECL}><a:themeElements/></a:theme>".encode()

    result = extract_font_scheme(xml)

    assert result == ("Calibri", "Calibri")
