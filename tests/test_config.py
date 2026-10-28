from agentic_workflow_for_literature_synthesis.core.config import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.log_level == "INFO"
    assert settings.database_url is None


def test_settings_can_be_overridden_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.environment == "testing"
    assert settings.log_level == "DEBUG"
