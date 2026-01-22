"""Trading bot logic for maintaining best bid/ask position."""

import os
import signal
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import List, Optional, Tuple

from .api import BudaClient, BudaAPIError, InsufficientBalanceError
from .utils import (
    format_clp,
    format_crypto,
    parse_order_book_entry,
    calculate_amount_for_clp,
    print_status,
    print_order_info,
)
from .ws import RealtimeClient


class TradingBot:
    """Bot for placing and maintaining best bid/ask orders on Buda.com."""

    # Minimum order amounts per market (in crypto)
    MIN_AMOUNTS = {
        "btc-clp": Decimal("0.00002"),
        "usdc-clp": Decimal("0.01"),
    }

    # Minimum order value in CLP per market
    MIN_CLP = {
        "btc-clp": Decimal("2000"),
        "usdc-clp": Decimal("10"),
    }

    # Price tick size per market (CLP)
    PRICE_TICKS = {
        "btc-clp": Decimal("1"),
        "usdc-clp": Decimal("0.01"),
    }

    def __init__(
        self,
        client: BudaClient,
        currency: str,
        interval: int = 30,
        dry_run: bool = False,
        strategy: str = "top",
        depth_ratio: Decimal = Decimal("0.9"),
    ):
        """
        Initialize the trading bot.

        Args:
            client: Buda API client instance.
            currency: Currency to trade (btc, usdc).
            interval: Monitoring interval in seconds.
            dry_run: If True, simulate without executing orders.
        """
        self.client = client
        self.currency = currency.lower()
        self.market_id = f"{self.currency}-clp"
        self.interval = interval
        self.dry_run = dry_run
        self.strategy = strategy
        self.depth_ratio = Decimal(str(depth_ratio))
        self.min_amount = self.MIN_AMOUNTS.get(
            self.market_id, Decimal("0.00001"))

        if self.strategy not in {"top", "depth"}:
            raise BudaAPIError(f"Unknown strategy: {self.strategy}")
        if not (Decimal("0") < self.depth_ratio <= Decimal("1")):
            raise BudaAPIError("Depth ratio must be between 0 and 1")

        self._current_order_id: Optional[str] = None
        self._running = False
        self._realtime: Optional[RealtimeClient] = None
        self._last_action_ts = 0.0
        self._min_action_interval = 0.5
        self._last_sanity_ts = 0.0
        self._sanity_interval = 120.0

        # Execution tracking for partial fills
        self._total_clp_target: Decimal = Decimal("0")
        self._total_clp_executed: Decimal = Decimal("0")
        self._total_crypto_received: Decimal = Decimal("0")

        # Execution tracking for sells
        self._total_crypto_target: Decimal = Decimal("0")
        self._total_crypto_executed: Decimal = Decimal("0")
        self._total_clp_received: Decimal = Decimal("0")

        self._active_side: Optional[str] = None

        # Setup signal handlers for clean exit
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        """Handle interrupt signals for clean shutdown."""
        print("\n")
        print_status("Interrupt received. Cleaning up...", "WARN")
        self._running = False
        self._stop_realtime()

        if self._current_order_id and not self.dry_run:
            try:
                # Check for partial execution before canceling
                state, traded_crypto, order_price, traded_clp = self.get_order_state(
                    self._current_order_id)

                print_status(
                    f"Canceling active order {self._current_order_id}...", "INFO")
                self.client.cancel_order(self._current_order_id)
                print_status("Order canceled successfully.", "OK")

                # Track any partial execution from the canceled order
                if traded_crypto > 0:
                    if self._active_side == "sell":
                        self.update_sell_execution_tracking(
                            traded_crypto, traded_clp)
                    else:
                        self.update_execution_tracking(traded_crypto, traded_clp)
                    print_status(
                        f"Partial execution captured: {format_crypto(traded_crypto, self.currency)}", "INFO")

            except BudaAPIError as e:
                print_status(f"Failed to cancel order: {e}", "ERROR")

        # Show final summary if we had any target set
        if self._active_side == "sell":
            if self._total_crypto_target > 0:
                self.print_sell_final_summary()
        elif self._total_clp_target > 0:
            self.print_final_summary()

        sys.exit(0)

    def _start_realtime(self) -> None:
        """Start realtime clients if possible."""
        pubsub_key = None
        if not self.dry_run:
            try:
                user = self.client.get_me()
                pubsub_key = user.get("pubsub_key")
            except BudaAPIError as e:
                print_status(f"Realtime auth unavailable: {e}", "WARN")

        debug = os.getenv("BUDA_WS_DEBUG") == "1"
        debug_limit = int(os.getenv("BUDA_WS_DEBUG_LIMIT", "5"))
        self._realtime = RealtimeClient(
            self.market_id,
            pubsub_key,
            debug=debug,
            debug_limit=debug_limit,
        )
        self._realtime.start()

    def _stop_realtime(self) -> None:
        if self._realtime:
            self._realtime.stop()
            self._realtime = None

    def verify_balance(self, clp_amount: Decimal) -> Decimal:
        """
        Verify CLP balance is sufficient for the order.

        Args:
            clp_amount: Amount of CLP needed.

        Returns:
            Available CLP balance.

        Raises:
            InsufficientBalanceError: If balance is insufficient.
        """
        print_status("Checking CLP balance...", "INFO")
        balance = self.client.get_balance("clp")

        # Handle both formats: [amount, currency] or just amount
        available = balance.get("available_amount", [0])[0]
        if isinstance(available, list):
            available = available[0]
        available = Decimal(str(available))

        print_status(f"Available: {format_clp(available)}", "OK")

        if available < clp_amount:
            raise InsufficientBalanceError(
                f"Insufficient balance. Have {format_clp(available)}, need {format_clp(clp_amount)}"
            )

        return available

    def verify_crypto_balance(self, crypto_amount: Decimal) -> Decimal:
        """
        Verify crypto balance is sufficient for the order.

        Args:
            crypto_amount: Amount of crypto needed.

        Returns:
            Available crypto balance.

        Raises:
            InsufficientBalanceError: If balance is insufficient.
        """
        currency = self.currency.lower()
        print_status(f"Checking {currency.upper()} balance...", "INFO")
        balance = self.client.get_balance(currency)

        available = balance.get("available_amount", [0])[0]
        if isinstance(available, list):
            available = available[0]
        available = Decimal(str(available))

        print_status(
            f"Available: {format_crypto(available, currency)}", "OK")

        if available < crypto_amount:
            raise InsufficientBalanceError(
                f"Insufficient balance. Have {format_crypto(available, currency)}, "
                f"need {format_crypto(crypto_amount, currency)}"
            )

        return available

    def get_best_prices(self) -> Tuple[Decimal, Decimal]:
        """
        Get the best bid and ask prices from the order book.

        Returns:
            Tuple of (best_bid, best_ask) prices.
        """
        if self._realtime:
            max_age = max(self.interval * 3, 1.0)
            if not self._realtime.book_state.is_stale(max_age):
                best = self._realtime.book_state.get_best()
                if best:
                    return best
            else:
                age = self._realtime.book_state.age_seconds()
                if age != float("inf"):
                    print_status(
                        f"Realtime book stale ({age:.1f}s). Falling back to REST.", "WARN"
                    )

        bids, asks = self.get_order_book_levels()
        if not bids:
            raise BudaAPIError("No bids in order book")
        if not asks:
            raise BudaAPIError("No asks in order book")

        best_bid = bids[0][0]
        best_ask = asks[0][0]

        return best_bid, best_ask

    def get_order_book_levels(self) -> Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]:
        """
        Return order book levels as lists of (price, amount) tuples.

        Bids are sorted descending (best first), asks ascending (best first).
        """
        if self._realtime:
            max_age = max(self.interval * 3, 1.0)
            if not self._realtime.book_state.is_stale(max_age):
                bids_dict, asks_dict = self._realtime.book_state.get_snapshot()
                if bids_dict and asks_dict:
                    bids = sorted(bids_dict.items(), key=lambda x: x[0], reverse=True)
                    asks = sorted(asks_dict.items(), key=lambda x: x[0])
                    return bids, asks
            age = self._realtime.book_state.age_seconds()
            if age != float("inf"):
                print_status(
                    f"Realtime book stale ({age:.1f}s). Falling back to REST.", "WARN"
                )

        order_book = self.client.get_order_book(self.market_id)
        bids_raw = order_book.get("bids", [])
        asks_raw = order_book.get("asks", [])
        if self._realtime:
            self._realtime.book_state.apply_snapshot(bids_raw, asks_raw)

        bids = [parse_order_book_entry(entry) for entry in bids_raw]
        asks = [parse_order_book_entry(entry) for entry in asks_raw]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return bids, asks

    def _strategy_label(self) -> str:
        if self.strategy == "depth":
            pct = (self.depth_ratio * Decimal("100")).quantize(Decimal("1"))
            return f"depth {pct}%"
        return "top"

    def calculate_depth_price(
        self,
        side: str,
        bids: List[Tuple[Decimal, Decimal]],
        asks: List[Tuple[Decimal, Decimal]],
    ) -> Decimal:
        """
        Calculate price based on cumulative depth ratio.

        For buys, accumulate bids from low to high.
        For sells, accumulate asks from high to low.
        """
        if side == "buy":
            levels = bids
            ordered = sorted(levels, key=lambda x: x[0])  # low -> high
        else:
            levels = asks
            ordered = sorted(levels, key=lambda x: x[0], reverse=True)  # high -> low

        if not ordered:
            raise BudaAPIError("Order book side is empty")

        total = sum((amount for _, amount in ordered), Decimal("0"))
        if total <= 0:
            raise BudaAPIError("Order book volume is zero")

        target = total * self.depth_ratio
        cumulative = Decimal("0")
        for price, amount in ordered:
            cumulative += amount
            if cumulative >= target:
                if side == "buy":
                    return self._round_price_down(price)
                return self._round_price_up(price)

        price = ordered[-1][0]
        if side == "buy":
            return self._round_price_down(price)
        return self._round_price_up(price)

    def calculate_strategy_price(
        self,
        side: str,
        bids: List[Tuple[Decimal, Decimal]],
        asks: List[Tuple[Decimal, Decimal]],
        best_bid: Decimal,
        best_ask: Decimal,
    ) -> Decimal:
        if self.strategy == "depth":
            return self.calculate_depth_price(side, bids, asks)
        if side == "buy":
            return self.calculate_optimal_price(best_bid, best_ask)
        return self.calculate_optimal_sell_price(best_bid, best_ask)

    @staticmethod
    def _parse_order_state(order: dict) -> Tuple[str, Decimal, Decimal, Decimal]:
        state = order.get("state", "unknown")

        traded = order.get("traded_amount", ["0"])
        if isinstance(traded, list):
            traded = traded[0]
        traded_crypto = Decimal(str(traded))

        limit = order.get("limit", ["0"])
        if isinstance(limit, list):
            limit = limit[0]
        order_price = Decimal(str(limit))

        total_exchanged = order.get("total_exchanged", ["0"])
        if isinstance(total_exchanged, list):
            total_exchanged = total_exchanged[0]
        traded_clp = Decimal(str(total_exchanged))

        return state, traded_crypto, order_price, traded_clp

    def _price_tick(self) -> Decimal:
        """Return the minimum price increment for the current market."""
        return self.PRICE_TICKS.get(self.market_id, Decimal("1"))

    def _round_price_up(self, price: Decimal) -> Decimal:
        """Round price up to the market tick size."""
        tick = self._price_tick()
        return price.quantize(tick, rounding=ROUND_UP)

    def _round_price_down(self, price: Decimal) -> Decimal:
        """Round price down to the market tick size."""
        tick = self._price_tick()
        return price.quantize(tick, rounding=ROUND_DOWN)

    def _format_limit_price(self, price: Decimal) -> str:
        """Format limit price for the API based on market tick size."""
        tick = self._price_tick()
        quantized = price.quantize(tick)
        if tick == Decimal("1"):
            return str(int(quantized))
        decimals = abs(tick.as_tuple().exponent)
        return f"{quantized:.{decimals}f}"

    def calculate_optimal_price(self, best_bid: Decimal, best_ask: Decimal) -> Decimal:
        """
        Calculate the optimal bid price to be at the top of the order book.

        The optimal price is best_bid + tick size (1 CLP for BTC, 0.01 for USDC),
        but must be less than best_ask to avoid immediate execution.

        Args:
            best_bid: Current best bid price.
            best_ask: Current best ask price.

        Returns:
            Optimal bid price.
        """
        tick = self._price_tick()
        optimal = self._round_price_up(best_bid + tick)

        # Ensure we don't cross the spread
        if optimal >= best_ask:
            optimal = self._round_price_up(best_bid)

        return optimal

    def calculate_optimal_sell_price(self, best_bid: Decimal, best_ask: Decimal) -> Decimal:
        """
        Calculate the optimal ask price to be at the top of the order book.

        The optimal price is best_ask - tick size, but must be above best_bid
        to avoid immediate execution.

        Args:
            best_bid: Current best bid price.
            best_ask: Current best ask price.

        Returns:
            Optimal ask price.
        """
        tick = self._price_tick()
        optimal = self._round_price_down(best_ask - tick)

        if optimal <= best_bid:
            optimal = self._round_price_down(best_ask)

        return optimal

    def calculate_crypto_amount(self, clp_amount: Decimal, price: Decimal) -> Decimal:
        """
        Calculate how much crypto can be bought with the given CLP amount.

        Args:
            clp_amount: Amount of CLP to spend.
            price: Price per unit of crypto.

        Returns:
            Amount of crypto to buy.

        Raises:
            BudaAPIError: If the calculated amount is below minimum.
        """
        amount = calculate_amount_for_clp(clp_amount, price, self.min_amount)

        if amount < self.min_amount:
            raise BudaAPIError(
                f"Order amount {format_crypto(amount, self.currency)} is below "
                f"minimum {format_crypto(self.min_amount, self.currency)}"
            )

        return amount

    def quantize_crypto_amount(self, amount: Decimal) -> Decimal:
        """
        Quantize crypto amount to exchange precision.

        Args:
            amount: Crypto amount.

        Returns:
            Quantized crypto amount.
        """
        return amount.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

    def is_best_bid(self, current_price: Decimal) -> bool:
        """
        Check if our order is at the best bid position.

        Args:
            current_price: Our order's price.

        Returns:
            True if we are the best bid, False otherwise.
        """
        best_bid, _ = self.get_best_prices()
        return current_price >= best_bid

    def is_best_ask(self, current_price: Decimal) -> bool:
        """
        Check if our order is at the best ask position.

        Args:
            current_price: Our order's price.

        Returns:
            True if we are the best ask, False otherwise.
        """
        _, best_ask = self.get_best_prices()
        return current_price <= best_ask

    def place_order(self, amount: Decimal, price: Decimal, order_type: str = "Bid") -> dict:
        """
        Place a limit order.

        Args:
            amount: Amount of crypto to trade.
            price: Limit price in CLP.
            order_type: Order type ('Bid' for buy, 'Ask' for sell).

        Returns:
            Order information.
        """
        if self.dry_run:
            limit_price = self._format_limit_price(price)
            print_status("[DRY RUN] Would place order:", "INFO")
            print(f"    Type: {order_type}")
            print(f"    Amount: {format_crypto(amount, self.currency)}")
            print(f"    Price: {format_clp(price)}")
            print(f"    Total: {format_clp(amount * price)}")
            return {
                "id": "dry-run-order",
                "state": "pending",
                "amount": [str(amount), self.currency.upper()],
                "limit": [limit_price, "CLP"],
            }

        order = self.client.create_limit_order(
            market_id=self.market_id,
            order_type=order_type,
            amount=str(amount),
            limit_price=self._format_limit_price(price)
        )

        self._current_order_id = order.get("id")
        self._last_action_ts = time.time()
        return order

    def cancel_current_order(self, order_id: Optional[str] = None) -> bool:
        """
        Cancel the current active order.

        Returns:
            True if canceled successfully, False otherwise.
        """
        target_id = order_id or self._current_order_id
        if not target_id:
            return True

        if self.dry_run:
            print_status(
                f"[DRY RUN] Would cancel order {target_id}", "INFO")
            if target_id == self._current_order_id:
                self._current_order_id = None
            return True

        try:
            self.client.cancel_order(target_id)
            print_status(f"Cancel requested for order {target_id}", "OK")

            # Wait briefly for cancellation to be reflected before placing a new order.
            for _ in range(10):
                state, _, _, _ = self.get_order_state(target_id)
                if state in ("canceled", "canceled_and_traded", "traded"):
                    if target_id == self._current_order_id:
                        self._current_order_id = None
                    print_status(f"Order {target_id} canceled ({state}).", "OK")
                    return True
                time.sleep(0.2)

            print_status(
                f"Order {target_id} not confirmed canceled yet. Skipping reprice.", "WARN"
            )
            return False
        except BudaAPIError as e:
            print_status(f"Failed to cancel order: {e}", "ERROR")
            return False

    def get_order_state(self, order_id: str) -> Tuple[str, Decimal, Decimal, Decimal]:
        """
        Get the current state and execution details of an order.

        Args:
            order_id: The order ID.

        Returns:
            Tuple of (state, traded_crypto, order_price, traded_clp).
        """
        if self.dry_run:
            return "pending", Decimal("0"), Decimal("0"), Decimal("0")

        if self._realtime:
            order = self._realtime.order_state.get_order(order_id)
            if order:
                return self._parse_order_state(order)

        order = self.client.get_order(order_id)
        return self._parse_order_state(order)

    def calculate_remaining_clp(self) -> Decimal:
        """
        Calculate remaining CLP to spend.

        Returns:
            Remaining CLP (target - executed).
        """
        return self._total_clp_target - self._total_clp_executed

    def can_place_new_order(self, remaining_clp: Decimal) -> bool:
        """
        Check if remaining CLP is enough to place a new order.

        Args:
            remaining_clp: Remaining CLP to spend.

        Returns:
            True if can place order, False if below minimum.
        """
        min_clp = self.MIN_CLP.get(self.market_id, Decimal("2000"))
        return remaining_clp >= min_clp

    def update_execution_tracking(self, traded_crypto: Decimal, traded_clp: Decimal) -> None:
        """
        Update execution tracking with new partial fill.

        Args:
            traded_crypto: Crypto amount traded in this fill.
            traded_clp: CLP amount spent in this fill.
        """
        self._total_crypto_received += traded_crypto
        self._total_clp_executed += traded_clp

    def update_sell_execution_tracking(self, traded_crypto: Decimal, traded_clp: Decimal) -> None:
        """
        Update execution tracking with new sell fill.

        Args:
            traded_crypto: Crypto amount sold in this fill.
            traded_clp: CLP amount received in this fill.
        """
        self._total_crypto_executed += traded_crypto
        self._total_clp_received += traded_clp

    def print_progress(self) -> None:
        """Print current execution progress."""
        remaining = self.calculate_remaining_clp()
        progress_pct = (self._total_clp_executed / self._total_clp_target *
                        100) if self._total_clp_target > 0 else Decimal("0")
        print_status(
            f"Progress: {format_clp(self._total_clp_executed)} / {format_clp(self._total_clp_target)} ({progress_pct:.1f}%)",
            "INFO"
        )
        print_status(
            f"Crypto received: {format_crypto(self._total_crypto_received, self.currency)}", "INFO")
        print_status(f"Remaining: {format_clp(remaining)}", "INFO")

    def print_final_summary(self) -> None:
        """Print final execution summary."""
        print()
        print_status("=" * 50, "INFO")
        print_status("EXECUTION SUMMARY", "INFO")
        print_status("=" * 50, "INFO")
        print_status(f"Target: {format_clp(self._total_clp_target)}", "INFO")
        print_status(f"Executed: {format_clp(self._total_clp_executed)}", "OK")
        print_status(
            f"Crypto received: {format_crypto(self._total_crypto_received, self.currency)}", "OK")
        if self._total_crypto_received > 0:
            avg_price = self._total_clp_executed / self._total_crypto_received
            print_status(f"Average price: {format_clp(avg_price)}", "INFO")
        remaining = self.calculate_remaining_clp()
        if remaining > 0:
            print_status(
                f"Remaining (not executed): {format_clp(remaining)}", "WARN")
        print_status("=" * 50, "INFO")

    def print_sell_progress(self) -> None:
        """Print current sell execution progress."""
        remaining = self._total_crypto_target - self._total_crypto_executed
        progress_pct = (self._total_crypto_executed / self._total_crypto_target *
                        100) if self._total_crypto_target > 0 else Decimal("0")
        print_status(
            f"Progress: {format_crypto(self._total_crypto_executed, self.currency)} / "
            f"{format_crypto(self._total_crypto_target, self.currency)} ({progress_pct:.1f}%)",
            "INFO"
        )
        print_status(
            f"CLP received: {format_clp(self._total_clp_received)}", "INFO")
        print_status(
            f"Remaining: {format_crypto(remaining, self.currency)}", "INFO")

    def print_sell_final_summary(self) -> None:
        """Print final sell execution summary."""
        print()
        print_status("=" * 50, "INFO")
        print_status("EXECUTION SUMMARY", "INFO")
        print_status("=" * 50, "INFO")
        print_status(
            f"Target: {format_crypto(self._total_crypto_target, self.currency)}", "INFO")
        print_status(
            f"Executed: {format_crypto(self._total_crypto_executed, self.currency)}", "OK")
        print_status(
            f"CLP received: {format_clp(self._total_clp_received)}", "OK")
        if self._total_crypto_executed > 0:
            avg_price = self._total_clp_received / self._total_crypto_executed
            print_status(f"Average price: {format_clp(avg_price)}", "INFO")
        remaining = self._total_crypto_target - self._total_crypto_executed
        if remaining > 0:
            print_status(
                f"Remaining (not executed): {format_crypto(remaining, self.currency)}", "WARN")
        print_status("=" * 50, "INFO")

    def execute_buy_order(self, clp_amount: Decimal) -> None:
        """
        Execute the main trading loop.

        Args:
            clp_amount: Amount of CLP to spend on the order.
        """
        self._running = True
        clp_amount = Decimal(str(clp_amount))
        self._active_side = "buy"

        # Initialize execution tracking
        self._total_clp_target = clp_amount
        self._total_clp_executed = Decimal("0")
        self._total_crypto_received = Decimal("0")

        # Validate minimum CLP amount for this market
        min_clp = self.MIN_CLP.get(self.market_id, Decimal("2000"))
        if clp_amount < min_clp:
            raise BudaAPIError(
                f"Amount {format_clp(clp_amount)} is below minimum {format_clp(min_clp)} for {self.market_id.upper()}"
            )

        print_status(f"Starting {self.currency.upper()} buy bot", "INFO")
        print_status(f"Target spend: {format_clp(clp_amount)}", "INFO")
        print_status(f"Market: {self.market_id.upper()}", "INFO")
        print_status(f"Check interval: {self.interval}s", "INFO")
        if self.dry_run:
            print_status("DRY RUN MODE - No orders will be placed", "WARN")
        print()

        # Step 1: Verify balance
        self.verify_balance(clp_amount)
        print()

        # Step 2: Start realtime (book + orders if available)
        self._start_realtime()
        try:
            if self._realtime and not self._realtime.book_state.wait_ready(5):
                print_status(
                    "Realtime book not ready; using REST until available.", "WARN"
                )

            # Step 3: Get initial prices
            print_status("Fetching order book...", "INFO")
            bids, asks = self.get_order_book_levels()
            if not bids or not asks:
                raise BudaAPIError("Order book empty")
            best_bid, best_ask = bids[0][0], asks[0][0]
            print_status(f"Best bid: {format_clp(best_bid)}", "INFO")
            print_status(f"Best ask: {format_clp(best_ask)}", "INFO")
            print_status(f"Spread: {format_clp(best_ask - best_bid)}", "INFO")
            print_status(f"Strategy: {self._strategy_label()}", "INFO")
            print()

            # Step 4: Calculate optimal price and amount
            target_price = self.calculate_strategy_price(
                "buy", bids, asks, best_bid, best_ask
            )
            amount = self.calculate_crypto_amount(clp_amount, target_price)

            print_status(f"Target price: {format_clp(target_price)}", "INFO")
            print_status(
                f"Order amount: {format_crypto(amount, self.currency)}", "INFO")
            print_status(
                f"Estimated total: {format_clp(amount * target_price)}", "INFO")
            print()

            # Step 5: Place initial order
            print_status("Placing initial order...", "INFO")
            order = self.place_order(amount, target_price)
            order_id = order.get("id")
            current_price = target_price
            print_status(f"Order placed! ID: {order_id}", "OK")
            print_order_info(order, self.currency)
            print()

            # Step 6: Monitoring loop
            print_status("Starting monitoring loop. Press Ctrl+C to stop.", "INFO")
            print()

            # Track last known traded amounts for this order
            last_traded_crypto = Decimal("0")
            last_traded_clp = Decimal("0")

            while self._running:
                try:
                    if self._realtime:
                        self._realtime.book_state.wait_for_top_change(self.interval)
                    else:
                        time.sleep(self.interval)

                    if not self._running:
                        break

                    if self._realtime and time.time() - self._last_sanity_ts >= self._sanity_interval:
                        try:
                            order_book = self.client.get_order_book(self.market_id)
                            self._realtime.book_state.apply_snapshot(
                                order_book.get("bids", []),
                                order_book.get("asks", [])
                            )
                            self._last_sanity_ts = time.time()
                        except BudaAPIError as e:
                            print_status(
                                f"Sanity check failed: {e}", "WARN"
                            )

                    # Check order state
                    state, traded_crypto, order_price, traded_clp = self.get_order_state(
                        order_id)

                    # Check for partial fills on current order
                    new_traded_crypto = traded_crypto - last_traded_crypto
                    new_traded_clp = traded_clp - last_traded_clp
                    if new_traded_crypto > 0:
                        print_status(
                            f"Partial fill: +{format_crypto(new_traded_crypto, self.currency)}", "OK")
                        last_traded_crypto = traded_crypto
                        last_traded_clp = traded_clp

                    if state == "traded":
                        # Order fully executed - update tracking and finish
                        self.update_execution_tracking(traded_crypto, traded_clp)
                        print_status("Order fully executed!", "OK")
                        self._current_order_id = None
                        self.print_final_summary()
                        return

                    if state == "canceled_and_traded":
                        # Partial execution + canceled (can happen from external cancel)
                        print_status(
                            "Order was partially executed and canceled.", "WARN")
                        self.update_execution_tracking(traded_crypto, traded_clp)
                        self.print_progress()
                        self._current_order_id = None

                        # Check if we can continue
                        remaining_clp = self.calculate_remaining_clp()
                        if not self.can_place_new_order(remaining_clp):
                            print_status(
                                f"Remaining {format_clp(remaining_clp)} is below minimum. Finishing.", "WARN")
                            self.print_final_summary()
                            return

                        # Place new order with remaining amount
                        bids, asks = self.get_order_book_levels()
                        if not bids or not asks:
                            raise BudaAPIError("Order book empty")
                        best_bid, best_ask = bids[0][0], asks[0][0]
                        target_price = self.calculate_strategy_price(
                            "buy", bids, asks, best_bid, best_ask
                        )
                        amount = self.calculate_crypto_amount(
                            remaining_clp, target_price)
                        print_status(
                            f"Placing new order with remaining {format_clp(remaining_clp)}", "INFO")
                        order = self.place_order(amount, target_price)
                        order_id = order.get("id")
                        current_price = target_price
                        last_traded_crypto = Decimal("0")
                        last_traded_clp = Decimal("0")
                        print_status(f"New order placed! ID: {order_id}", "OK")
                        continue

                    if state in ("canceled", "canceling"):
                        print_status("Order was canceled externally.", "WARN")
                        self.print_final_summary()
                        return

                    # Check if we're still best bid
                    print_status("Checking position...", "INFO")
                    bids, asks = self.get_order_book_levels()
                    if not bids or not asks:
                        raise BudaAPIError("Order book empty")
                    best_bid, best_ask = bids[0][0], asks[0][0]
                    target_price = self.calculate_strategy_price(
                        "buy", bids, asks, best_bid, best_ask
                    )

                    if self.strategy == "top" and current_price >= best_bid:
                        print_status(
                            f"Still best bid at {format_clp(current_price)} "
                            f"(market: {format_clp(best_bid)})",
                            "OK"
                        )
                    elif self.strategy != "top" and current_price == target_price:
                        print_status(
                            f"Still at target price {format_clp(current_price)} "
                            f"(strategy: {self._strategy_label()})",
                            "OK"
                        )
                    else:
                        if time.time() - self._last_action_ts < self._min_action_interval:
                            continue

                        if self.strategy == "top":
                            print_status(
                                f"Outbid! Our price: {format_clp(current_price)}, "
                                f"Best bid: {format_clp(best_bid)}",
                                "WARN"
                            )
                        else:
                            print_status(
                                f"Target moved. Our price: {format_clp(current_price)}, "
                                f"New target: {format_clp(target_price)}",
                                "WARN"
                            )

                        # Cancel current order - may result in partial fill
                        if not self.cancel_current_order(order_id):
                            print_status(
                                "Could not cancel order. Retrying...", "WARN")
                            continue

                        # Re-fetch order state after cancellation to capture any partial fills
                        state, traded_crypto, order_price, traded_clp = self.get_order_state(
                            order_id)

                        # Track any execution that happened before cancellation
                        if traded_crypto > 0:
                            self.update_execution_tracking(
                                traded_crypto, traded_clp)
                            print_status(
                                f"Partial execution before cancel: {format_crypto(traded_crypto, self.currency)}", "INFO")
                            self.print_progress()

                        # Check if we can place a new order
                        remaining_clp = self.calculate_remaining_clp()
                        if not self.can_place_new_order(remaining_clp):
                            print_status(
                                f"Remaining {format_clp(remaining_clp)} is below minimum. Finishing.", "WARN")
                            self.print_final_summary()
                            return

                        # Recalculate price and amount with remaining CLP
                        target_price = self.calculate_strategy_price(
                            "buy", bids, asks, best_bid, best_ask
                        )
                        amount = self.calculate_crypto_amount(
                            remaining_clp, target_price)

                        print_status(
                            f"New target price: {format_clp(target_price)}", "INFO")
                        print_status(
                            f"Order amount: {format_crypto(amount, self.currency)} ({format_clp(remaining_clp)})", "INFO")

                        # Place new order
                        order = self.place_order(amount, target_price)
                        order_id = order.get("id")
                        current_price = target_price
                        last_traded_crypto = Decimal("0")
                        last_traded_clp = Decimal("0")
                        print_status(f"New order placed! ID: {order_id}", "OK")

                except BudaAPIError as e:
                    print_status(f"API error: {e}", "ERROR")
                    print_status("Will retry on next interval...", "WARN")
                except Exception as e:
                    print_status(f"Unexpected error: {e}", "ERROR")
                    print_status("Will retry on next interval...", "WARN")
        finally:
            self._stop_realtime()

    def execute_sell_order(self, crypto_amount: Decimal) -> None:
        """
        Execute the main trading loop for selling.

        Args:
            crypto_amount: Amount of crypto to sell.
        """
        self._running = True
        crypto_amount = Decimal(str(crypto_amount))
        self._active_side = "sell"

        # Initialize execution tracking
        self._total_crypto_target = crypto_amount
        self._total_crypto_executed = Decimal("0")
        self._total_clp_received = Decimal("0")

        if crypto_amount < self.min_amount:
            raise BudaAPIError(
                f"Amount {format_crypto(crypto_amount, self.currency)} is below minimum "
                f"{format_crypto(self.min_amount, self.currency)} for {self.market_id.upper()}"
            )

        print_status(f"Starting {self.currency.upper()} sell bot", "INFO")
        print_status(
            f"Target sell: {format_crypto(crypto_amount, self.currency)}", "INFO")
        print_status(f"Market: {self.market_id.upper()}", "INFO")
        print_status(f"Check interval: {self.interval}s", "INFO")
        if self.dry_run:
            print_status("DRY RUN MODE - No orders will be placed", "WARN")
        print()

        # Step 1: Verify balance
        self.verify_crypto_balance(crypto_amount)
        print()

        # Step 2: Start realtime (book + orders if available)
        self._start_realtime()
        try:
            if self._realtime and not self._realtime.book_state.wait_ready(5):
                print_status(
                    "Realtime book not ready; using REST until available.", "WARN"
                )

            # Step 3: Get initial prices
            print_status("Fetching order book...", "INFO")
            bids, asks = self.get_order_book_levels()
            if not bids or not asks:
                raise BudaAPIError("Order book empty")
            best_bid, best_ask = bids[0][0], asks[0][0]
            print_status(f"Best bid: {format_clp(best_bid)}", "INFO")
            print_status(f"Best ask: {format_clp(best_ask)}", "INFO")
            print_status(f"Spread: {format_clp(best_ask - best_bid)}", "INFO")
            print_status(f"Strategy: {self._strategy_label()}", "INFO")
            print()

            # Step 4: Calculate optimal price and amount
            target_price = self.calculate_strategy_price(
                "sell", bids, asks, best_bid, best_ask
            )
            amount = self.quantize_crypto_amount(crypto_amount)

            if amount < self.min_amount:
                raise BudaAPIError(
                    f"Order amount {format_crypto(amount, self.currency)} is below "
                    f"minimum {format_crypto(self.min_amount, self.currency)}"
                )

            print_status(f"Target price: {format_clp(target_price)}", "INFO")
            print_status(
                f"Order amount: {format_crypto(amount, self.currency)}", "INFO")
            print_status(
                f"Estimated total: {format_clp(amount * target_price)}", "INFO")
            print()

            # Step 5: Place initial order
            print_status("Placing initial order...", "INFO")
            order = self.place_order(amount, target_price, order_type="Ask")
            order_id = order.get("id")
            current_price = target_price
            print_status(f"Order placed! ID: {order_id}", "OK")
            print_order_info(order, self.currency)
            print()

            # Step 6: Monitoring loop
            print_status("Starting monitoring loop. Press Ctrl+C to stop.", "INFO")
            print()

            # Track last known traded amounts for this order
            last_traded_crypto = Decimal("0")
            last_traded_clp = Decimal("0")

            while self._running:
                try:
                    if self._realtime:
                        self._realtime.book_state.wait_for_top_change(self.interval)
                    else:
                        time.sleep(self.interval)

                    if not self._running:
                        break

                    if self._realtime and time.time() - self._last_sanity_ts >= self._sanity_interval:
                        try:
                            order_book = self.client.get_order_book(self.market_id)
                            self._realtime.book_state.apply_snapshot(
                                order_book.get("bids", []),
                                order_book.get("asks", [])
                            )
                            self._last_sanity_ts = time.time()
                        except BudaAPIError as e:
                            print_status(
                                f"Sanity check failed: {e}", "WARN"
                            )

                    # Check order state
                    state, traded_crypto, order_price, traded_clp = self.get_order_state(
                        order_id)

                    # Check for partial fills on current order
                    new_traded_crypto = traded_crypto - last_traded_crypto
                    new_traded_clp = traded_clp - last_traded_clp
                    if new_traded_crypto > 0:
                        print_status(
                            f"Partial fill: +{format_crypto(new_traded_crypto, self.currency)}", "OK")
                        last_traded_crypto = traded_crypto
                        last_traded_clp = traded_clp

                    if state == "traded":
                        # Order fully executed - update tracking and finish
                        self.update_sell_execution_tracking(
                            traded_crypto, traded_clp)
                        print_status("Order fully executed!", "OK")
                        self._current_order_id = None
                        self.print_sell_final_summary()
                        return

                    if state == "canceled_and_traded":
                        # Partial execution + canceled (can happen from external cancel)
                        print_status(
                            "Order was partially executed and canceled.", "WARN")
                        self.update_sell_execution_tracking(
                            traded_crypto, traded_clp)
                        self.print_sell_progress()
                        self._current_order_id = None

                        remaining_crypto = self._total_crypto_target - \
                            self._total_crypto_executed
                        if remaining_crypto < self.min_amount:
                            print_status(
                                f"Remaining {format_crypto(remaining_crypto, self.currency)} is below minimum. Finishing.", "WARN")
                            self.print_sell_final_summary()
                            return

                        bids, asks = self.get_order_book_levels()
                        if not bids or not asks:
                            raise BudaAPIError("Order book empty")
                        best_bid, best_ask = bids[0][0], asks[0][0]
                        target_price = self.calculate_strategy_price(
                            "sell", bids, asks, best_bid, best_ask
                        )
                        amount = self.quantize_crypto_amount(remaining_crypto)
                        print_status(
                            f"Placing new order with remaining {format_crypto(remaining_crypto, self.currency)}", "INFO")
                        order = self.place_order(
                            amount, target_price, order_type="Ask")
                        order_id = order.get("id")
                        current_price = target_price
                        last_traded_crypto = Decimal("0")
                        last_traded_clp = Decimal("0")
                        print_status(f"New order placed! ID: {order_id}", "OK")
                        continue

                    if state in ("canceled", "canceling"):
                        print_status("Order was canceled externally.", "WARN")
                        self.print_sell_final_summary()
                        return

                    # Check if we're still best ask
                    print_status("Checking position...", "INFO")
                    bids, asks = self.get_order_book_levels()
                    if not bids or not asks:
                        raise BudaAPIError("Order book empty")
                    best_bid, best_ask = bids[0][0], asks[0][0]
                    target_price = self.calculate_strategy_price(
                        "sell", bids, asks, best_bid, best_ask
                    )

                    if self.strategy == "top" and current_price <= best_ask:
                        print_status(
                            f"Still best ask at {format_clp(current_price)} "
                            f"(market: {format_clp(best_ask)})",
                            "OK"
                        )
                    elif self.strategy != "top" and current_price == target_price:
                        print_status(
                            f"Still at target price {format_clp(current_price)} "
                            f"(strategy: {self._strategy_label()})",
                            "OK"
                        )
                    else:
                        if time.time() - self._last_action_ts < self._min_action_interval:
                            continue

                        if self.strategy == "top":
                            print_status(
                                f"Outasked! Our price: {format_clp(current_price)}, "
                                f"Best ask: {format_clp(best_ask)}",
                                "WARN"
                            )
                        else:
                            print_status(
                                f"Target moved. Our price: {format_clp(current_price)}, "
                                f"New target: {format_clp(target_price)}",
                                "WARN"
                            )

                        if not self.cancel_current_order(order_id):
                            print_status(
                                "Could not cancel order. Retrying...", "WARN")
                            continue

                        state, traded_crypto, order_price, traded_clp = self.get_order_state(
                            order_id)

                        if traded_crypto > 0:
                            self.update_sell_execution_tracking(
                                traded_crypto, traded_clp)
                            print_status(
                                f"Partial execution before cancel: {format_crypto(traded_crypto, self.currency)}", "INFO")
                            self.print_sell_progress()

                        remaining_crypto = self._total_crypto_target - \
                            self._total_crypto_executed
                        if remaining_crypto < self.min_amount:
                            print_status(
                                f"Remaining {format_crypto(remaining_crypto, self.currency)} is below minimum. Finishing.", "WARN")
                            self.print_sell_final_summary()
                            return

                        target_price = self.calculate_strategy_price(
                            "sell", bids, asks, best_bid, best_ask
                        )
                        amount = self.quantize_crypto_amount(remaining_crypto)

                        print_status(
                            f"New target price: {format_clp(target_price)}", "INFO")
                        print_status(
                            f"Order amount: {format_crypto(amount, self.currency)}", "INFO")

                        order = self.place_order(
                            amount, target_price, order_type="Ask")
                        order_id = order.get("id")
                        current_price = target_price
                        last_traded_crypto = Decimal("0")
                        last_traded_clp = Decimal("0")
                        print_status(f"New order placed! ID: {order_id}", "OK")

                except BudaAPIError as e:
                    print_status(f"API error: {e}", "ERROR")
                    print_status("Will retry on next interval...", "WARN")
                except Exception as e:
                    print_status(f"Unexpected error: {e}", "ERROR")
                    print_status("Will retry on next interval...", "WARN")
        finally:
            self._stop_realtime()
