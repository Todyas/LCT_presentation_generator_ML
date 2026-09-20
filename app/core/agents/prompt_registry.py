from __future__ import annotations

import glob
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class PromptSpec(BaseModel):
    id: str
    version: int
    model_params: dict
    system_prompt: str
    user_template: str
    output_schema_ref: str


class PromptRegistry:
    def __init__(self, skills_dir: str = "skills") -> None:
        self._skills_dir = Path(skills_dir)

    def load(self, skill_id: str, version: int | Literal["latest"] = "latest") -> PromptSpec:
        skill_dir = self._skills_dir / skill_id
        if version == "latest":
            version = self._resolve_latest_version(skill_dir)
        path = skill_dir / f"v{version}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"prompt spec not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return PromptSpec.model_validate(data)

    def _resolve_latest_version(self, skill_dir: Path) -> int:
        pattern = str(skill_dir / "v*.yaml")
        versions = []
        for p in glob.glob(pattern):
            stem = Path(p).stem
            versions.append(int(stem.lstrip("v")))
        if not versions:
            raise FileNotFoundError(f"no prompt versions found in {skill_dir}")
        return max(versions)
