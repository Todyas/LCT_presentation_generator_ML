from app.models.audit_report import AuditIssue, AuditReport, IssueType, Severity


def _issue(severity: Severity, fixed: bool = False) -> AuditIssue:
    return AuditIssue(
        issue_type=IssueType.COLLISION,
        severity=severity,
        slide_index=0,
        message="test issue",
        fixed=fixed,
    )


def test_passed_is_false_with_unfixed_critical_issue():
    report = AuditReport(variant="A", issues=[_issue(Severity.CRITICAL, fixed=False)])

    assert report.passed is False


def test_passed_is_true_when_critical_issue_fixed():
    report = AuditReport(variant="A", issues=[_issue(Severity.CRITICAL, fixed=True)])

    assert report.passed is True


def test_passed_is_true_with_only_warnings():
    report = AuditReport(
        variant="A",
        issues=[_issue(Severity.WARNING), _issue(Severity.INFO)],
    )

    assert report.passed is True
