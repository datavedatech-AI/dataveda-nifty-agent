"""Operator tool: reset a tenant's api_key / webhook_passphrase / agent_token.

There is currently no self-service account recovery (a lost api_key can't
be looked up - only its hash is stored). Until that's built, the platform
owner can run this locally against the server's database to issue a
tenant fresh credentials without losing their account (subscription,
symbol map, trade history, etc. are all preserved).

Usage (from the project root, with the venv active):
    python scripts/reset_tenant_credentials.py someone@example.com

Prints the new api_key, webhook_id, webhook_passphrase, and agent_token
exactly once - save them immediately, same as at signup.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.storage.db import make_engine, make_session_factory
from app.storage.repository import hash_secret, _generate_id
from app.storage.models import Tenant
from sqlalchemy import select
import secrets


async def reset_credentials(email: str) -> None:
    engine = make_engine(get_settings().database_url)
    session_factory = make_session_factory(engine)

    async with session_factory() as session:
        result = await session.execute(select(Tenant).where(Tenant.email == email))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            print(f"No tenant found with email {email!r}")
            return

        api_key = _generate_id("sk_live", 24)
        webhook_passphrase = secrets.token_urlsafe(18)
        agent_token = _generate_id("agent", 24)

        tenant.api_key_hash = hash_secret(api_key)
        tenant.webhook_passphrase_hash = hash_secret(webhook_passphrase)
        tenant.agent_token_hash = hash_secret(agent_token)
        await session.commit()

        print(f"Credentials reset for {email} (tenant_id={tenant.id})")
        print(f"  api_key:            {api_key}")
        print(f"  webhook_id:         {tenant.webhook_id}")
        print(f"  webhook_passphrase: {webhook_passphrase}")
        print(f"  agent_token:        {agent_token}")
        print("\nSave these now - shown only once.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/reset_tenant_credentials.py <email>")
        sys.exit(1)
    asyncio.run(reset_credentials(sys.argv[1]))
