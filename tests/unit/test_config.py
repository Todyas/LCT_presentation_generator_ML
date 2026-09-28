from app.config import Settings, get_settings


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "custom-model-override")

    settings = get_settings()

    assert settings.llm_model == "custom-model-override"


def test_default_when_no_env_var_set(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)

    # get_settings()/Settings() reads the real repo-root .env by default
    # (pydantic-settings' env_file), which on a developer machine carries a
    # real LLM_MODEL override. Deleting only the process env var (above)
    # doesn't stop that file read, so this must disable the .env file
    # explicitly to observe the field's actual code default.
    settings = Settings(_env_file=None)

    assert settings.llm_model == "Qwen2.5-32B-Instruct"
