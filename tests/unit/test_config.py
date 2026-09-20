from app.config import get_settings


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "custom-model-override")

    settings = get_settings()

    assert settings.llm_model == "custom-model-override"


def test_default_when_no_env_var_set(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = get_settings()

    assert settings.llm_model == "Qwen2.5-32B-Instruct"
