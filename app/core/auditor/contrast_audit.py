from __future__ import annotations

from app.models.audit_report import AuditIssue, IssueType, Severity


def _srgb_to_linear(channel_0_255: int) -> float:
    c = channel_0_255 / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    r_lin, g_lin, b_lin = (_srgb_to_linear(v) for v in (r, g, b))
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    l1, l2 = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_wcag_aa(text_hex: str, bg_hex: str, large_text: bool = False) -> bool:
    threshold = 3.0 if large_text else 4.5
    return contrast_ratio(text_hex, bg_hex) >= threshold


def run_contrast_audit(
    text_color_hex: str,
    bg_color_hex: str,
    is_large_text: bool,
    slide_index: int,
    shape_id: str,
) -> AuditIssue | None:
    if check_wcag_aa(text_color_hex, bg_color_hex, is_large_text):
        return None
    ratio = contrast_ratio(text_color_hex, bg_color_hex)
    threshold = 3.0 if is_large_text else 4.5
    return AuditIssue(
        issue_type=IssueType.CONTRAST,
        severity=Severity.CRITICAL,
        slide_index=slide_index,
        message=f"contrast ratio {ratio:.2f} below required {threshold} for shape {shape_id!r}",
        shape_ids=[shape_id],
        auto_fixable=True,
    )
