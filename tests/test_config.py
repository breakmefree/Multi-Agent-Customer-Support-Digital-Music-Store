"""Environment configuration tests."""

from src.config import Settings


def test_settings_read_environment_variables(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("PORT", "9000")

    settings = Settings()

    assert settings.llm_model == "test-model"
    assert settings.llm_api_key == "test-key"
    assert settings.llm_base_url == "https://example.test/v1"
    assert settings.llm_temperature == 0.25
    assert settings.port == 9000