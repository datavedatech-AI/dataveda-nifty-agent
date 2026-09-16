from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server-wide configuration. Per-tenant secrets (broker credentials,
    webhook passphrase, agent token) live in the database, not here - this
    is a hosted multi-tenant service, so tenant config can't be a static
    .env file.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="sqlite+aiosqlite:///./data/tenants.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # How long the webhook handler waits for a tenant's bridge agent to
    # acknowledge an order over the WebSocket relay before giving up.
    bridge_order_timeout_seconds: float = Field(default=8.0, alias="BRIDGE_ORDER_TIMEOUT_SECONDS")

    # Grants access to /admin/* (list tenants, grant/revoke subscriptions).
    # Left blank by default so admin endpoints fail closed until you set
    # this - never treat an empty value as "no auth required".
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
