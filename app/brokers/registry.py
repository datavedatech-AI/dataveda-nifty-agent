import logging

from app.brokers.base import BrokerAdapter
from app.brokers.dhan import DhanAdapter
from app.brokers.mt5 import MT5Adapter
from app.config import Settings

logger = logging.getLogger(__name__)


def build_brokers(settings: Settings) -> dict[str, BrokerAdapter]:
    """Instantiate every broker listed in ENABLED_BROKERS.

    Adding a new broker later: implement BrokerAdapter in app/brokers/<x>.py
    and add one `elif name == "<x>":` branch here.
    """
    brokers: dict[str, BrokerAdapter] = {}

    for name in settings.enabled_brokers_list:
        if name == "dhan":
            brokers["dhan"] = DhanAdapter(
                client_id=settings.dhan_client_id,
                access_token=settings.dhan_access_token,
                base_url=settings.dhan_base_url,
            )
        elif name == "mt5":
            brokers["mt5"] = MT5Adapter(
                mode=settings.mt5_mode,
                login=settings.mt5_login,
                password=settings.mt5_password,
                server=settings.mt5_server,
                terminal_path=settings.mt5_terminal_path,
                bridge_url=settings.mt5_bridge_url,
            )
        else:
            logger.warning("Unknown broker %r in ENABLED_BROKERS, skipping", name)

    return brokers
