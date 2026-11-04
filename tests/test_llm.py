from agents.llm import get_llm


def test_runs_offline_without_a_key(monkeypatch, capsys):
    monkeypatch.setattr("config.MODEL", "xai:any-model")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert get_llm() is None
    assert "offline simulation" in capsys.readouterr().out


def test_key_loads_the_configured_model(monkeypatch):
    monkeypatch.setattr("config.MODEL", "xai:any-model")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    assert get_llm() is not None