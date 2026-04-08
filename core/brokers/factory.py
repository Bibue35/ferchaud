"""BrokerFactory — registry and instantiation for all broker adapters."""
from __future__ import annotations
from typing import Type
from core.brokers.base import BrokerBase

__all__ = ["BrokerFactory"]


def _lazy_import(module_path: str, class_name: str) -> Type[BrokerBase]:
    """Import broker class on demand to avoid hard dependencies at import time."""
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


class BrokerFactory:
    """Registry of all supported broker adapters.

    Usage:
        broker = BrokerFactory.create("alpaca", {"api_key": "...", "secret_key": "..."})
    """

    # name -> (module_path, class_name, status)
    _REGISTRY: dict[str, tuple[str, str, str]] = {
        "alpaca":       ("core.brokers.alpaca",       "AlpacaBroker",       "live"),
        "tradier":      ("core.brokers.tradier",       "TradierBroker",      "live"),
        "tastytrade":   ("core.brokers.tastytrade",    "TastytradeBroker",   "live"),
        "coinbase":     ("core.brokers.coinbase",      "CoinbaseBroker",     "live"),
        "binance":      ("core.brokers.binance",       "BinanceBroker",      "live"),
        "bybit":        ("core.brokers.bybit",         "BybitBroker",        "live"),
        "kraken":       ("core.brokers.kraken",        "KrakenBroker",       "live"),
        "ibkr":         ("core.brokers.ibkr",          "IBKRBroker",         "live"),
        "tradestation": ("core.brokers.tradestation",  "TradeStationBroker", "live"),
        "robinhood":    ("core.brokers.robinhood",     "RobinhoodBroker",    "beta"),
        "webull":       ("core.brokers.webull",        "WebullBroker",       "beta"),
        "tradovate":    ("core.brokers.tradovate",     "TradovateBroker",    "live"),
        "paper":        ("core.brokers.paper",         "PaperBroker",        "live"),
        # Coming soon — not yet implemented
        "etrade":       ("core.brokers.etrade",        "ETradeBroker",       "coming_soon"),
        "ninjatrader":  ("core.brokers.ninjatrader",   "NinjaTraderBroker",  "coming_soon"),
        "projectx":     ("core.brokers.projectx",      "ProjectXBroker",     "coming_soon"),
        "cryptocom":    ("core.brokers.cryptocom",     "CryptoDotComBroker", "coming_soon"),
    }

    # Asset support map (mirrors each broker's SUPPORTED_ASSETS)
    _ASSETS: dict[str, list[str]] = {
        "alpaca":       ["stocks", "options", "etfs"],
        "tradier":      ["stocks", "options", "futures"],
        "tastytrade":   ["stocks", "options"],
        "coinbase":     ["crypto"],
        "binance":      ["crypto"],
        "bybit":        ["crypto"],
        "kraken":       ["crypto"],
        "ibkr":         ["stocks", "options", "futures", "forex"],
        "tradestation": ["stocks", "options", "futures"],
        "robinhood":    ["stocks", "options"],
        "webull":       ["stocks", "options"],
        "tradovate":    ["futures"],
        "paper":        ["stocks", "options", "futures", "crypto"],
        "etrade":       ["stocks", "options"],
        "ninjatrader":  ["futures"],
        "projectx":     ["futures"],
        "cryptocom":    ["crypto"],
    }

    @classmethod
    def create(cls, broker_name: str, credentials: dict) -> BrokerBase:
        """Instantiate a broker by name, passing credentials as kwargs.

        Args:
            broker_name: e.g. "alpaca", "binance", "paper"
            credentials: dict of constructor kwargs for the broker class

        Returns:
            Instantiated BrokerBase subclass

        Raises:
            ValueError: if broker_name is unknown or status is coming_soon
            ImportError: if optional library for the broker is not installed
        """
        entry = cls._REGISTRY.get(broker_name)
        if entry is None:
            raise ValueError(
                f"Unknown broker: {broker_name!r}. "
                f"Available: {list(cls._REGISTRY.keys())}"
            )
        module_path, class_name, status = entry
        if status == "coming_soon":
            raise ValueError(
                f"Broker {broker_name!r} is not yet implemented. Status: coming_soon"
            )
        broker_cls = _lazy_import(module_path, class_name)
        return broker_cls(**credentials)

    @classmethod
    def list_brokers(cls) -> list[dict]:
        """Return metadata for all registered brokers."""
        result = []
        for name, (_, _, status) in cls._REGISTRY.items():
            result.append({
                "name": name,
                "supported_assets": cls._ASSETS.get(name, []),
                "status": status,
            })
        return result

    @classmethod
    def get_broker_class(cls, broker_name: str) -> Type[BrokerBase]:
        """Return the class for a broker without instantiating."""
        entry = cls._REGISTRY.get(broker_name)
        if not entry:
            raise ValueError(f"Unknown broker: {broker_name!r}")
        module_path, class_name, _ = entry
        return _lazy_import(module_path, class_name)
