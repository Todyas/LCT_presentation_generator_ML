from app.core.auditor.contrast_audit import (
    check_wcag_aa,
    contrast_ratio,
    run_contrast_audit,
)
from app.models.audit_report import Severity


def test_check_wcag_aa_boundary():
    ratio = contrast_ratio("767676", "FFFFFF")

    assert 4.4 <= ratio <= 4.6
    assert check_wcag_aa("767676", "FFFFFF") is True


def test_black_on_white_passes():
    assert check_wcag_aa("000000", "FFFFFF") is True


def test_light_gray_on_white_fails():
    assert check_wcag_aa("EEEEEE", "FFFFFF") is False


def test_run_contrast_audit_returns_none_when_passing():
    issue = run_contrast_audit(
        text_color_hex="000000",
        bg_color_hex="FFFFFF",
        is_large_text=False,
        slide_index=0,
        shape_id="title",
    )

    assert issue is None


def test_run_contrast_audit_returns_critical_issue_when_failing():
    issue = run_contrast_audit(
        text_color_hex="EEEEEE",
        bg_color_hex="FFFFFF",
        is_large_text=False,
        slide_index=0,
        shape_id="title",
    )

    assert issue is not None
    assert issue.severity == Severity.CRITICAL
    assert issue.auto_fixable is True
    assert "title" in issue.message
