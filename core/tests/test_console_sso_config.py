"""P4 Task 6b: CONSOLE_SSO_PLATFORM_ORIGINS parsing (fail-closed) and the
/console/sso-config.json route the console reads before the postMessage hand-off.
Pure: no DB/Redis (TestClient without `with` never runs the lifespan)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, parse_platform_origins


def test_origins_accept_exact_wildcard_and_dev():
    raw = "https://sharwa.example, https://*.sharwa.example:8443,http://localhost:8000,http://*.localhost:8000"
    assert parse_platform_origins(raw) == (
        "https://sharwa.example", "https://*.sharwa.example:8443",
        "http://localhost:8000", "http://*.localhost:8000",
    )


def test_origins_reject_unsafe_values():
    for bad in ["*", "https://*", "https://*.com", "http://sharwa.example", "https://sharwa.example/",
                "https://sharwa.example/path", "https://Sharwa.example", "https://a*.sharwa.example",
                "https://sharwa.example?x=1", "https://ok.example,*"]:
        with pytest.raises(ConfigError, match="CONSOLE_SSO_PLATFORM_ORIGINS"):
            parse_platform_origins(bad)


def test_origins_empty_means_disabled():
    assert parse_platform_origins("") == ()
    assert parse_platform_origins(" , ") == ()


def _client(monkeypatch, tmp_path, origins):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    monkeypatch.setenv("CONSOLE_STATIC_DIR", str(tmp_path))
    if origins is None:
        monkeypatch.delenv("CONSOLE_SSO_PLATFORM_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CONSOLE_SSO_PLATFORM_ORIGINS", origins)
    import app.main as main_module
    return TestClient(main_module.create_app())


def test_sso_config_served_with_console_headers(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path, "https://*.sharwa.example").get("/console/sso-config.json")
    assert r.status_code == 200
    assert r.json() == {"platform_origins": ["https://*.sharwa.example"]}
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_sso_config_empty_when_unset(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path, None).get("/console/sso-config.json")
    assert r.status_code == 200 and r.json() == {"platform_origins": []}


def test_sso_config_absent_without_console(monkeypatch):
    monkeypatch.delenv("CONSOLE_STATIC_DIR", raising=False)
    import app.main as main_module
    assert TestClient(main_module.create_app()).get("/console/sso-config.json").status_code == 404
