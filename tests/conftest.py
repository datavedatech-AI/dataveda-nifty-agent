import pytest_asyncio

from app.storage.db import init_models, make_engine, make_session_factory


@pytest_asyncio.fixture
async def db_session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    factory = make_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()
