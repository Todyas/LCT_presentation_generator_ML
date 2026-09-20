from __future__ import annotations

from lxml import etree

NSMAP = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

FALLBACK_COLORS: dict[str, str] = {
    "dk1": "000000", "lt1": "FFFFFF", "dk2": "44546A", "lt2": "E7E6E6",
    "accent1": "4472C4", "accent2": "ED7D31", "accent3": "A5A5A5",
    "accent4": "FFC000", "accent5": "5B9BD5", "accent6": "70AD47",
    "hlink": "0563C1", "fol_hlink": "954F72",
}

_XML_TO_OUT_KEY = {
    "dk1": "dk1", "lt1": "lt1", "dk2": "dk2", "lt2": "lt2",
    "accent1": "accent1", "accent2": "accent2", "accent3": "accent3",
    "accent4": "accent4", "accent5": "accent5", "accent6": "accent6",
    "hlink": "hlink", "folHlink": "fol_hlink",
}


def extract_theme_colors(theme_xml_bytes: bytes) -> dict[str, str]:
    result = dict(FALLBACK_COLORS)
    try:
        root = etree.fromstring(theme_xml_bytes)
    except etree.XMLSyntaxError:
        return result

    scheme = root.find(".//a:clrScheme", NSMAP)
    if scheme is None:
        return result

    for xml_key, out_key in _XML_TO_OUT_KEY.items():
        node = scheme.find(f"a:{xml_key}", NSMAP)
        if node is None:
            continue
        srgb = node.find("a:srgbClr", NSMAP)
        if srgb is not None and srgb.get("val"):
            result[out_key] = srgb.get("val").upper()
            continue
        sys_clr = node.find("a:sysClr", NSMAP)
        if sys_clr is not None and sys_clr.get("lastClr"):
            result[out_key] = sys_clr.get("lastClr").upper()
    return result


def extract_font_scheme(theme_xml_bytes: bytes) -> tuple[str, str]:
    try:
        root = etree.fromstring(theme_xml_bytes)
    except etree.XMLSyntaxError:
        return "Calibri", "Calibri"

    major = root.find(".//a:fontScheme/a:majorFont/a:latin", NSMAP)
    minor = root.find(".//a:fontScheme/a:minorFont/a:latin", NSMAP)
    major_name = major.get("typeface") if major is not None else None
    minor_name = minor.get("typeface") if minor is not None else None
    return (major_name or "Calibri"), (minor_name or "Calibri")
