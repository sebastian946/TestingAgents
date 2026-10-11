"""LangSmith configuration: settings reach the SDK, tests never trace, a missing key is safe."""
import os

from config import variables
from config.variables import Settings, export_langsmith_env


def _settings(**overrides) -> Settings:
    base = {"db_user": "u", "db_password": "p", "db_name": "d", "endpoint": "localhost"}
    return Settings(_env_file=None, **base, **overrides)  # type: ignore[call-arg]


def test_the_test_session_never_traces():
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert variables.TRACING_ENABLED is False


def test_settings_are_exported_for_the_sdk(monkeypatch):
    for key in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    enabled = export_langsmith_env(_settings(langsmith_tracing=True, langsmith_api_key="lsv2_test", langsmith_project="Demo"))
    assert enabled is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_test"
    assert os.environ["LANGSMITH_PROJECT"] == "Demo"


def test_values_already_in_the_environment_win(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")  # e.g. forced off by tests or Docker
    monkeypatch.setenv("LANGSMITH_PROJECT", "FromDocker")
    enabled = export_langsmith_env(_settings(langsmith_tracing=True, langsmith_api_key="k", langsmith_project="FromDotenv"))
    assert enabled is False and os.environ["LANGSMITH_PROJECT"] == "FromDocker"


def test_tracing_without_a_key_is_turned_off_instead_of_failing(monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert export_langsmith_env(_settings(langsmith_tracing=True)) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert "tracing disabled" in capsys.readouterr().out


def test_the_designer_client_is_only_wrapped_when_tracing(monkeypatch):
    from agents import designer

    monkeypatch.setattr(designer.settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(designer, "TRACING_ENABLED", False)
    plain = designer._default_client()
    monkeypatch.setattr(designer, "TRACING_ENABLED", True)
    wrapped = designer._default_client()
    # wrap_anthropic replaces `create` on the instance; a plain client inherits it from the class
    assert "create" not in vars(plain.beta.messages)
    assert "create" in vars(wrapped.beta.messages)


def test_trace_config_labels_the_run_with_the_job():
    from worker.tasks import _trace_config

    config = _trace_config("1234", "https://shop.example/")
    assert config["run_name"] == "job 1234"
    assert config["metadata"]["job_id"] == "1234" and config["metadata"]["url"] == "https://shop.example/"
    assert any(tag.startswith("model:") for tag in config["tags"])


def test_other_langsmith_options_in_the_env_file_are_exported(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LANGSMITH_HIDE_INPUTS=true\nLANGSMITH_TRACING_SAMPLING_RATE=0.25\nOTHER=x\n", encoding="utf-8")
    for key in ("LANGSMITH_HIDE_INPUTS", "LANGSMITH_TRACING_SAMPLING_RATE", "OTHER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    s = _settings()
    monkeypatch.setitem(s.model_config, "env_file", str(env_file))
    export_langsmith_env(s)
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_TRACING_SAMPLING_RATE"] == "0.25"
    assert "OTHER" not in os.environ  # only LangSmith options
