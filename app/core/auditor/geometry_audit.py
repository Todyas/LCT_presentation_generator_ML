from __future__ import annotations

from dataclasses import dataclass

from app.models.audit_report import AuditIssue, IssueType, Severity
from app.models.audit_report import BBox as AuditBBox


@dataclass(frozen=True)
class BBox:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h


def intersection(a: BBox, b: BBox, tolerance: float = 0.0) -> BBox | None:
    ix1 = max(a.x, b.x) - tolerance
    iy1 = max(a.y, b.y) - tolerance
    ix2 = min(a.x2, b.x2) + tolerance
    iy2 = min(a.y2, b.y2) + tolerance
    if ix1 < ix2 and iy1 < iy2:
        return BBox(ix1, iy1, ix2 - ix1, iy2 - iy1)
    return None


def find_collisions(
    shapes: list[tuple[str, BBox]], tolerance: float = 0.0
) -> list[tuple[str, str, BBox]]:
    out: list[tuple[str, str, BBox]] = []
    for i in range(len(shapes)):
        id_a, box_a = shapes[i]
        for j in range(i + 1, len(shapes)):
            id_b, box_b = shapes[j]
            overlap = intersection(box_a, box_b, tolerance)
            if overlap is not None:
                out.append((id_a, id_b, overlap))
    return out


def is_within_bounds(box: BBox, slide_w: float, slide_h: float) -> bool:
    return box.x >= 0 and box.y >= 0 and box.x2 <= slide_w and box.y2 <= slide_h


def run_geometry_audit(
    bbox_map: dict[str, BBox], slide_w: float, slide_h: float, slide_index: int
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    shapes = list(bbox_map.items())

    per_shape_tolerance = lambda box: -0.01 * min(box.w, box.h)

    for i in range(len(shapes)):
        id_a, box_a = shapes[i]
        for j in range(i + 1, len(shapes)):
            id_b, box_b = shapes[j]
            tol = min(per_shape_tolerance(box_a), per_shape_tolerance(box_b))
            overlap = intersection(box_a, box_b, tolerance=tol)
            if overlap is not None:
                issues.append(
                    AuditIssue(
                        issue_type=IssueType.COLLISION,
                        severity=Severity.CRITICAL,
                        slide_index=slide_index,
                        message=f"shapes {id_a!r} and {id_b!r} overlap",
                        bbox=AuditBBox(x=overlap.x, y=overlap.y, w=overlap.w, h=overlap.h),
                        shape_ids=[id_a, id_b],
                        auto_fixable=False,
                    )
                )

    for shape_id, box in shapes:
        if not is_within_bounds(box, slide_w, slide_h):
            issues.append(
                AuditIssue(
                    issue_type=IssueType.OVERFLOW,
                    severity=Severity.CRITICAL,
                    slide_index=slide_index,
                    message=f"shape {shape_id!r} extends past slide bounds",
                    bbox=AuditBBox(x=box.x, y=box.y, w=box.w, h=box.h),
                    shape_ids=[shape_id],
                    auto_fixable=True,
                )
            )

    return issues
