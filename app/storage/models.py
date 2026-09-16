import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)

    # All three secrets below are stored as SHA-256 hashes, never plaintext -
    # the plaintext values are shown once, at signup, and never persisted.
    api_key_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    webhook_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    webhook_passphrase_hash: Mapped[str] = mapped_column(String)
    agent_token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)

    # {"NIFTY": "some_mt5_symbol", "EURUSD": "EURUSD"} - TradingView symbol -> MT5 symbol
    symbol_map: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"max_qty_per_order": 1.0, "max_daily_loss": 1000.0, "allowed_symbols": [...]}
    risk_settings: Mapped[dict] = mapped_column(JSON, default=dict)

    plan: Mapped[str] = mapped_column(String, default="free")
    kill_switch_engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(String, default="")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))


class TradeLog(Base):
    __tablename__ = "trade_log"
    __table_args__ = (UniqueConstraint("tenant_id", "signal_id", name="uq_tenant_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    signal_id: Mapped[str] = mapped_column(String, index=True)
    strategy: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String, default="")
    broker_order_id: Mapped[str] = mapped_column(String, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))
