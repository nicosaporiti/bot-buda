"""Grid trading strategy engine for Buda.com."""

from __future__ import annotations

import os
import signal
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import List, Optional, Tuple

from .api import BudaAPIError, BudaClient, InsufficientBalanceError
from .grid_types import GridConfig, GridConfigError, GridLevel, GridOrder
from .market import MarketConfig
from .utils import format_clp, format_crypto, parse_order_book_entry, print_status
from .ws import RealtimeClient


def compute_auto_range(price: Decimal, range_pct: Decimal) -> Tuple[Decimal, Decimal]:
    """Center a `range_pct` window on `price` and return (lower, upper)."""
    if price <= 0:
        raise GridConfigError("Precio actual invalido para rango automatico")
    if range_pct <= 0:
        raise GridConfigError("range_pct debe ser positivo")

    half = price * range_pct / Decimal("100")
    return price - half, price + half


def generate_levels(
    lower: Decimal,
    upper: Decimal,
    n_levels: int,
    tick: Decimal,
) -> List[GridLevel]:
    """Generate `n_levels` evenly-spaced grid levels in [lower, upper] rounded to `tick`."""
    if n_levels < 2:
        raise GridConfigError("levels debe ser >= 2")
    if lower >= upper:
        raise GridConfigError("lower debe ser menor que upper")
    if tick <= 0:
        raise GridConfigError("tick debe ser positivo")

    span = upper - lower
    step = span / Decimal(n_levels - 1)

    rounded: List[Decimal] = []
    for i in range(n_levels):
        raw = lower + step * Decimal(i)
        rounded.append(raw.quantize(tick, rounding=ROUND_HALF_UP))

    if len(set(rounded)) != len(rounded):
        raise GridConfigError(
            "Niveles duplicados despues de redondear al tick. "
            "Reduce levels o amplia el rango."
        )

    return [GridLevel(index=i, price=p) for i, p in enumerate(rounded)]


def split_levels(
    levels: List[GridLevel],
    current_price: Decimal,
) -> Tuple[List[GridLevel], List[GridLevel]]:
    """Return (buy_levels, sell_levels) split strictly around `current_price`."""
    buys = [lvl for lvl in levels if lvl.price < current_price]
    sells = [lvl for lvl in levels if lvl.price > current_price]
    return buys, sells


def quantize_base(amount: Decimal, base_decimals: int) -> Decimal:
    precision = Decimal(10) ** -base_decimals
    return amount.quantize(precision, rounding=ROUND_DOWN)


def quantize_quote(amount: Decimal, quote_decimals: int) -> Decimal:
    precision = Decimal(10) ** -quote_decimals
    return amount.quantize(precision, rounding=ROUND_DOWN)


def plan_initial_buys(
    buy_levels: List[GridLevel],
    quote_budget: Decimal,
    buy_slots: int,
    market_config: MarketConfig,
) -> List[Tuple[GridLevel, Decimal]]:
    """Plan initial buy orders. Returns list of (level, base_amount).

    Buys go from closest-to-price (highest) down to lowest, until slots fill.
    """
    if buy_slots <= 0 or not buy_levels:
        return []

    selected = sorted(buy_levels, key=lambda lvl: lvl.price, reverse=True)[:buy_slots]
    if not selected:
        return []

    quote_per_order = quantize_quote(
        quote_budget / Decimal(len(selected)), market_config.quote_decimals
    )

    plan: List[Tuple[GridLevel, Decimal]] = []
    for lvl in selected:
        amount = quantize_base(quote_per_order / lvl.price, market_config.base_decimals)
        if amount < market_config.min_order_amount:
            raise GridConfigError(
                f"Monto por nivel ({format_clp(quote_per_order)} -> "
                f"{amount} {market_config.base_currency.upper()}) bajo minimo "
                f"{market_config.min_order_amount} {market_config.base_currency.upper()}"
            )
        plan.append((lvl, amount))

    return plan


def plan_initial_sells(
    sell_levels: List[GridLevel],
    base_budget: Decimal,
    sell_slots: int,
    market_config: MarketConfig,
) -> List[Tuple[GridLevel, Decimal]]:
    """Plan initial sell orders. Returns list of (level, base_amount)."""
    if sell_slots <= 0 or base_budget <= 0 or not sell_levels:
        return []

    selected = sorted(sell_levels, key=lambda lvl: lvl.price)[:sell_slots]
    if not selected:
        return []

    base_per_order = quantize_base(
        base_budget / Decimal(len(selected)), market_config.base_decimals
    )

    if base_per_order < market_config.min_order_amount:
        raise GridConfigError(
            f"Cantidad por venta ({base_per_order}) bajo minimo "
            f"{market_config.min_order_amount} {market_config.base_currency.upper()}"
        )

    return [(lvl, base_per_order) for lvl in selected]


def allocate_slots(max_open_orders: int, has_base_budget: bool) -> Tuple[int, int]:
    """Split `max_open_orders` between buys and sells."""
    if not has_base_budget:
        return max_open_orders, 0
    sell_slots = max_open_orders // 2
    buy_slots = max_open_orders - sell_slots
    return buy_slots, sell_slots


class GridTradingBot:
    """Spot grid bot that maintains many limit orders inside a price range."""

    def __init__(
        self,
        client: BudaClient,
        config: GridConfig,
        register_signals: bool = True,
    ):
        self.client = client
        self.config = config
        self.market_config = config.market_config
        self.market_id = self.market_config.market_id
        self.dry_run = config.dry_run
        self.interval = config.interval

        self._levels: List[GridLevel] = []
        self._orders: dict[str, GridOrder] = {}
        self._deferred: List[Tuple[str, GridLevel, Decimal]] = []
        self._running = False
        self._realtime: Optional[RealtimeClient] = None
        self._last_sanity_ts = 0.0
        self._sanity_interval = 120.0
        self._last_snapshot_attempt_ts = 0.0
        self._snapshot_retry_interval = max(min(float(self.interval), 5.0), 1.0)
        self._last_action_ts = 0.0
        self._min_action_interval = 0.5

        self._fills_buy_quote = Decimal("0")
        self._fills_buy_base = Decimal("0")
        self._fills_sell_base = Decimal("0")
        self._fills_sell_quote = Decimal("0")

        if register_signals:
            signal.signal(signal.SIGINT, self._handle_interrupt)
            signal.signal(signal.SIGTERM, self._handle_interrupt)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        cfg = self.config
        if cfg.levels < 2:
            raise GridConfigError("levels debe ser >= 2")
        if cfg.max_open_orders <= 0:
            raise GridConfigError("max_open_orders debe ser > 0")
        if cfg.quote_budget <= 0:
            raise GridConfigError("quote_budget debe ser > 0")
        if cfg.base_budget < 0:
            raise GridConfigError("base_budget no puede ser negativo")
        if cfg.range_pct is None:
            if cfg.lower_price is None or cfg.upper_price is None:
                raise GridConfigError(
                    "Debes indicar (lower y upper) o range_pct"
                )
            if cfg.lower_price >= cfg.upper_price:
                raise GridConfigError("lower debe ser menor que upper")
        else:
            if cfg.range_pct <= 0:
                raise GridConfigError("range_pct debe ser > 0")

    # ------------------------------------------------------------------
    # Realtime + price helpers
    # ------------------------------------------------------------------

    def _start_realtime(self) -> None:
        pubsub_key = None
        if not self.dry_run:
            try:
                user = self.client.get_me()
                pubsub_key = user.get("pubsub_key")
            except BudaAPIError as e:
                print_status(f"Realtime auth no disponible: {e}", "WARN")

        debug = os.getenv("BUDA_WS_DEBUG") == "1"
        debug_limit = int(os.getenv("BUDA_WS_DEBUG_LIMIT", "5"))
        self._realtime = RealtimeClient(
            self.market_id, pubsub_key, debug=debug, debug_limit=debug_limit
        )
        self._realtime.start()

    def _stop_realtime(self) -> None:
        if self._realtime:
            self._realtime.stop()
            self._realtime = None

    def _realtime_book_levels(
        self,
    ) -> Optional[Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]]:
        if not self._realtime:
            return None
        bids, asks = self._realtime.book_state.get_snapshot()
        if not bids or not asks:
            return None
        return (
            sorted(bids.items(), key=lambda item: item[0], reverse=True),
            sorted(asks.items(), key=lambda item: item[0]),
        )

    def _get_book_levels(
        self,
    ) -> Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]:
        if self._realtime:
            max_age = max(self.interval * 3, 1.0)
            if not self._realtime.book_state.is_stale(max_age):
                live_levels = self._realtime_book_levels()
                if live_levels:
                    return live_levels

        if (
            self._realtime
            and self._snapshot_refresh_pending()
            and self._snapshot_retry_delay() > 0
        ):
            raise BudaAPIError("Reintento de snapshot del book en espera")

        snapshot_version = (
            self._realtime.book_state.snapshot_version()
            if self._realtime
            else 0
        )
        if self._realtime:
            self._last_snapshot_attempt_ts = time.monotonic()
        order_book = self.client.get_order_book(self.market_id)
        bids_raw = order_book.get("bids", [])
        asks_raw = order_book.get("asks", [])
        if self._realtime:
            applied = self._realtime.book_state.apply_snapshot_if_current(
                snapshot_version, bids_raw, asks_raw
            )
            if applied:
                self._last_sanity_ts = time.time()
            if not applied:
                live_levels = self._realtime_book_levels()
                if live_levels:
                    return live_levels
                self._realtime.book_state.seed_snapshot_if_unready(
                    bids_raw, asks_raw
                )
                live_levels = self._realtime_book_levels()
                if live_levels:
                    return live_levels
        bids = [parse_order_book_entry(e) for e in bids_raw]
        asks = [parse_order_book_entry(e) for e in asks_raw]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return bids, asks

    def _refresh_realtime_book_if_needed(self) -> None:
        if not self._realtime:
            return
        now = time.monotonic()
        if not self._snapshot_refresh_pending():
            return
        if self._snapshot_retry_delay(now) > 0:
            return

        try:
            self._last_snapshot_attempt_ts = now
            snapshot_version = self._realtime.book_state.snapshot_version()
            order_book = self.client.get_order_book(self.market_id)
            applied = self._realtime.book_state.apply_snapshot_if_current(
                snapshot_version,
                order_book.get("bids", []), order_book.get("asks", [])
            )
            if not applied and self._realtime.book_state.needs_snapshot():
                self._realtime.book_state.seed_snapshot_if_unready(
                    order_book.get("bids", []), order_book.get("asks", [])
                )
            if applied:
                self._last_sanity_ts = time.time()
        except BudaAPIError as error:
            print_status(f"Sanity check fallo: {error}", "WARN")

    def _snapshot_retry_delay(self, now: Optional[float] = None) -> float:
        current = time.monotonic() if now is None else now
        elapsed = current - self._last_snapshot_attempt_ts
        return max(self._snapshot_retry_interval - elapsed, 0.0)

    def _snapshot_refresh_pending(self) -> bool:
        if not self._realtime:
            return False
        sanity_due = time.time() - self._last_sanity_ts >= self._sanity_interval
        return self._realtime.book_state.needs_snapshot() or sanity_due

    def _realtime_wait_timeout(self) -> float:
        if not self._snapshot_refresh_pending():
            return float(self.interval)
        return min(float(self.interval), self._snapshot_retry_delay())

    def _get_current_price(self) -> Decimal:
        """Mid-price if both sides available; fallback to ticker last_price."""
        try:
            bids, asks = self._get_book_levels()
            if bids and asks:
                mid = (bids[0][0] + asks[0][0]) / Decimal("2")
                return mid
        except BudaAPIError:
            pass

        ticker = self.client.get_ticker(self.market_id)
        last = ticker.get("last_price", ["0"])
        if isinstance(last, list):
            last = last[0]
        return Decimal(str(last))

    # ------------------------------------------------------------------
    # Balance verification
    # ------------------------------------------------------------------

    def _read_available(self, currency: str) -> Decimal:
        balance = self.client.get_balance(currency.lower())
        avail = balance.get("available_amount", [0])
        if isinstance(avail, list):
            avail = avail[0]
        return Decimal(str(avail))

    def _verify_balances(self) -> None:
        if self.dry_run:
            return
        quote = self.market_config.quote_currency
        avail_quote = self._read_available(quote)
        if self.config.quote_budget > avail_quote:
            raise InsufficientBalanceError(
                f"quote_budget {format_clp(self.config.quote_budget)} > "
                f"saldo disponible {format_clp(avail_quote)}"
            )

        if self.config.base_budget > 0:
            base = self.market_config.base_currency
            avail_base = self._read_available(base)
            if self.config.base_budget > avail_base:
                raise InsufficientBalanceError(
                    f"base_budget {self.config.base_budget} > "
                    f"saldo disponible {avail_base} {base.upper()}"
                )

    # ------------------------------------------------------------------
    # Price + amount formatting
    # ------------------------------------------------------------------

    def _format_limit_price(self, price: Decimal) -> str:
        tick = self.market_config.price_tick
        quantized = price.quantize(tick)
        if tick == Decimal("1"):
            return str(int(quantized))
        decimals = abs(tick.as_tuple().exponent)
        return f"{quantized:.{decimals}f}"

    def _fmt_base(self, amount: Decimal) -> str:
        return format_crypto(
            amount,
            self.market_config.base_currency,
            self.market_config.base_decimals,
        )

    # ------------------------------------------------------------------
    # Resolution: lower/upper from config (manual or auto)
    # ------------------------------------------------------------------

    def _resolve_range(self) -> Tuple[Decimal, Decimal]:
        cfg = self.config
        if cfg.range_pct is not None:
            current = self._get_current_price()
            lower, upper = compute_auto_range(current, cfg.range_pct)
            print_status(
                f"Precio referencia: {format_clp(current)} "
                f"(rango {cfg.range_pct}% -> "
                f"{format_clp(lower)} ... {format_clp(upper)})",
                "INFO",
            )
            return lower, upper

        return cfg.lower_price, cfg.upper_price  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def _is_safe_price(self, side: str, price: Decimal) -> bool:
        """Reject orders that would cross the live spread."""
        try:
            bids, asks = self._get_book_levels()
        except BudaAPIError:
            return False
        if not bids or not asks:
            return False
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        if side == "buy":
            return price < best_ask
        return price > best_bid

    def _place_order(
        self,
        side: str,
        level: GridLevel,
        amount: Decimal,
    ) -> Optional[GridOrder]:
        """Place a buy ('Bid') or sell ('Ask') limit order at `level`. Returns GridOrder."""
        order_type = "Bid" if side == "buy" else "Ask"
        limit_str = self._format_limit_price(level.price)

        if self.dry_run:
            print_status(
                f"[DRY RUN] {side.upper()} nivel {level.index} -> "
                f"{self._fmt_base(amount)} @ {format_clp(level.price)} "
                f"(total {format_clp(amount * level.price)})",
                "INFO",
            )
            order_id = f"dry-{side}-{level.index}-{int(time.time() * 1000)}"
            grid_order = GridOrder(
                order_id=order_id,
                side=side,
                level_index=level.index,
                amount=amount,
                price=level.price,
                state="pending",
            )
            self._orders[order_id] = grid_order
            return grid_order

        if not self._is_safe_price(side, level.price):
            print_status(
                f"{side.upper()} nivel {level.index} @ {format_clp(level.price)} "
                f"cruzaria el spread; omitida.",
                "WARN",
            )
            return None

        try:
            order = self.client.create_limit_order(
                market_id=self.market_id,
                order_type=order_type,
                amount=str(amount),
                limit_price=limit_str,
            )
        except BudaAPIError as e:
            print_status(
                f"Falla al crear {side} nivel {level.index} @ {format_clp(level.price)}: {e}",
                "ERROR",
            )
            return None

        order_id = str(order.get("id"))
        grid_order = GridOrder(
            order_id=order_id,
            side=side,
            level_index=level.index,
            amount=amount,
            price=level.price,
            state=str(order.get("state", "pending")),
        )
        self._orders[order_id] = grid_order
        self._last_action_ts = time.time()
        print_status(
            f"{side.upper()} nivel {level.index} colocado: "
            f"{self._fmt_base(amount)} @ {format_clp(level.price)} (id {order_id})",
            "OK",
        )
        return grid_order

    def _cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order:
            return False
        if self.dry_run:
            order.state = "canceled"
            return True
        try:
            self.client.cancel_order(order_id)
            order.state = "canceling"
            return True
        except BudaAPIError as e:
            print_status(f"Falla al cancelar {order_id}: {e}", "ERROR")
            return False

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_remote(order: dict) -> Tuple[str, Decimal, Decimal, Decimal]:
        state = str(order.get("state", "unknown"))

        traded = order.get("traded_amount", ["0"])
        if isinstance(traded, list):
            traded = traded[0]
        traded_base = Decimal(str(traded))

        limit = order.get("limit", ["0"])
        if isinstance(limit, list):
            limit = limit[0]
        order_price = Decimal(str(limit))

        total = order.get("total_exchanged", ["0"])
        if isinstance(total, list):
            total = total[0]
        traded_quote = Decimal(str(total))

        return state, traded_base, order_price, traded_quote

    def _refresh_order(self, order: GridOrder) -> None:
        if self.dry_run:
            return

        remote = None
        if self._realtime:
            remote = self._realtime.order_state.get_order(order.order_id)
        if remote is None:
            try:
                remote = self.client.get_order(order.order_id)
            except BudaAPIError as e:
                print_status(f"No se pudo refrescar orden {order.order_id}: {e}", "WARN")
                return

        state, traded_base, _, traded_quote = self._parse_remote(remote)
        order.state = state
        order.traded_amount = traded_base
        order.traded_quote = traded_quote

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _handle_interrupt(self, _signum, _frame):
        self.cleanup()
        sys.exit(0)

    _TERMINAL_STATES = ("traded", "canceled", "canceled_and_traded")

    def cleanup(self) -> None:
        if not self._running and not self._orders:
            return
        print()
        print_status("Deteniendo grilla. Cancelando ordenes activas...", "WARN")
        self._running = False

        if not self.dry_run:
            # Phase 1: send cancel for every still-open order.
            pending: list[GridOrder] = []
            for oid, order in list(self._orders.items()):
                if order.state in self._TERMINAL_STATES:
                    continue
                if self._cancel_order(oid):
                    pending.append(order)

            # Phase 2: poll until each cancel is confirmed (or timeout).
            self._await_cancellations(pending)

            # Account every order: _account_fill is delta-based and idempotent,
            # so partial fills on still-canceling orders are not lost from the
            # summary if the cancel never confirmed within the timeout.
            for order in self._orders.values():
                self._account_fill(order)

        self._stop_realtime()
        self._print_summary()

    def _retry_deferred(self) -> None:
        """Reattempt placements that were rejected (e.g. spread crossed)."""
        if not self._deferred:
            return
        still_pending: List[Tuple[str, GridLevel, Decimal]] = []
        for side, lvl, amount in self._deferred:
            if self._open_orders_count() >= self.config.max_open_orders:
                still_pending.append((side, lvl, amount))
                continue
            if self._place_order(side, lvl, amount) is None:
                still_pending.append((side, lvl, amount))
        self._deferred = still_pending

    def _await_cancellations(
        self,
        orders: list[GridOrder],
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> None:
        """Block until every order reaches a terminal state or `timeout` elapses."""
        deadline = time.time() + timeout
        remaining = list(orders)
        while remaining and time.time() < deadline:
            still_pending: list[GridOrder] = []
            for order in remaining:
                self._refresh_order(order)
                if order.state not in self._TERMINAL_STATES:
                    still_pending.append(order)
            remaining = still_pending
            if remaining:
                time.sleep(poll_interval)

        if remaining:
            print_status(
                f"{len(remaining)} orden(es) sin confirmacion de cancelacion. "
                "Verifica manualmente en Buda.",
                "ERROR",
            )
            for order in remaining:
                print_status(
                    f"  - {order.side} nivel {order.level_index} "
                    f"id={order.order_id} state={order.state}",
                    "ERROR",
                )

    # ------------------------------------------------------------------
    # Mirror logic
    # ------------------------------------------------------------------

    def _level_at(self, index: int) -> Optional[GridLevel]:
        if 0 <= index < len(self._levels):
            return self._levels[index]
        return None

    def _open_orders_count(self) -> int:
        return sum(
            1
            for o in self._orders.values()
            if o.state in ("pending", "received")
        )

    def _account_fill(self, order: GridOrder) -> None:
        """Record only the fill delta since the last accounting. Idempotent."""
        delta_base = order.traded_amount - order.accounted_amount
        delta_quote = order.traded_quote - order.accounted_quote
        if delta_base <= 0:
            return
        if order.side == "buy":
            self._fills_buy_base += delta_base
            self._fills_buy_quote += delta_quote
        else:
            self._fills_sell_base += delta_base
            self._fills_sell_quote += delta_quote
        order.accounted_amount = order.traded_amount
        order.accounted_quote = order.traded_quote

    def _maybe_mirror(self, order: GridOrder) -> None:
        """If `order` reached terminal with fills, place its mirror once."""
        if order.mirrored:
            return
        if order.traded_amount <= 0:
            order.mirrored = True
            return
        if order.traded_amount < self.market_config.min_order_amount:
            order.mirrored = True
            return

        if order.side == "buy":
            target = self._level_at(order.level_index + 1)
            mirror_side = "sell"
        else:
            target = self._level_at(order.level_index - 1)
            mirror_side = "buy"

        if target is None:
            print_status(
                f"Sin nivel espejo para {order.side} nivel {order.level_index}",
                "WARN",
            )
            order.mirrored = True
            return

        amount = quantize_base(order.traded_amount, self.market_config.base_decimals)
        if amount < self.market_config.min_order_amount:
            order.mirrored = True
            return

        if self._open_orders_count() >= self.config.max_open_orders:
            # No slots free yet; defer until next iteration.
            return

        placed = self._place_order(mirror_side, target, amount)
        if placed is not None:
            order.mirrored = True

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def execute(self) -> None:
        self.validate()
        self._running = True

        cfg = self.config
        print_status(f"Mercado: {self.market_id.upper()}", "INFO")
        print_status(f"Quote budget: {format_clp(cfg.quote_budget)}", "INFO")
        if cfg.base_budget > 0:
            print_status(f"Base budget: {cfg.base_budget} {self.market_config.base_currency.upper()}", "INFO")
        print_status(f"Levels: {cfg.levels}", "INFO")
        print_status(f"max_open_orders: {cfg.max_open_orders}", "INFO")
        print_status(f"interval: {cfg.interval}s", "INFO")
        if self.dry_run:
            print_status("DRY RUN - no se publicaran ordenes", "WARN")
        print()

        self._verify_balances()

        self._start_realtime()
        try:
            if self._realtime and not self._realtime.book_state.wait_ready(5):
                print_status("Realtime book aun no listo; usando REST.", "WARN")

            lower, upper = self._resolve_range()
            self._levels = generate_levels(
                lower, upper, cfg.levels, self.market_config.price_tick
            )
            print()
            print_status(f"Niveles generados: {len(self._levels)}", "INFO")
            for lvl in self._levels:
                print(f"    [{lvl.index:>2}] {format_clp(lvl.price)}")
            print()

            current = self._get_current_price()
            print_status(f"Precio actual: {format_clp(current)}", "INFO")

            buy_levels, sell_levels = split_levels(self._levels, current)

            buy_slots, sell_slots = allocate_slots(
                cfg.max_open_orders, cfg.base_budget > 0
            )
            buy_slots = min(buy_slots, len(buy_levels))
            sell_slots = min(sell_slots, len(sell_levels))

            initial_buys = plan_initial_buys(
                buy_levels, cfg.quote_budget, buy_slots, self.market_config
            )
            initial_sells = plan_initial_sells(
                sell_levels, cfg.base_budget, sell_slots, self.market_config
            )

            print()
            print_status(
                f"Ordenes iniciales: {len(initial_buys)} compras, {len(initial_sells)} ventas",
                "INFO",
            )

            attempted = 0
            placed = 0
            for side, plan in (("buy", initial_buys), ("sell", initial_sells)):
                for lvl, amount in plan:
                    attempted += 1
                    if self._place_order(side, lvl, amount) is not None:
                        placed += 1
                    elif not self.dry_run:
                        self._deferred.append((side, lvl, amount))

            if attempted == 0:
                raise GridConfigError(
                    "Sin niveles utilizables para ordenes iniciales. "
                    "Revisa rango, precio actual y max_open_orders."
                )

            if not self.dry_run and placed == 0:
                raise GridConfigError(
                    f"Ninguna de las {attempted} ordenes iniciales pudo colocarse "
                    "(rechazadas por spread o error de API). La grilla NO se inicio."
                )

            if self._deferred:
                print_status(
                    f"{len(self._deferred)} orden(es) inicial(es) diferidas, "
                    "se reintentaran en el loop.",
                    "WARN",
                )

            if self.dry_run:
                print()
                print_status("Dry run completado.", "OK")
                self._running = False
                self._stop_realtime()
                return

            print()
            print_status(
                "Iniciando loop de monitoreo. Presiona Ctrl+C para detener.",
                "INFO",
            )
            print()

            self._monitor_loop()

        finally:
            if self._running:
                self.cleanup()
            else:
                self._stop_realtime()

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                if self._realtime:
                    self._realtime.book_state.wait_for_top_change(
                        self._realtime_wait_timeout()
                    )
                else:
                    time.sleep(self.interval)

                if not self._running:
                    break

                self._refresh_realtime_book_if_needed()

                # Refresh + react.
                for order in list(self._orders.values()):
                    if order.state in ("traded", "canceled", "canceled_and_traded"):
                        continue
                    self._refresh_order(order)
                    if order.state == "traded":
                        print_status(
                            f"{order.side.upper()} nivel {order.level_index} ejecutada "
                            f"({self._fmt_base(order.traded_amount)} @ {format_clp(order.price)})",
                            "OK",
                        )
                        self._account_fill(order)
                        self._maybe_mirror(order)
                    elif order.state == "canceled_and_traded":
                        print_status(
                            f"{order.side.upper()} nivel {order.level_index} parcial "
                            f"+ cancelada ({self._fmt_base(order.traded_amount)})",
                            "WARN",
                        )
                        self._account_fill(order)
                        self._maybe_mirror(order)
                    elif order.state == "canceled":
                        # Nothing filled.
                        order.mirrored = True

                # Retry deferred mirrors when slots free up.
                for order in list(self._orders.values()):
                    if (
                        order.state in ("traded", "canceled_and_traded")
                        and not order.mirrored
                    ):
                        self._maybe_mirror(order)

                # Retry deferred initial orders.
                self._retry_deferred()

            except BudaAPIError as e:
                print_status(f"API error: {e}", "ERROR")
            except Exception as e:  # noqa: BLE001
                print_status(f"Error inesperado: {e}", "ERROR")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self) -> None:
        print()
        print_status("=" * 50, "INFO")
        print_status("RESUMEN GRILLA", "INFO")
        print_status("=" * 50, "INFO")
        print_status(
            f"Compras ejecutadas: {self._fmt_base(self._fills_buy_base)} "
            f"por {format_clp(self._fills_buy_quote)}",
            "INFO",
        )
        print_status(
            f"Ventas ejecutadas: {self._fmt_base(self._fills_sell_base)} "
            f"por {format_clp(self._fills_sell_quote)}",
            "INFO",
        )

        net_base = self._fills_buy_base - self._fills_sell_base
        net_quote = self._fills_sell_quote - self._fills_buy_quote
        print_status(
            f"Inventario base neto: {self._fmt_base(net_base)}", "INFO"
        )
        print_status(f"PnL bruto (quote): {format_clp(net_quote)}", "INFO")
        print_status("=" * 50, "INFO")
