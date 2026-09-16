import datetime as dt
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.config import Settings, get_settings
from app.models.signal import TradingViewSignal
from app.security.auth import passphrase_matches
from app.services.order_router import process_signal
from app.storage.db import init_models, make_engine, make_session_factory
from app.storage.models import Tenant
from app.storage.repository import (
    create_tenant,
    get_tenant_by_agent_token,
    get_tenant_by_api_key,
    get_tenant_by_id,
    get_tenant_by_webhook_id,
    get_today_realized_pnl,
    grant_subscription,
    hash_secret,
    list_tenants,
    list_trades,
    regenerate_agent_token,
    regenerate_webhook_passphrase,
    revoke_subscription,
    set_kill_switch,
    update_risk_settings,
    update_symbol_map,
    utcnow_naive,
)
from app.ws.manager import ConnectionManager
from app.ws.relay_broker import WSRelayBroker

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level)

    engine = make_engine(settings.database_url)
    await init_models(engine)

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.ws_manager = ConnectionManager()

    logger.info("Started (database=%s)", settings.database_url.split("://")[0])
    yield

    await engine.dispose()


app = FastAPI(title="DataVeda MT5 Bridge", lifespan=lifespan)


async def get_db(request: Request):
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def get_current_tenant(
    authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)
) -> Tenant:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    api_key = authorization.removeprefix("Bearer ").strip()
    tenant = await get_tenant_by_api_key(db, api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return tenant


async def get_current_admin(x_admin_key: str = Header(default=""), settings: Settings = Depends(get_settings)) -> None:
    # The `not settings.admin_api_key` check must come first and short-
    # circuit: hmac.compare_digest("", "") is True, so without it, an
    # unset ADMIN_API_KEY plus a missing header would authorize anyone.
    if not settings.admin_api_key or not passphrase_matches(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin key")


@app.get("/health")
def health():
    return {"status": "ok"}


class SignupRequest(BaseModel):
    email: EmailStr


@app.post("/signup")
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    created = await create_tenant(db, payload.email)
    if created is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    tenant, secrets_once = created

    return {
        "tenant_id": tenant.id,
        "webhook_url": f"/webhook/tradingview/{secrets_once['webhook_id']}",
        "api_key": secrets_once["api_key"],
        "webhook_passphrase": secrets_once["webhook_passphrase"],
        "agent_token": secrets_once["agent_token"],
        "message": (
            "Save these now - api_key, webhook_passphrase, and agent_token are shown only once. "
            "Use api_key as a Bearer token for /me endpoints, webhook_passphrase in your TradingView "
            "alert JSON, and agent_token to start your bridge agent (bridge_agent/agent.py)."
        ),
    }


@app.get("/me")
async def get_me(request: Request, tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    now = utcnow_naive()
    return {
        "tenant_id": tenant.id,
        "email": tenant.email,
        "webhook_url": f"/webhook/tradingview/{tenant.webhook_id}",
        "symbol_map": tenant.symbol_map,
        "risk_settings": tenant.risk_settings,
        "plan": tenant.plan,
        "kill_switch_engaged": tenant.kill_switch_engaged,
        "kill_switch_reason": tenant.kill_switch_reason,
        "subscription_expires_at": tenant.subscription_expires_at.isoformat() if tenant.subscription_expires_at else None,
        "subscription_active": bool(tenant.subscription_expires_at and tenant.subscription_expires_at > now),
        "bridge_agent_connected": request.app.state.ws_manager.is_connected(tenant.id),
        "today_realized_pnl": await get_today_realized_pnl(db, tenant.id),
    }


class SymbolMapUpdate(BaseModel):
    symbol_map: dict[str, str]


@app.put("/me/symbol-map")
async def put_symbol_map(
    payload: SymbolMapUpdate, tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)
):
    normalized = {k.strip().upper(): v.strip() for k, v in payload.symbol_map.items()}
    tenant = await update_symbol_map(db, tenant, normalized)
    return {"symbol_map": tenant.symbol_map}


class RiskSettingsUpdate(BaseModel):
    max_qty_per_order: float | None = None
    max_daily_loss: float | None = None
    allowed_symbols: list[str] | None = None


@app.put("/me/risk-settings")
async def put_risk_settings(
    payload: RiskSettingsUpdate, tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)
):
    current = dict(tenant.risk_settings or {})
    updates = payload.model_dump(exclude_none=True)
    current.update(updates)
    tenant = await update_risk_settings(db, tenant, current)
    return {"risk_settings": tenant.risk_settings}


@app.post("/me/kill-switch")
async def post_kill_switch(
    engaged: bool, reason: str = "", tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)
):
    tenant = await set_kill_switch(db, tenant, engaged, reason)
    logger.warning("Tenant %s kill switch set to engaged=%s reason=%r", tenant.id, tenant.kill_switch_engaged, reason)
    return {"engaged": tenant.kill_switch_engaged, "reason": tenant.kill_switch_reason}


@app.get("/me/trades")
async def get_trades(
    limit: int = 50, offset: int = 0, tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)
):
    limit = max(1, min(limit, 200))
    trades = await list_trades(db, tenant.id, limit=limit, offset=max(0, offset))
    return {
        "trades": [
            {
                "signal_id": t.signal_id.removesuffix(":mt5"),
                "strategy": t.strategy,
                "symbol": t.symbol,
                "action": t.action,
                "side": t.side,
                "quantity": t.quantity,
                "status": t.status,
                "message": t.message,
                "broker_order_id": t.broker_order_id,
                "realized_pnl": t.realized_pnl,
                "created_at": t.created_at.isoformat(),
            }
            for t in trades
        ]
    }


@app.post("/me/regenerate-webhook-passphrase")
async def post_regenerate_webhook_passphrase(tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    tenant, new_passphrase = await regenerate_webhook_passphrase(db, tenant)
    return {
        "webhook_passphrase": new_passphrase,
        "message": "Save this now - it won't be shown again. Update your TradingView alert JSON with it.",
    }


@app.post("/me/regenerate-agent-token")
async def post_regenerate_agent_token(tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    tenant, new_token = await regenerate_agent_token(db, tenant)
    return {
        "agent_token": new_token,
        "message": (
            "Save this now - it won't be shown again. Restart bridge_agent.py with the new token; "
            "any agent still running with the old token will need to reconnect with this one."
        ),
    }


class SubscriptionGrant(BaseModel):
    days: int = 30


def _serialize_tenant_for_admin(tenant: Tenant, now: dt.datetime) -> dict:
    return {
        "tenant_id": tenant.id,
        "email": tenant.email,
        "plan": tenant.plan,
        "subscription_expires_at": tenant.subscription_expires_at.isoformat() if tenant.subscription_expires_at else None,
        "subscription_active": bool(tenant.subscription_expires_at and tenant.subscription_expires_at > now),
        "kill_switch_engaged": tenant.kill_switch_engaged,
        "created_at": tenant.created_at.isoformat(),
    }


@app.get("/admin/tenants", dependencies=[Depends(get_current_admin)])
async def admin_list_tenants(limit: int = 100, offset: int = 0, db: AsyncSession = Depends(get_db)):
    tenants = await list_tenants(db, limit=max(1, min(limit, 500)), offset=max(0, offset))
    now = utcnow_naive()
    return {"tenants": [_serialize_tenant_for_admin(t, now) for t in tenants]}


@app.post("/admin/tenants/{tenant_id}/subscription", dependencies=[Depends(get_current_admin)])
async def admin_grant_subscription(tenant_id: str, payload: SubscriptionGrant, db: AsyncSession = Depends(get_db)):
    if payload.days <= 0:
        raise HTTPException(status_code=422, detail="days must be positive")
    tenant = await get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    tenant = await grant_subscription(db, tenant, payload.days)
    logger.warning("Admin granted %d day(s) to tenant %s, now expires %s", payload.days, tenant.id, tenant.subscription_expires_at)
    return _serialize_tenant_for_admin(tenant, utcnow_naive())


@app.post("/admin/tenants/{tenant_id}/revoke-subscription", dependencies=[Depends(get_current_admin)])
async def admin_revoke_subscription(tenant_id: str, db: AsyncSession = Depends(get_db)):
    tenant = await get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    tenant = await revoke_subscription(db, tenant)
    logger.warning("Admin revoked subscription for tenant %s", tenant.id)
    return _serialize_tenant_for_admin(tenant, utcnow_naive())


@app.post("/webhook/tradingview/{webhook_id}")
async def tradingview_webhook(webhook_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    tenant = await get_tenant_by_webhook_id(db, webhook_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown webhook")

    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    if not passphrase_matches(hash_secret(str(raw.get("passphrase", ""))), tenant.webhook_passphrase_hash):
        logger.warning("Rejected webhook for tenant=%s: invalid passphrase", tenant.id)
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    try:
        signal = TradingViewSignal(**raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    settings: Settings = request.app.state.settings
    broker = WSRelayBroker(request.app.state.ws_manager, tenant.id, timeout=settings.bridge_order_timeout_seconds)
    result = await process_signal(signal, tenant, db, broker)

    return {
        "signal_id": signal.signal_id,
        "status": result.status.value,
        "broker_order_id": result.broker_order_id,
        "message": result.message,
    }


@app.websocket("/agent/ws")
async def agent_ws(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    session_factory = websocket.app.state.session_factory
    async with session_factory() as db:
        tenant = await get_tenant_by_agent_token(db, token)

    if tenant is None:
        await websocket.close(code=1008)
        return

    manager: ConnectionManager = websocket.app.state.ws_manager
    await manager.connect(tenant.id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "order_result":
                manager.resolve(message["request_id"], message)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(tenant.id)


_web_dir = Path(__file__).resolve().parent.parent / "web"


@app.get("/admin")
def admin_page():
    return FileResponse(str(_web_dir / "admin.html"))


# Mounted last so it never shadows an API route above - Starlette matches
# routes in registration order, and a "/" static mount would otherwise
# swallow everything.
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="dashboard")
