from app.config import Settings
from app.models.signal import TradingViewSignal
from app.risk.manager import RiskManager
from app.storage.repository import log_trade, set_kill_switch
from tests.fakes import FakeBroker


def make_settings(**overrides) -> Settings:
    base = dict(
        WEBHOOK_PASSPHRASE="secret",
        ENABLED_BROKERS="fake",
        RISK_MAX_QTY_PER_ORDER=100,
        RISK_MAX_OPEN_POSITIONS=5,
        RISK_MAX_DAILY_LOSS=1000,
        RISK_ALLOWED_SYMBOLS="",
        KILL_SWITCH=False,
    )
    base.update(overrides)
    return Settings(**base)


def make_signal(**overrides) -> TradingViewSignal:
    base = dict(
        passphrase="secret",
        signal_id="sig-1",
        strategy="s1",
        symbol="NIFTY",
        action="buy",
        quantity=50,
    )
    base.update(overrides)
    return TradingViewSignal(**base)


def test_allows_when_everything_ok(db_session):
    settings = make_settings()
    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {"fake": FakeBroker()})
    assert result.allowed is True


def test_env_kill_switch_blocks(db_session):
    settings = make_settings(KILL_SWITCH=True)
    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {})
    assert result.allowed is False
    assert "Kill switch" in result.reason


def test_runtime_kill_switch_blocks(db_session):
    settings = make_settings()
    set_kill_switch(db_session, True, "manual halt")
    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {})
    assert result.allowed is False
    assert "manual halt" in result.reason


def test_symbol_allowlist_blocks(db_session):
    settings = make_settings(RISK_ALLOWED_SYMBOLS="BANKNIFTY")
    manager = RiskManager(settings)
    result = manager.check(make_signal(symbol="NIFTY"), db_session, {})
    assert result.allowed is False
    assert "not in RISK_ALLOWED_SYMBOLS" in result.reason


def test_max_qty_blocks(db_session):
    settings = make_settings(RISK_MAX_QTY_PER_ORDER=10)
    manager = RiskManager(settings)
    result = manager.check(make_signal(quantity=50), db_session, {})
    assert result.allowed is False
    assert "exceeds RISK_MAX_QTY_PER_ORDER" in result.reason


def test_daily_loss_circuit_breaker_blocks(db_session):
    settings = make_settings(RISK_MAX_DAILY_LOSS=500)
    entry = log_trade(
        db_session,
        signal_id="loss-1",
        strategy="s1",
        symbol="NIFTY",
        action="sell",
        side="sell",
        quantity=75,
        broker="fake",
        status="accepted",
    )
    entry.realized_pnl = -600
    db_session.commit()

    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {})
    assert result.allowed is False
    assert "Daily loss circuit breaker" in result.reason


def test_max_open_positions_blocks(db_session):
    settings = make_settings(RISK_MAX_OPEN_POSITIONS=1)
    broker = FakeBroker(positions=[{"symbol": "NIFTY"}, {"symbol": "BANKNIFTY"}])
    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {"fake": broker})
    assert result.allowed is False
    assert "RISK_MAX_OPEN_POSITIONS" in result.reason


def test_broker_position_lookup_failure_does_not_crash(db_session):
    class BrokenBroker(FakeBroker):
        def get_open_positions(self):
            raise RuntimeError("network down")

    settings = make_settings()
    manager = RiskManager(settings)
    result = manager.check(make_signal(), db_session, {"fake": BrokenBroker()})
    assert result.allowed is True
