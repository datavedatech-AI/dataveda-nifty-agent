from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/tenants.db")
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _signup(client: TestClient, email: str = "trader@example.com") -> dict:
    resp = client.post("/signup", json={"email": email})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(account: dict) -> dict:
    return {"Authorization": f"Bearer {account['api_key']}"}


def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_signup_returns_secrets_once(client: TestClient):
    account = _signup(client)
    assert account["api_key"].startswith("sk_live_")
    assert account["agent_token"].startswith("agent_")
    assert "/webhook/tradingview/" in account["webhook_url"]


def test_signup_duplicate_email_rejected(client: TestClient):
    _signup(client, "dup@example.com")
    resp = client.post("/signup", json={"email": "dup@example.com"})
    assert resp.status_code == 409


def test_me_requires_auth(client: TestClient):
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_valid_key(client: TestClient):
    account = _signup(client)
    resp = client.get("/me", headers=_auth_headers(account))
    assert resp.status_code == 200
    assert resp.json()["bridge_agent_connected"] is False


def test_update_symbol_map(client: TestClient):
    account = _signup(client)
    resp = client.put("/me/symbol-map", json={"symbol_map": {"eurusd": " EURUSD "}}, headers=_auth_headers(account))
    assert resp.status_code == 200
    assert resp.json()["symbol_map"] == {"EURUSD": "EURUSD"}


def test_webhook_unknown_id_404(client: TestClient):
    resp = client.post("/webhook/tradingview/does-not-exist", json={"passphrase": "x"})
    assert resp.status_code == 404


def test_webhook_wrong_passphrase_401(client: TestClient):
    account = _signup(client)
    webhook_path = account["webhook_url"]
    resp = client.post(webhook_path, json={"passphrase": "wrong", "signal_id": "s1", "strategy": "s", "symbol": "EURUSD", "action": "buy", "quantity": 0.1})
    assert resp.status_code == 401


def test_webhook_without_bridge_agent_connected(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))

    resp = client.post(
        account["webhook_url"],
        json={
            "passphrase": account["webhook_passphrase"],
            "signal_id": "s1",
            "strategy": "s",
            "symbol": "EURUSD",
            "action": "buy",
            "quantity": 0.1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "No bridge agent connected" in body["message"]


def test_webhook_full_round_trip_with_connected_agent(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))

    with client.websocket_connect(f"/agent/ws?token={account['agent_token']}") as agent_ws:
        # The webhook handler blocks (awaiting the agent's reply) until we
        # answer over the WebSocket below, so it has to run on a separate
        # thread while this thread plays the role of the agent.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post,
                account["webhook_url"],
                json={
                    "passphrase": account["webhook_passphrase"],
                    "signal_id": "round-trip-1",
                    "strategy": "s",
                    "symbol": "EURUSD",
                    "action": "buy",
                    "quantity": 0.1,
                },
            )

            order_message = agent_ws.receive_json()
            assert order_message["type"] == "place_order"
            assert order_message["symbol"] == "EURUSD"
            assert order_message["side"] == "buy"

            agent_ws.send_json(
                {"type": "order_result", "request_id": order_message["request_id"], "status": "accepted", "order_id": "77"}
            )

            resp = future.result(timeout=5)

    body = resp.json()
    assert body["status"] == "accepted"
    assert body["broker_order_id"] == "77"


def test_kill_switch_blocks_webhook(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))
    client.post("/me/kill-switch", params={"engaged": True, "reason": "testing"}, headers=_auth_headers(account))

    resp = client.post(
        account["webhook_url"],
        json={
            "passphrase": account["webhook_passphrase"],
            "signal_id": "s-after-kill",
            "strategy": "s",
            "symbol": "EURUSD",
            "action": "buy",
            "quantity": 0.1,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert "Kill switch" in resp.json()["message"]


def test_webhook_duplicate_signal_rejected(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))
    payload = {
        "passphrase": account["webhook_passphrase"],
        "signal_id": "dup-api-1",
        "strategy": "s",
        "symbol": "EURUSD",
        "action": "buy",
        "quantity": 0.1,
    }

    first = client.post(account["webhook_url"], json=payload)
    second = client.post(account["webhook_url"], json=payload)

    assert first.json()["status"] == "rejected"  # no agent connected, but signal_id is now reserved
    assert second.json()["status"] == "rejected"
    assert "Duplicate" in second.json()["message"]
