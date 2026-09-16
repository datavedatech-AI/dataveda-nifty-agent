import datetime as dt

from app.storage.repository import (
    create_tenant,
    get_tenant_by_agent_token,
    get_tenant_by_api_key,
    get_tenant_by_id,
    get_tenant_by_webhook_id,
    grant_subscription,
    hash_secret,
    list_tenants,
    revoke_subscription,
    utcnow_naive,
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


async def test_new_tenant_has_no_subscription(db_session):
    tenant, _ = await create_tenant(db_session, "fresh@example.com")
    assert tenant.subscription_expires_at is None


async def test_grant_subscription_from_no_subscription(db_session):
    tenant, _ = await create_tenant(db_session, "grant1@example.com")
    before = utcnow_naive()
    tenant = await grant_subscription(db_session, tenant, days=30)

    assert tenant.subscription_expires_at is not None
    delta = tenant.subscription_expires_at - before
    assert dt.timedelta(days=29) < delta <= dt.timedelta(days=30, seconds=5)


async def test_grant_subscription_stacks_on_renewal(db_session):
    tenant, _ = await create_tenant(db_session, "grant2@example.com")
    tenant = await grant_subscription(db_session, tenant, days=30)
    first_expiry = tenant.subscription_expires_at

    tenant = await grant_subscription(db_session, tenant, days=30)

    assert tenant.subscription_expires_at == first_expiry + dt.timedelta(days=30)


async def test_grant_subscription_on_lapsed_account_starts_from_now(db_session):
    tenant, _ = await create_tenant(db_session, "grant3@example.com")
    tenant.subscription_expires_at = utcnow_naive() - dt.timedelta(days=100)
    await db_session.commit()

    before = utcnow_naive()
    tenant = await grant_subscription(db_session, tenant, days=30)

    # Renewing a long-lapsed account should not resurrect the old expiry
    # and stack 30 days onto a date 100 days in the past.
    delta = tenant.subscription_expires_at - before
    assert dt.timedelta(days=29) < delta <= dt.timedelta(days=30, seconds=5)


async def test_revoke_subscription_sets_immediate_cutoff(db_session):
    tenant, _ = await create_tenant(db_session, "revoke1@example.com")
    tenant = await grant_subscription(db_session, tenant, days=30)

    tenant = await revoke_subscription(db_session, tenant)

    assert tenant.subscription_expires_at <= utcnow_naive()


async def test_get_tenant_by_id(db_session):
    tenant, _ = await create_tenant(db_session, "byid@example.com")
    found = await get_tenant_by_id(db_session, tenant.id)
    assert found is not None
    assert found.email == "byid@example.com"

    assert await get_tenant_by_id(db_session, "does-not-exist") is None


async def test_list_tenants_newest_first(db_session):
    t1, _ = await create_tenant(db_session, "list1@example.com")
    t2, _ = await create_tenant(db_session, "list2@example.com")

    tenants = await list_tenants(db_session, limit=10)

    ids = [t.id for t in tenants]
    assert ids.index(t2.id) < ids.index(t1.id)
