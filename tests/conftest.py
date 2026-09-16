import os

import pytest

os.environ.setdefault("WEBHOOK_PASSPHRASE", "test-secret")
os.environ.setdefault("ENABLED_BROKERS", "dhan")
os.environ.setdefault("DHAN_CLIENT_ID", "test-client")
os.environ.setdefault("DHAN_ACCESS_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TRADING_MODE", "paper")

from app.config import Settings, get_settings  # noqa: E402
from app.storage.db import Base, make_engine, make_session_factory  # noqa: E402


@pytest.fixture
def settings(monkeypatch) -> Settings:
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def db_session():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
