from pathlib import Path

import pytest

from app.core.agents.prompt_registry import PromptRegistry

_V1_YAML = """\
id: x
version: 1
model_params:
  temperature: 0.1
  max_tokens: 100
system_prompt: "v1 system prompt"
user_template: "v1 {brief}"
output_schema_ref: app.models.outline.Outline
"""

_V2_YAML = """\
id: x
version: 2
model_params:
  temperature: 0.2
  max_tokens: 200
system_prompt: "v2 system prompt"
user_template: "v2 {brief}"
output_schema_ref: app.models.outline.Outline
"""


def test_load_latest_picks_highest_version(tmp_path: Path):
    skill_dir = tmp_path / "x"
    skill_dir.mkdir()
    (skill_dir / "v1.yaml").write_text(_V1_YAML, encoding="utf-8")
    (skill_dir / "v2.yaml").write_text(_V2_YAML, encoding="utf-8")

    registry = PromptRegistry(str(tmp_path))
    result = registry.load("x", "latest")

    assert result.version == 2
    assert result.system_prompt == "v2 system prompt"
    assert result.user_template == "v2 {brief}"


def test_load_missing_version_raises(tmp_path: Path):
    skill_dir = tmp_path / "x"
    skill_dir.mkdir()
    (skill_dir / "v1.yaml").write_text(_V1_YAML, encoding="utf-8")

    registry = PromptRegistry(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        registry.load("x", 99)
