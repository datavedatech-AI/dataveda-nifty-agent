from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


ADMIN_KEY = "test-admin-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/tenants.db")
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
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


def _admin_headers(key: str = ADMIN_KEY) -> dict:
    return {"X-Admin-Key": key}


def _grant_subscription(client: TestClient, account: dict, days: int = 30) -> dict:
    resp = client.post(f"/admin/tenants/{account['tenant_id']}/subscription", json={"days": days}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


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
    _grant_subscription(client, account)
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
    _grant_subscription(client, account)
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
    _grant_subscription(client, account)
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
    _grant_subscription(client, account)
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


def test_trades_empty_initially(client: TestClient):
    account = _signup(client)
    resp = client.get("/me/trades", headers=_auth_headers(account))
    assert resp.status_code == 200
    assert resp.json()["trades"] == []


def test_trades_lists_after_a_signal(client: TestClient):
    account = _signup(client)
    _grant_subscription(client, account)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))
    client.post(
        account["webhook_url"],
        json={
            "passphrase": account["webhook_passphrase"],
            "signal_id": "trade-log-1",
            "strategy": "s",
            "symbol": "EURUSD",
            "action": "buy",
            "quantity": 0.1,
        },
    )

    resp = client.get("/me/trades", headers=_auth_headers(account))
    trades = resp.json()["trades"]
    assert len(trades) == 1  # the internal dedup "reservation" row is filtered out
    assert trades[0]["signal_id"] == "trade-log-1"
    assert trades[0]["status"] == "rejected"  # no bridge agent connected


def test_trades_requires_auth(client: TestClient):
    resp = client.get("/me/trades")
    assert resp.status_code == 401


def test_trades_scoped_per_tenant(client: TestClient):
    account_a = _signup(client, "a@example.com")
    account_b = _signup(client, "b@example.com")
    _grant_subscription(client, account_a)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account_a))
    client.post(
        account_a["webhook_url"],
        json={
            "passphrase": account_a["webhook_passphrase"],
            "signal_id": "only-a",
            "strategy": "s",
            "symbol": "EURUSD",
            "action": "buy",
            "quantity": 0.1,
        },
    )

    resp_b = client.get("/me/trades", headers=_auth_headers(account_b))
    assert resp_b.json()["trades"] == []


def test_regenerate_webhook_passphrase_invalidates_old_one(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))

    resp = client.post("/me/regenerate-webhook-passphrase", headers=_auth_headers(account))
    assert resp.status_code == 200
    new_passphrase = resp.json()["webhook_passphrase"]
    assert new_passphrase != account["webhook_passphrase"]

    old_attempt = client.post(
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
    assert old_attempt.status_code == 401

    new_attempt = client.post(
        account["webhook_url"],
        json={
            "passphrase": new_passphrase,
            "signal_id": "s2",
            "strategy": "s",
            "symbol": "EURUSD",
            "action": "buy",
            "quantity": 0.1,
        },
    )
    assert new_attempt.status_code == 200


def test_regenerate_agent_token_invalidates_old_one(client: TestClient):
    account = _signup(client)

    resp = client.post("/me/regenerate-agent-token", headers=_auth_headers(account))
    assert resp.status_code == 200
    new_token = resp.json()["agent_token"]
    assert new_token != account["agent_token"]

    # Old token can no longer open the bridge agent's WebSocket connection -
    # the server closes it during the handshake, which TestClient surfaces
    # as a WebSocketDisconnect raised by the `with` block itself.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/agent/ws?token={account['agent_token']}"):
            pass

    # New token connects fine.
    with client.websocket_connect(f"/agent/ws?token={new_token}") as ws:
        me = client.get("/me", headers=_auth_headers(account)).json()
        assert me["bridge_agent_connected"] is True


# ---------------------------------------------------------------- subscription / admin


def test_new_account_cannot_trade_until_granted(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))

    me = client.get("/me", headers=_auth_headers(account)).json()
    assert me["subscription_active"] is False
    assert me["subscription_expires_at"] is None

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
    assert resp.json()["status"] == "rejected"
    assert "No active subscription" in resp.json()["message"]


def test_admin_grant_unblocks_trading(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))
    _grant_subscription(client, account, days=30)

    me = client.get("/me", headers=_auth_headers(account)).json()
    assert me["subscription_active"] is True

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
    # Reaches the actual broker-dispatch step now, rejected only because no
    # bridge agent is connected - not because of the subscription gate.
    assert resp.json()["status"] == "rejected"
    assert "No bridge agent connected" in resp.json()["message"]


def test_admin_revoke_reblocks_trading(client: TestClient):
    account = _signup(client)
    client.put("/me/symbol-map", json={"symbol_map": {"EURUSD": "EURUSD"}}, headers=_auth_headers(account))
    _grant_subscription(client, account, days=30)

    resp = client.post(f"/admin/tenants/{account['tenant_id']}/revoke-subscription", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["subscription_active"] is False

    webhook_resp = client.post(
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
    assert "No active subscription" in webhook_resp.json()["message"]


def test_grant_subscription_stacks_via_api(client: TestClient):
    account = _signup(client)
    first = _grant_subscription(client, account, days=30)
    second = _grant_subscription(client, account, days=30)
    assert second["subscription_expires_at"] > first["subscription_expires_at"]


def test_admin_endpoints_require_key(client: TestClient):
    assert client.get("/admin/tenants").status_code == 401
    assert client.post("/admin/tenants/does-not-matter/subscription", json={"days": 30}).status_code == 401
    assert client.post("/admin/tenants/does-not-matter/revoke-subscription").status_code == 401


def test_admin_endpoints_reject_wrong_key(client: TestClient):
    resp = client.get("/admin/tenants", headers=_admin_headers("wrong-key"))
    assert resp.status_code == 401


def test_admin_endpoints_fail_closed_when_admin_key_unset(client: TestClient, monkeypatch):
    # Regression guard: hmac.compare_digest("", "") is True, so the auth
    # dependency must reject on an unset ADMIN_API_KEY before it ever
    # compares against the (possibly also empty) provided header.
    monkeypatch.setenv("ADMIN_API_KEY", "")
    get_settings.cache_clear()
    try:
        resp = client.get("/admin/tenants", headers=_admin_headers(""))
        assert resp.status_code == 401
    finally:
        monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
        get_settings.cache_clear()


def test_admin_grant_unknown_tenant_404(client: TestClient):
    resp = client.post("/admin/tenants/does-not-exist/subscription", json={"days": 30}, headers=_admin_headers())
    assert resp.status_code == 404


def test_admin_list_tenants(client: TestClient):
    account = _signup(client, "list-me@example.com")
    _grant_subscription(client, account, days=30)

    resp = client.get("/admin/tenants", headers=_admin_headers())
    assert resp.status_code == 200
    emails = [t["email"] for t in resp.json()["tenants"]]
    assert "list-me@example.com" in emails
