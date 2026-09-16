from app.storage.repository import (
    create_tenant,
    get_tenant_by_agent_token,
    get_tenant_by_api_key,
    get_tenant_by_webhook_id,
    hash_secret,
)


async def test_create_tenant_returns_plaintext_secrets_once(db_session):
    created = await create_tenant(db_session, "trader@example.com")
    assert created is not None
    tenant, secrets_once = created

    assert tenant.email == "trader@example.com"
    assert tenant.api_key_hash == hash_secret(secrets_once["api_key"])
    assert tenant.webhook_passphrase_hash == hash_secret(secrets_once["webhook_passphrase"])
    assert tenant.agent_token_hash == hash_secret(secrets_once["agent_token"])
    assert tenant.symbol_map == {}
    assert tenant.risk_settings["max_qty_per_order"] == 1.0


async def test_duplicate_email_rejected(db_session):
    await create_tenant(db_session, "dup@example.com")
    second = await create_tenant(db_session, "dup@example.com")
    assert second is None


async def test_lookup_by_api_key(db_session):
    tenant, secrets_once = await create_tenant(db_session, "a@example.com")
    found = await get_tenant_by_api_key(db_session, secrets_once["api_key"])
    assert found is not None
    assert found.id == tenant.id

    assert await get_tenant_by_api_key(db_session, "wrong-key") is None


async def test_lookup_by_webhook_id(db_session):
    tenant, secrets_once = await create_tenant(db_session, "b@example.com")
    found = await get_tenant_by_webhook_id(db_session, secrets_once["webhook_id"])
    assert found is not None
    assert found.id == tenant.id


async def test_lookup_by_agent_token(db_session):
    tenant, secrets_once = await create_tenant(db_session, "c@example.com")
    found = await get_tenant_by_agent_token(db_session, secrets_once["agent_token"])
    assert found is not None
    assert found.id == tenant.id
