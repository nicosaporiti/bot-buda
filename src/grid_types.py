"""Data types for the grid trading strategy."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .market import MarketConfig


class GridConfigError(Exception):
    """Invalid grid configuration."""


@dataclass(frozen=True)
class GridConfig:
    market_config: MarketConfig
    lower_price: Optional[Decimal]
    upper_price: Optional[Decimal]
    range_pct: Optional[Decimal]
    levels: int
    quote_budget: Decimal
    base_budget: Decimal = Decimal("0")
    max_open_orders: int = 0
    interval: int = 10
    dry_run: bool = False


@dataclass(frozen=True)
class GridLevel:
    index: int
    price: Decimal


@dataclass
class GridOrder:
    order_id: str
    side: str  # "buy" | "sell"
    level_index: int
    amount: Decimal
    price: Decimal
    traded_amount: Decimal = Decimal("0")
    traded_quote: Decimal = Decimal("0")
    accounted_amount: Decimal = Decimal("0")
    accounted_quote: Decimal = Decimal("0")
    state: str = "pending"
    mirrored: bool = False
