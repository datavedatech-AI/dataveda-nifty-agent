import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.brokers.registry import build_brokers
from app.config import Settings, get_settings
from app.models.signal import TradingViewSignal
from app.risk.manager import RiskManager
from app.security.auth import ip_allowed, passphrase_matches
from app.services.order_router import process_signal
from app.storage.db import Base, make_engine, make_session_factory
from app.storage.repository import get_kill_switch, get_today_realized_pnl, set_kill_switch

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)

    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.state.brokers = build_brokers(settings)
    app.state.symbol_map = settings.load_symbol_map()
    app.state.risk_manager = RiskManager(settings)

    logger.info(
        "Started in %s mode with brokers=%s, %d symbols mapped",
        settings.trading_mode,
        list(app.state.brokers.keys()),
        len(app.state.symbol_map),
    )
    yield


app = FastAPI(title="DataVeda Nifty Agent", lifespan=lifespan)


def get_db(request: Request):
    session_factory = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def _require_admin(x_admin_token: str = Header(default=""), settings: Settings = Depends(get_settings)) -> None:
    # Personal-use agent: admin endpoints reuse the webhook passphrase as the
    # admin token. Rotate WEBHOOK_PASSPHRASE if you suspect it has leaked.
    if not passphrase_matches(x_admin_token, settings.webhook_passphrase):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _serialize_result(result) -> dict:
    d = asdict(result)
    d["status"] = result.status.value
    return d


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/tradingview")
async def tradingview_webhook(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    client_ip = request.client.host if request.client else ""

    if not ip_allowed(client_ip, settings.webhook_allowed_ips_list):
        logger.warning("Rejected webhook from disallowed IP %s", client_ip)
        raise HTTPException(status_code=403, detail="Source IP not allowed")

    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    if not passphrase_matches(str(raw.get("passphrase", "")), settings.webhook_passphrase):
        logger.warning("Rejected webhook with invalid passphrase from %s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    try:
        signal = TradingViewSignal(**raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    results = process_signal(
        signal,
        settings,
        request.app.state.brokers,
        request.app.state.symbol_map,
        db,
        request.app.state.risk_manager,
    )

    return {
        "signal_id": signal.signal_id,
        "trading_mode": settings.trading_mode,
        "results": [_serialize_result(r) for r in results],
    }


@app.get("/admin/status", dependencies=[Depends(_require_admin)])
def admin_status(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    kill_switch = get_kill_switch(db)
    return {
        "trading_mode": settings.trading_mode,
        "env_kill_switch": settings.kill_switch,
        "runtime_kill_switch_engaged": kill_switch.engaged,
        "runtime_kill_switch_reason": kill_switch.reason,
        "today_realized_pnl": get_today_realized_pnl(db),
        "enabled_brokers": list(request.app.state.brokers.keys()),
    }


@app.post("/admin/kill-switch", dependencies=[Depends(_require_admin)])
def admin_kill_switch(engaged: bool, reason: str = "", db: Session = Depends(get_db)):
    state = set_kill_switch(db, engaged, reason)
    logger.warning("Runtime kill switch set to engaged=%s reason=%r", state.engaged, state.reason)
    return {"engaged": state.engaged, "reason": state.reason}
