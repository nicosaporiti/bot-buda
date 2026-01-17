"""Trading bot logic for maintaining best bid position."""

import signal
import sys
import time
from decimal import Decimal
from typing import Optional, Tuple

from .api import BudaClient, BudaAPIError, InsufficientBalanceError
from .utils import (
    format_clp,
    format_crypto,
    parse_order_book_entry,
    calculate_amount_for_clp,
    round_price_up,
    print_status,
    print_order_info,
)


class TradingBot:
    """Bot for placing and maintaining best bid orders on Buda.com."""

    # Minimum order amounts per market (in crypto)
    MIN_AMOUNTS = {
        "btc-clp": Decimal("0.00002"),
        "usdc-clp": Decimal("1.0"),
    }

    # Minimum order value in CLP per market
    MIN_CLP = {
        "btc-clp": Decimal("2000"),
        "usdc-clp": Decimal("1000"),
    }

    def __init__(
        self,
        client: BudaClient,
        currency: str,
        interval: int = 30,
        dry_run: bool = False
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
        self.min_amount = self.MIN_AMOUNTS.get(self.market_id, Decimal("0.00001"))

        self._current_order_id: Optional[str] = None
        self._running = False

        # Execution tracking for partial fills
        self._total_clp_target: Decimal = Decimal("0")
        self._total_clp_executed: Decimal = Decimal("0")
        self._total_crypto_received: Decimal = Decimal("0")

        # Setup signal handlers for clean exit
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        """Handle interrupt signals for clean shutdown."""
        print("\n")
        print_status("Interrupt received. Cleaning up...", "WARN")
        self._running = False

        if self._current_order_id and not self.dry_run:
            try:
                # Check for partial execution before canceling
                state, traded_crypto, order_price, traded_clp = self.get_order_state(self._current_order_id)

                print_status(f"Canceling active order {self._current_order_id}...", "INFO")
                self.client.cancel_order(self._current_order_id)
                print_status("Order canceled successfully.", "OK")

                # Track any partial execution from the canceled order
                if traded_crypto > 0:
                    self.update_execution_tracking(traded_crypto, traded_clp)
                    print_status(f"Partial execution captured: {format_crypto(traded_crypto, self.currency)}", "INFO")

            except BudaAPIError as e:
                print_status(f"Failed to cancel order: {e}", "ERROR")

        # Show final summary if we had any target set
        if self._total_clp_target > 0:
            self.print_final_summary()

        sys.exit(0)

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

    def get_best_prices(self) -> Tuple[Decimal, Decimal]:
        """
        Get the best bid and ask prices from the order book.

        Returns:
            Tuple of (best_bid, best_ask) prices.
        """
        order_book = self.client.get_order_book(self.market_id)

        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        if not bids:
            raise BudaAPIError("No bids in order book")
        if not asks:
            raise BudaAPIError("No asks in order book")

        best_bid, _ = parse_order_book_entry(bids[0])
        best_ask, _ = parse_order_book_entry(asks[0])

        return best_bid, best_ask

    def calculate_optimal_price(self, best_bid: Decimal, best_ask: Decimal) -> Decimal:
        """
        Calculate the optimal bid price to be at the top of the order book.

        The optimal price is best_bid + 1 CLP, but must be less than best_ask
        to avoid immediate execution.

        Args:
            best_bid: Current best bid price.
            best_ask: Current best ask price.

        Returns:
            Optimal bid price.
        """
        optimal = round_price_up(best_bid + 1)

        # Ensure we don't cross the spread
        if optimal >= best_ask:
            optimal = round_price_up(best_bid)

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

    def place_order(self, amount: Decimal, price: Decimal) -> dict:
        """
        Place a limit buy order.

        Args:
            amount: Amount of crypto to buy.
            price: Limit price in CLP.

        Returns:
            Order information.
        """
        if self.dry_run:
            print_status("[DRY RUN] Would place order:", "INFO")
            print(f"    Amount: {format_crypto(amount, self.currency)}")
            print(f"    Price: {format_clp(price)}")
            print(f"    Total: {format_clp(amount * price)}")
            return {
                "id": "dry-run-order",
                "state": "pending",
                "amount": [str(amount), self.currency.upper()],
                "limit": [str(int(price)), "CLP"],
            }

        order = self.client.create_limit_order(
            market_id=self.market_id,
            order_type="Bid",
            amount=str(amount),
            limit_price=str(int(price))
        )

        self._current_order_id = order.get("id")
        return order

    def cancel_current_order(self) -> bool:
        """
        Cancel the current active order.

        Returns:
            True if canceled successfully, False otherwise.
        """
        if not self._current_order_id:
            return True

        if self.dry_run:
            print_status(f"[DRY RUN] Would cancel order {self._current_order_id}", "INFO")
            self._current_order_id = None
            return True

        try:
            self.client.cancel_order(self._current_order_id)
            print_status(f"Canceled order {self._current_order_id}", "OK")
            self._current_order_id = None
            return True
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

        order = self.client.get_order(order_id)
        state = order.get("state", "unknown")

        # Get traded crypto amount
        traded = order.get("traded_amount", ["0"])
        if isinstance(traded, list):
            traded = traded[0]
        traded_crypto = Decimal(str(traded))

        # Get order price
        limit = order.get("limit", ["0"])
        if isinstance(limit, list):
            limit = limit[0]
        order_price = Decimal(str(limit))

        # Get total CLP exchanged (paid_fee is deducted from what we pay)
        total_exchanged = order.get("total_exchanged", ["0"])
        if isinstance(total_exchanged, list):
            total_exchanged = total_exchanged[0]
        traded_clp = Decimal(str(total_exchanged))

        return state, traded_crypto, order_price, traded_clp

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

    def print_progress(self) -> None:
        """Print current execution progress."""
        remaining = self.calculate_remaining_clp()
        progress_pct = (self._total_clp_executed / self._total_clp_target * 100) if self._total_clp_target > 0 else Decimal("0")
        print_status(
            f"Progress: {format_clp(self._total_clp_executed)} / {format_clp(self._total_clp_target)} ({progress_pct:.1f}%)",
            "INFO"
        )
        print_status(f"Crypto received: {format_crypto(self._total_crypto_received, self.currency)}", "INFO")
        print_status(f"Remaining: {format_clp(remaining)}", "INFO")

    def print_final_summary(self) -> None:
        """Print final execution summary."""
        print()
        print_status("=" * 50, "INFO")
        print_status("EXECUTION SUMMARY", "INFO")
        print_status("=" * 50, "INFO")
        print_status(f"Target: {format_clp(self._total_clp_target)}", "INFO")
        print_status(f"Executed: {format_clp(self._total_clp_executed)}", "OK")
        print_status(f"Crypto received: {format_crypto(self._total_crypto_received, self.currency)}", "OK")
        if self._total_crypto_received > 0:
            avg_price = self._total_clp_executed / self._total_crypto_received
            print_status(f"Average price: {format_clp(avg_price)}", "INFO")
        remaining = self.calculate_remaining_clp()
        if remaining > 0:
            print_status(f"Remaining (not executed): {format_clp(remaining)}", "WARN")
        print_status("=" * 50, "INFO")

    def execute_buy_order(self, clp_amount: Decimal) -> None:
        """
        Execute the main trading loop.

        Args:
            clp_amount: Amount of CLP to spend on the order.
        """
        self._running = True
        clp_amount = Decimal(str(clp_amount))

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

        # Step 2: Get initial prices
        print_status("Fetching order book...", "INFO")
        best_bid, best_ask = self.get_best_prices()
        print_status(f"Best bid: {format_clp(best_bid)}", "INFO")
        print_status(f"Best ask: {format_clp(best_ask)}", "INFO")
        print_status(f"Spread: {format_clp(best_ask - best_bid)}", "INFO")
        print()

        # Step 3: Calculate optimal price and amount
        optimal_price = self.calculate_optimal_price(best_bid, best_ask)
        amount = self.calculate_crypto_amount(clp_amount, optimal_price)

        print_status(f"Optimal price: {format_clp(optimal_price)}", "INFO")
        print_status(f"Order amount: {format_crypto(amount, self.currency)}", "INFO")
        print_status(f"Estimated total: {format_clp(amount * optimal_price)}", "INFO")
        print()

        # Step 4: Place initial order
        print_status("Placing initial order...", "INFO")
        order = self.place_order(amount, optimal_price)
        order_id = order.get("id")
        current_price = optimal_price
        print_status(f"Order placed! ID: {order_id}", "OK")
        print_order_info(order, self.currency)
        print()

        # Step 5: Monitoring loop
        print_status("Starting monitoring loop. Press Ctrl+C to stop.", "INFO")
        print()

        # Track last known traded amounts for this order
        last_traded_crypto = Decimal("0")
        last_traded_clp = Decimal("0")

        while self._running:
            try:
                time.sleep(self.interval)

                if not self._running:
                    break

                # Check order state
                state, traded_crypto, order_price, traded_clp = self.get_order_state(order_id)

                # Check for partial fills on current order
                new_traded_crypto = traded_crypto - last_traded_crypto
                new_traded_clp = traded_clp - last_traded_clp
                if new_traded_crypto > 0:
                    print_status(f"Partial fill: +{format_crypto(new_traded_crypto, self.currency)}", "OK")
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
                    print_status("Order was partially executed and canceled.", "WARN")
                    self.update_execution_tracking(traded_crypto, traded_clp)
                    self.print_progress()
                    self._current_order_id = None

                    # Check if we can continue
                    remaining_clp = self.calculate_remaining_clp()
                    if not self.can_place_new_order(remaining_clp):
                        print_status(f"Remaining {format_clp(remaining_clp)} is below minimum. Finishing.", "WARN")
                        self.print_final_summary()
                        return

                    # Place new order with remaining amount
                    best_bid, best_ask = self.get_best_prices()
                    optimal_price = self.calculate_optimal_price(best_bid, best_ask)
                    amount = self.calculate_crypto_amount(remaining_clp, optimal_price)
                    print_status(f"Placing new order with remaining {format_clp(remaining_clp)}", "INFO")
                    order = self.place_order(amount, optimal_price)
                    order_id = order.get("id")
                    current_price = optimal_price
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
                best_bid, best_ask = self.get_best_prices()

                if self.is_best_bid(current_price):
                    print_status(
                        f"Still best bid at {format_clp(current_price)} "
                        f"(market: {format_clp(best_bid)})",
                        "OK"
                    )
                else:
                    print_status(
                        f"Outbid! Our price: {format_clp(current_price)}, "
                        f"Best bid: {format_clp(best_bid)}",
                        "WARN"
                    )

                    # Cancel current order - may result in partial fill
                    if not self.cancel_current_order():
                        print_status("Could not cancel order. Retrying...", "WARN")
                        continue

                    # Re-fetch order state after cancellation to capture any partial fills
                    state, traded_crypto, order_price, traded_clp = self.get_order_state(order_id)

                    # Track any execution that happened before cancellation
                    if traded_crypto > 0:
                        self.update_execution_tracking(traded_crypto, traded_clp)
                        print_status(f"Partial execution before cancel: {format_crypto(traded_crypto, self.currency)}", "INFO")
                        self.print_progress()

                    # Check if we can place a new order
                    remaining_clp = self.calculate_remaining_clp()
                    if not self.can_place_new_order(remaining_clp):
                        print_status(f"Remaining {format_clp(remaining_clp)} is below minimum. Finishing.", "WARN")
                        self.print_final_summary()
                        return

                    # Recalculate price and amount with remaining CLP
                    optimal_price = self.calculate_optimal_price(best_bid, best_ask)
                    amount = self.calculate_crypto_amount(remaining_clp, optimal_price)

                    print_status(f"New optimal price: {format_clp(optimal_price)}", "INFO")
                    print_status(f"Order amount: {format_crypto(amount, self.currency)} ({format_clp(remaining_clp)})", "INFO")

                    # Place new order
                    order = self.place_order(amount, optimal_price)
                    order_id = order.get("id")
                    current_price = optimal_price
                    last_traded_crypto = Decimal("0")
                    last_traded_clp = Decimal("0")
                    print_status(f"New order placed! ID: {order_id}", "OK")

            except BudaAPIError as e:
                print_status(f"API error: {e}", "ERROR")
                print_status("Will retry on next interval...", "WARN")
            except Exception as e:
                print_status(f"Unexpected error: {e}", "ERROR")
                print_status("Will retry on next interval...", "WARN")
