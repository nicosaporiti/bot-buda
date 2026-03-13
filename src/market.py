"""Dynamic market configuration from Buda.com API."""

from dataclasses import dataclass
from decimal import Decimal


# Known decimal precision for currencies
KNOWN_DECIMALS = {
    "btc": 8,
    "eth": 8,
    "ltc": 8,
    "bch": 8,
    "usdc": 6,
    "usdt": 6,
    "clp": 0,
    "cop": 0,
    "pen": 2,
}

# Authoritative price tick per market.  The Buda API does not expose the
# allowed price increment, so we maintain a lookup keyed by market-id.
# For any market not listed here the fallback is 0.01 (the finest
# granularity observed on the exchange).
KNOWN_PRICE_TICKS: dict[str, Decimal] = {
    # CLP markets — whole peso for crypto, centavo for stablecoins
    "btc-clp": Decimal("1"),
    "eth-clp": Decimal("1"),
    "ltc-clp": Decimal("1"),
    "bch-clp": Decimal("1"),
    "usdc-clp": Decimal("0.01"),
    "usdt-clp": Decimal("0.01"),
    # COP markets — same pattern as CLP (integer fiat)
    "btc-cop": Decimal("1"),
    "eth-cop": Decimal("1"),
    "bch-cop": Decimal("1"),
    "ltc-cop": Decimal("1"),
    "usdc-cop": Decimal("0.01"),
    "usdt-cop": Decimal("0.01"),
    # PEN markets — PEN has 2 decimals; stablecoins use finer tick
    "btc-pen": Decimal("0.01"),
    "eth-pen": Decimal("0.01"),
    "bch-pen": Decimal("0.01"),
    "ltc-pen": Decimal("0.01"),
    "usdc-pen": Decimal("0.0001"),
    "usdt-pen": Decimal("0.0001"),
    # Crypto-crypto markets
    "eth-btc": Decimal("0.00000001"),
    "bch-btc": Decimal("0.00000001"),
    "ltc-btc": Decimal("0.00000001"),
    "btc-usdc": Decimal("0.01"),
    "usdt-usdc": Decimal("0.0001"),
}

_DEFAULT_PRICE_TICK = Decimal("0.01")


@dataclass(frozen=True)
class MarketConfig:
    """Configuration for a single market."""

    market_id: str
    base_currency: str
    quote_currency: str
    min_order_amount: Decimal
    base_decimals: int
    quote_decimals: int
    price_tick: Decimal


def _infer_decimals(currency: str, minimum_order_amount: str | None = None) -> int:
    """Infer decimal precision for a currency.

    Uses known lookup first, then falls back to parsing the minimum_order_amount
    string from the API (e.g. "0.00002" → 5 decimals).
    """
    currency = currency.lower()
    if currency in KNOWN_DECIMALS:
        return KNOWN_DECIMALS[currency]

    if minimum_order_amount:
        s = str(minimum_order_amount)
        if "." in s:
            return len(s.rstrip("0").split(".")[1])

    return 8  # safe default


class MarketRegistry:
    """Registry of available markets fetched from the API."""

    def __init__(self, client, quote_currency: str = "clp"):
        self._configs: dict[str, MarketConfig] = {}
        self._quote_currency = quote_currency.lower()
        self._load(client)

    def _load(self, client) -> None:
        raw_markets = client.get_markets()
        for m in raw_markets:
            mid = m.get("id", "").lower()
            parts = mid.split("-")
            if len(parts) != 2:
                continue
            base, quote = parts
            if quote != self._quote_currency:
                continue

            # Parse minimum_order_amount
            min_amount_raw = m.get("minimum_order_amount", ["0"])
            if isinstance(min_amount_raw, list):
                min_amount_str = str(min_amount_raw[0])
            else:
                min_amount_str = str(min_amount_raw)
            min_order_amount = Decimal(min_amount_str)

            base_decimals = _infer_decimals(base, min_amount_str)
            quote_decimals = _infer_decimals(quote)
            price_tick = KNOWN_PRICE_TICKS.get(mid, _DEFAULT_PRICE_TICK)

            self._configs[mid] = MarketConfig(
                market_id=mid,
                base_currency=base,
                quote_currency=quote,
                min_order_amount=min_order_amount,
                base_decimals=base_decimals,
                quote_decimals=quote_decimals,
                price_tick=price_tick,
            )

    def get(self, market_id: str) -> MarketConfig:
        """Get config for a market_id (e.g. 'btc-clp')."""
        market_id = market_id.lower()
        if market_id not in self._configs:
            raise KeyError(
                f"Market '{market_id}' not found. "
                f"Available: {', '.join(sorted(self._configs))}"
            )
        return self._configs[market_id]

    def get_by_currency(self, currency: str) -> MarketConfig:
        """Get config for a base currency (e.g. 'btc')."""
        market_id = f"{currency.lower()}-{self._quote_currency}"
        return self.get(market_id)

    def currencies(self) -> list[str]:
        """Return sorted list of available base currencies."""
        return sorted(cfg.base_currency for cfg in self._configs.values())

    def market_ids(self) -> list[str]:
        """Return sorted list of available market IDs."""
        return sorted(self._configs)

    def has_market(self, market_id: str) -> bool:
        """Check if a market exists in the registry."""
        return market_id.lower() in self._configs

    @property
    def quote_currency(self) -> str:
        return self._quote_currency
