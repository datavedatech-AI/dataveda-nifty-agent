from app.config import Settings
from app.models.order import OrderStatus
from app.models.signal import TradingViewSignal
from app.risk.manager import RiskManager
from app.services.order_router import process_signal
from tests.fakes import FakeBroker


def make_settings(**overrides) -> Settings:
    base = dict(
        WEBHOOK_PASSPHRASE="secret",
        ENABLED_BROKERS="fake",
        TRADING_MODE="paper",
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


SYMBOL_MAP = {"NIFTY": {"fake": {"id": "13"}}}


def test_paper_mode_does_not_call_broker(db_session):
    settings = make_settings(TRADING_MODE="paper")
    broker = FakeBroker()
    results = process_signal(make_signal(), settings, {"fake": broker}, SYMBOL_MAP, db_session, RiskManager(settings))

    assert len(results) == 1
    assert results[0].status == OrderStatus.SIMULATED
    assert broker.placed_orders == []


def test_live_mode_calls_broker(db_session):
    settings = make_settings(TRADING_MODE="live")
    broker = FakeBroker()
    results = process_signal(make_signal(), settings, {"fake": broker}, SYMBOL_MAP, db_session, RiskManager(settings))

    assert len(results) == 1
    assert results[0].status == OrderStatus.ACCEPTED
    assert len(broker.placed_orders) == 1
    assert broker.placed_orders[0].quantity == 50


def test_duplicate_signal_id_is_rejected(db_session):
    settings = make_settings(TRADING_MODE="live")
    broker = FakeBroker()
    risk_manager = RiskManager(settings)

    process_signal(make_signal(signal_id="dup-1"), settings, {"fake": broker}, SYMBOL_MAP, db_session, risk_manager)
    results = process_signal(make_signal(signal_id="dup-1"), settings, {"fake": broker}, SYMBOL_MAP, db_session, risk_manager)

    assert len(broker.placed_orders) == 1  # second call never reached the broker
    assert results[0].status == OrderStatus.REJECTED
    assert "Duplicate" in results[0].message


def test_unmapped_symbol_is_rejected(db_session):
    settings = make_settings(TRADING_MODE="live")
    broker = FakeBroker()
    results = process_signal(
        make_signal(symbol="DOGEUSD"), settings, {"fake": broker}, SYMBOL_MAP, db_session, RiskManager(settings)
    )

    assert results[0].status == OrderStatus.REJECTED
    assert "No enabled broker" in results[0].message
    assert broker.placed_orders == []


def test_close_all_resolves_from_open_position(db_session):
    settings = make_settings(TRADING_MODE="live")
    broker = FakeBroker(positions=[{"id": "13", "qty": 75}])
    results = process_signal(
        make_signal(action="close_all", signal_id="close-1"),
        settings,
        {"fake": broker},
        SYMBOL_MAP,
        db_session,
        RiskManager(settings),
    )

    assert results[0].status == OrderStatus.ACCEPTED
    assert broker.placed_orders[0].side.value == "sell"
    assert broker.placed_orders[0].quantity == 75


def test_close_all_with_no_position_is_a_noop(db_session):
    settings = make_settings(TRADING_MODE="live")
    broker = FakeBroker(positions=[])
    results = process_signal(
        make_signal(action="close_all", signal_id="close-2"),
        settings,
        {"fake": broker},
        SYMBOL_MAP,
        db_session,
        RiskManager(settings),
    )

    assert results[0].status == OrderStatus.REJECTED
    assert "no position" in results[0].message.lower()
    assert broker.placed_orders == []


def test_broker_exception_is_captured_not_raised(db_session):
    class ExplodingBroker(FakeBroker):
        def place_order(self, order):
            raise RuntimeError("upstream 500")

    settings = make_settings(TRADING_MODE="live")
    broker = ExplodingBroker()
    results = process_signal(make_signal(), settings, {"fake": broker}, SYMBOL_MAP, db_session, RiskManager(settings))

    assert results[0].status == OrderStatus.ERROR
    assert "upstream 500" in results[0].message
