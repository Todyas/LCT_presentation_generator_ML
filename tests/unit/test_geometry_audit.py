from app.core.auditor.geometry_audit import BBox, find_collisions, run_geometry_audit
from app.models.audit_report import IssueType


def test_touching_edges_not_flagged_as_collision():
    box_a = BBox(x=0, y=0, w=100, h=100)
    box_b = BBox(x=100, y=0, w=100, h=100)

    result = find_collisions([("a", box_a), ("b", box_b)])

    assert result == []


def test_run_geometry_audit_flags_real_overlap():
    bbox_map = {
        "component_0": BBox(x=0, y=0, w=100, h=100),
        "component_1": BBox(x=50, y=50, w=100, h=100),
    }

    issues = run_geometry_audit(bbox_map, slide_w=1000, slide_h=1000, slide_index=0)

    collisions = [i for i in issues if i.issue_type == IssueType.COLLISION]
    assert len(collisions) == 1
    assert set(collisions[0].shape_ids) == {"component_0", "component_1"}


def test_run_geometry_audit_flags_overflow():
    bbox_map = {"component_0": BBox(x=950, y=0, w=100, h=50)}

    issues = run_geometry_audit(bbox_map, slide_w=1000, slide_h=1000, slide_index=0)

    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.OVERFLOW
    assert issues[0].auto_fixable is True


def test_run_geometry_audit_clean_deck_returns_empty():
    bbox_map = {
        "title": BBox(x=0, y=0, w=200, h=100),
        "component_0": BBox(x=0, y=200, w=200, h=100),
    }

    issues = run_geometry_audit(bbox_map, slide_w=1000, slide_h=1000, slide_index=0)

    assert issues == []
