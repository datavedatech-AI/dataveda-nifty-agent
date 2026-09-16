import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    symbol_map_path = tmp_path / "symbol_map.json"
    symbol_map_path.write_text(json.dumps({"NIFTY": {"dhan": {"security_id": "13", "exchange_segment": "IDX_I"}}}))

    monkeypatch.setenv("SYMBOL_MAP_FILE", str(symbol_map_path))
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/trades.db")
    monkeypatch.setenv("WEBHOOK_ALLOWED_IPS", "")
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _payload(**overrides):
    payload = {
        "passphrase": "test-secret",
        "signal_id": "api-sig-1",
        "strategy": "ema_cross",
        "symbol": "NIFTY",
        "action": "buy",
        "quantity": 75,
    }
    payload.update(overrides)
    return payload


def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_rejects_bad_passphrase(client: TestClient):
    resp = client.post("/webhook/tradingview", json=_payload(passphrase="wrong"))
    assert resp.status_code == 401


def test_webhook_rejects_invalid_body(client: TestClient):
    resp = client.post("/webhook/tradingview", json=_payload(quantity=-1))
    assert resp.status_code == 422


def test_webhook_accepts_valid_signal_in_paper_mode(client: TestClient):
    resp = client.post("/webhook/tradingview", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_mode"] == "paper"
    assert body["results"][0]["status"] == "simulated"


def test_webhook_deduplicates_retried_signal(client: TestClient):
    first = client.post("/webhook/tradingview", json=_payload(signal_id="dup-api-1"))
    second = client.post("/webhook/tradingview", json=_payload(signal_id="dup-api-1"))

    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "simulated"
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "rejected"
    assert "Duplicate" in second.json()["results"][0]["message"]


def test_admin_status_requires_token(client: TestClient):
    resp = client.get("/admin/status")
    assert resp.status_code == 401


def test_admin_status_with_valid_token(client: TestClient):
    resp = client.get("/admin/status", headers={"X-Admin-Token": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["trading_mode"] == "paper"


def test_admin_kill_switch_toggle(client: TestClient):
    headers = {"X-Admin-Token": "test-secret"}
    resp = client.post("/admin/kill-switch", params={"engaged": True, "reason": "testing"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["engaged"] is True

    blocked = client.post("/webhook/tradingview", json=_payload(signal_id="after-kill-switch"))
    assert blocked.status_code == 200
    assert blocked.json()["results"][0]["status"] == "rejected"
