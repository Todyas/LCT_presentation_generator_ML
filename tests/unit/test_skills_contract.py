"""Contract tests for skills/*/v*.yaml and app/core/agents/prompt_registry.py."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from app.core.agents.prompt_registry import PromptRegistry, PromptSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

ALL_SKILL_FILES = sorted(SKILLS_DIR.glob("*/v*.yaml"))

# The exact kwargs each calling agent passes to `spec.user_template.format(...)`,
# read from the agent source (narrative_architect.py, slot_filler.py,
# slide_reviser.py, semantic_audit.py).
_TEMPLATE_KWARGS: dict[str, dict[str, str]] = {
    "narrative_architect": {
        "brief": "A sufficiently detailed brief.",
        "variant_description": "Executive: decision-oriented.",
        "n_slides_min": "10",
        "n_slides_max": "15",
        "available_layout_types": "CONTENT_1COL, CONTENT_2COL, COMPARISON",
    },
    "slot_filler": {
        "brief": "A sufficiently detailed brief.",
        "variant_description": "Executive: decision-oriented.",
        "slide_index": "3",
        "working_title": "Working title",
        "key_message": "Key message",
        "content_hint": "Content hint",
        "requested_layout_type": "CONTENT_1COL",
        "resolved_layout_type": "CONTENT_1COL",
        "allowed_component_types": "bullet_block",
        "retry_feedback": "",
    },
    "slide_reviser": {
        "brief": "A sufficiently detailed brief.",
        "current_slide_json": '{"slide_index": 0}',
        "instructions": "Shorten the text.",
        "available_layout_types": "CONTENT_1COL, CONTENT_2COL",
        "retry_feedback": "",
    },
    "semantic_audit": {
        "brief": "A sufficiently detailed brief.",
        "deck_json": '{"slides": []}',
    },
}


@pytest.mark.parametrize(
    "path", ALL_SKILL_FILES, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_skill_file_parses_into_prompt_spec(path):
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    spec = PromptSpec.model_validate(data)

    assert spec.id == path.parent.name
    assert str(spec.version) == path.stem.lstrip("v")


@pytest.mark.parametrize(
    "path", ALL_SKILL_FILES, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_output_schema_ref_imports_to_a_real_pydantic_class(path):
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = PromptSpec.model_validate(data)

    module_path, _, class_name = spec.output_schema_ref.rpartition(".")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    assert issubclass(cls, BaseModel)


@pytest.mark.parametrize(
    "path", ALL_SKILL_FILES, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_user_template_formats_without_error_using_the_agents_actual_kwargs(path):
    skill_id = path.parent.name
    kwargs = _TEMPLATE_KWARGS.get(skill_id)
    if kwargs is None:
        pytest.skip(f"no known caller kwargs registered for skill {skill_id!r}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = PromptSpec.model_validate(data)

    try:
        rendered = spec.user_template.format(**kwargs)
    except (KeyError, IndexError) as exc:
        pytest.fail(f"{path}: user_template.format() raised {exc!r}")

    # Every non-empty kwarg the caller passes should actually show up in the
    # rendered prompt; anything missing means the template dropped a field
    # the calling agent relies on.
    for value in kwargs.values():
        if value:
            assert value in rendered


def test_load_latest_picks_the_numeric_maximum_not_lexicographic(tmp_path):
    skill_dir = tmp_path / "dummy_skill"
    skill_dir.mkdir()
    base = {
        "id": "dummy_skill",
        "model_params": {},
        "system_prompt": "sys",
        "user_template": "usr",
        "output_schema_ref": "app.models.outline.Outline",
    }
    for version in (1, 9, 10, 2):
        payload = {**base, "version": version}
        (skill_dir / f"v{version}.yaml").write_text(
            yaml.safe_dump(payload), encoding="utf-8"
        )

    registry = PromptRegistry(str(tmp_path))
    spec = registry.load("dummy_skill", "latest")

    assert spec.version == 10
