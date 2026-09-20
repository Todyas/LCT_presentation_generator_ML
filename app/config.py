from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "Qwen2.5-32B-Instruct"
    llm_api_key: str = "EMPTY"
    llm_max_concurrency: int = 4

    n_slides_min: int = 10
    n_slides_max: int = 15
    slot_filler_max_retries: int = 2

    pipeline_timeout_seconds: int = 300
    pdf_export_timeout_seconds: int = 60

    storage_dir: str = "storage"
    skills_dir: str = "skills"


def get_settings() -> Settings:
    return Settings()
