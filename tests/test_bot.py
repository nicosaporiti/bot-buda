"""Tests for the single-order trading strategies."""

import io
import time
import unittest
from contextlib import redirect_stdout
from decimal import Decimal

from src.api import BudaAPIError
from src.bot import TradingBot
from src.market import MarketConfig
from src.ws import RealtimeClient


def make_bot(
    price_tick: Decimal = Decimal("0.01"),
    strategy: str = "top",
    depth_ratio: Decimal = Decimal("0.9"),
) -> TradingBot:
    market = MarketConfig(
        market_id="usdc-clp",
        base_currency="usdc",
        quote_currency="clp",
        min_order_amount=Decimal("1"),
        base_decimals=6,
        quote_decimals=0,
        price_tick=price_tick,
    )
    return TradingBot(
        client=object(),
        market_config=market,
        strategy=strategy,
        depth_ratio=depth_ratio,
        register_signals=False,
    )


class RepricingClient:
    """Deterministic client that exposes an overpriced active top order."""

    def __init__(self, side: str) -> None:
        self.side = side
        self.book_reads = 0
        self.create_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.orders: dict[str, dict] = {}
        self.order_reads: dict[str, int] = {}

    def get_balance(self, currency: str) -> dict:
        amount = "100000" if currency.lower() == "clp" else "1000"
        return {"available_amount": [amount, currency.upper()]}

    def get_order_book(self, _market_id: str) -> dict:
        self.book_reads += 1
        if self.side == "buy":
            return self._buy_book()
        return self._sell_book()

    def _buy_book(self) -> dict:
        if self.book_reads <= 2:
            bids = [["931.12", "100"]]
        elif self.book_reads == 3:
            own_amount = self.create_calls[0]["amount"]
            bids = [["931.13", own_amount], ["927.85", "100"]]
        elif len(self.create_calls) == 1:
            # Cancellation is eventually consistent in the public book.
            own_amount = self.create_calls[0]["amount"]
            bids = [["931.13", own_amount], ["927.85", "100"]]
        else:
            old_amount = self.create_calls[0]["amount"]
            current_amount = self.create_calls[1]["amount"]
            bids = [
                ["931.13", old_amount],
                ["927.86", current_amount],
                ["927.85", "100"],
            ]
        return {"bids": bids, "asks": [["940.00", "100"]]}

    def _sell_book(self) -> dict:
        if self.book_reads == 1:
            asks = [["927.86", "100"]]
        elif self.book_reads == 2:
            own_amount = self.create_calls[0]["amount"]
            asks = [["927.85", own_amount], ["931.13", "100"]]
        else:
            old_amount = self.create_calls[0]["amount"]
            current_amount = self.create_calls[1]["amount"]
            asks = [
                ["927.85", old_amount],
                ["931.12", current_amount],
                ["931.13", "100"],
            ]
        return {"bids": [["920.00", "100"]], "asks": asks}

    def create_limit_order(
        self,
        market_id: str,
        order_type: str,
        amount: str,
        limit_price: str,
    ) -> dict:
        call = {
            "market_id": market_id,
            "type": order_type,
            "amount": amount,
            "limit": limit_price,
        }
        self.create_calls.append(call)
        order_id = f"order-{len(self.create_calls)}"
        order = {
            "id": order_id,
            "state": "pending",
            "amount": [amount, "USDC"],
            "original_amount": [amount, "USDC"],
            "limit": [limit_price, "CLP"],
            "traded_amount": ["0", "USDC"],
            "total_exchanged": ["0", "CLP"],
        }
        self.orders[order_id] = order
        return order

    def get_order(self, order_id: str) -> dict:
        self.order_reads[order_id] = self.order_reads.get(order_id, 0) + 1
        if order_id == "order-2" and self.order_reads[order_id] >= 2:
            order = self.orders[order_id]
            amount = order["original_amount"][0]
            limit_price = order["limit"][0]
            order["state"] = "traded"
            order["traded_amount"] = [amount, "USDC"]
            order["total_exchanged"] = [
                str(Decimal(amount) * Decimal(limit_price)),
                "CLP",
            ]
        return self.orders[order_id]

    def cancel_order(self, order_id: str) -> dict:
        self.cancel_calls.append(order_id)
        self.orders[order_id]["state"] = "canceled"
        return self.orders[order_id]


class TestTopStrategyPricing(unittest.TestCase):
    def test_initial_buy_improves_best_bid_by_one_tick(self):
        bot = make_bot()
        bids = [(Decimal("927.85"), Decimal("100"))]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy", bids, asks, bids[0][0], asks[0][0]
        )

        self.assertEqual(target, Decimal("927.86"))

    def test_buy_uses_next_level_when_best_bid_is_our_order(self):
        bot = make_bot()
        bids = [
            (Decimal("931.13"), Decimal("176.61")),
            (Decimal("927.85"), Decimal("29.64")),
        ]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("931.13"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("927.86"))

    def test_sell_uses_next_level_when_best_ask_is_our_order(self):
        bot = make_bot()
        bids = [(Decimal("920.00"), Decimal("100"))]
        asks = [
            (Decimal("927.85"), Decimal("176.61")),
            (Decimal("931.13"), Decimal("29.64")),
        ]

        target = bot.calculate_strategy_price(
            "sell",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("927.85"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("931.12"))

    def test_ambiguous_shared_top_level_keeps_current_price(self):
        bot = make_bot()
        bids = [
            (Decimal("931.13"), Decimal("200")),
            (Decimal("927.85"), Decimal("29.64")),
        ]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("931.13"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("931.13"))

    def test_stale_lower_volume_keeps_current_price(self):
        bot = make_bot()
        bids = [
            (Decimal("931.13"), Decimal("175.61")),
            (Decimal("927.85"), Decimal("29.64")),
        ]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("931.13"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("931.13"))

    def test_only_own_level_keeps_current_price(self):
        bot = make_bot()
        bids = [(Decimal("931.13"), Decimal("176.61"))]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("931.13"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("931.13"))

    def test_buy_uses_market_tick_instead_of_fixed_cent(self):
        bot = make_bot(price_tick=Decimal("1"))
        bids = [
            (Decimal("100"), Decimal("2")),
            (Decimal("90"), Decimal("3")),
        ]
        asks = [(Decimal("110"), Decimal("1"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("100"),
            own_remaining_amount=Decimal("2"),
        )

        self.assertEqual(target, Decimal("91"))

    def test_outbid_order_uses_actual_best_bid(self):
        bot = make_bot()
        bids = [
            (Decimal("931.13"), Decimal("10")),
            (Decimal("927.85"), Decimal("176.61")),
        ]
        asks = [(Decimal("932.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("927.85"),
            own_remaining_amount=Decimal("176.61"),
        )

        self.assertEqual(target, Decimal("931.14"))

    def test_canceled_level_is_ignored_until_it_disappears(self):
        bot = make_bot()
        bot._remember_canceled_level(
            "buy", "order-1", Decimal("931.13"), Decimal("176.61")
        )
        asks = [(Decimal("940.00"), Decimal("100"))]
        with_ghost = [
            (Decimal("931.13"), Decimal("176.61")),
            (Decimal("927.86"), Decimal("100")),
            (Decimal("927.85"), Decimal("29.64")),
        ]

        target = bot.calculate_strategy_price(
            "buy",
            with_ghost,
            asks,
            with_ghost[0][0],
            asks[0][0],
            own_price=Decimal("927.86"),
            own_remaining_amount=Decimal("100"),
        )
        self.assertEqual(target, Decimal("927.86"))

        without_ghost = [
            (Decimal("927.86"), Decimal("100")),
            (Decimal("927.85"), Decimal("29.64")),
        ]
        bot.calculate_strategy_price(
            "buy",
            without_ghost,
            asks,
            without_ghost[0][0],
            asks[0][0],
            own_price=Decimal("927.86"),
            own_remaining_amount=Decimal("100"),
        )

        external_returned = [
            (Decimal("931.13"), Decimal("10")),
            (Decimal("927.86"), Decimal("100")),
        ]
        target = bot.calculate_strategy_price(
            "buy",
            external_returned,
            asks,
            external_returned[0][0],
            asks[0][0],
            own_price=Decimal("927.86"),
            own_remaining_amount=Decimal("100"),
        )
        self.assertEqual(target, Decimal("931.14"))

    def test_canceled_level_expires_if_price_never_disappears(self):
        bot = make_bot()
        bot._remember_canceled_level(
            "buy", "order-1", Decimal("931.13"), Decimal("176.61")
        )
        bot._canceled_level_ttl = 0
        bids = [
            (Decimal("931.13"), Decimal("176.61")),
            (Decimal("927.86"), Decimal("100")),
        ]
        asks = [(Decimal("940.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("927.86"),
            own_remaining_amount=Decimal("100"),
        )

        self.assertEqual(target, Decimal("931.14"))

    def test_canceled_level_record_is_idempotent_by_order_id(self):
        bot = make_bot()

        for _ in range(2):
            bot._remember_canceled_level(
                "buy", "order-1", Decimal("931.13"), Decimal("176.61")
            )

        records = bot._recently_canceled_orders["buy"]
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records["order-1"].remaining_amount, Decimal("176.61")
        )

    def test_canceled_terminal_retry_does_not_count_order_twice(self):
        bot = make_bot()
        bot._remember_canceled_level(
            "buy", "order-1", Decimal("931.13"), Decimal("176.61")
        )
        bids = [
            (Decimal("931.13"), Decimal("176.61")),
            (Decimal("927.85"), Decimal("100")),
        ]
        asks = [(Decimal("940.00"), Decimal("100"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("931.13"),
            own_remaining_amount=Decimal("176.61"),
            own_order_id="order-1",
        )

        self.assertEqual(target, Decimal("927.86"))

    def test_terminal_fill_is_accounted_once_when_retried(self):
        bot = make_bot()

        first = bot._account_terminal_fill_once(
            "buy", "order-1", Decimal("1.5"), Decimal("1400")
        )
        repeated = bot._account_terminal_fill_once(
            "buy", "order-1", Decimal("1.5"), Decimal("1400")
        )

        self.assertTrue(first)
        self.assertFalse(repeated)
        self.assertEqual(bot._total_crypto_received, Decimal("1.5"))
        self.assertEqual(bot._total_clp_executed, Decimal("1400"))

    def test_depth_strategy_is_unchanged_by_own_order_context(self):
        bot = make_bot(strategy="depth")
        bids = [
            (Decimal("100"), Decimal("1")),
            (Decimal("90"), Decimal("9")),
        ]
        asks = [(Decimal("110"), Decimal("10"))]

        target = bot.calculate_strategy_price(
            "buy",
            bids,
            asks,
            bids[0][0],
            asks[0][0],
            own_price=Decimal("100"),
            own_remaining_amount=Decimal("1"),
        )

        self.assertEqual(target, Decimal("90.00"))


class TestTopStrategyRepricingLoop(unittest.TestCase):
    def make_repricing_bot(self, client: RepricingClient) -> TradingBot:
        bot = make_bot()
        bot.client = client
        bot.interval = 0
        bot._min_action_interval = 0
        bot._start_realtime = lambda: None  # type: ignore[method-assign]
        return bot

    def test_buy_reprices_down_from_own_best_bid(self):
        client = RepricingClient("buy")
        bot = self.make_repricing_bot(client)

        with redirect_stdout(io.StringIO()):
            bot.execute_buy_order(Decimal("1000"))

        self.assertEqual(
            [call["limit"] for call in client.create_calls],
            ["931.13", "927.86"],
        )
        self.assertEqual(client.cancel_calls, ["order-1"])

    def test_sell_reprices_up_from_own_best_ask(self):
        client = RepricingClient("sell")
        bot = self.make_repricing_bot(client)

        with redirect_stdout(io.StringIO()):
            bot.execute_sell_order(Decimal("10"))

        self.assertEqual(
            [call["limit"] for call in client.create_calls],
            ["927.85", "931.12"],
        )
        self.assertEqual(client.cancel_calls, ["order-1"])


class TestRealtimeSnapshotFallback(unittest.TestCase):
    def test_pre_sync_delta_triggers_immediate_rest_snapshot(self):
        client = RepricingClient("buy")
        realtime = RealtimeClient("usdc-clp")
        realtime.book_state.apply_change("bid", "930.00", "1")
        bot = make_bot()
        bot.client = client
        bot._realtime = realtime
        bot._last_sanity_ts = time.time()

        bot._refresh_realtime_book_if_needed()

        self.assertEqual(client.book_reads, 1)
        self.assertFalse(realtime.book_state.needs_snapshot())
        self.assertEqual(
            realtime.book_state.get_best(),
            (Decimal("931.12"), Decimal("940.00")),
        )

    def test_newer_delta_keeps_resync_request_during_rest_fetch(self):
        realtime = RealtimeClient("usdc-clp")

        class RacingClient(RepricingClient):
            def get_order_book(inner_self, market_id: str) -> dict:
                realtime.book_state.apply_change("bid", "930.00", "1")
                return super().get_order_book(market_id)

        client = RacingClient("buy")
        realtime.book_state.reset()
        bot = make_bot()
        bot.client = client
        bot._realtime = realtime

        bot._refresh_realtime_book_if_needed()

        self.assertTrue(realtime.book_state.needs_snapshot())
        self.assertEqual(
            realtime.book_state.get_best(),
            (Decimal("931.12"), Decimal("940.00")),
        )

        realtime.book_state.apply_change("bid", "931.12", "1")
        bot._refresh_realtime_book_if_needed()
        bids, _ = realtime.book_state.get_snapshot()
        self.assertEqual(bids[Decimal("931.12")], Decimal("101"))
        self.assertEqual(client.book_reads, 1)

    def test_rejected_rest_read_returns_newer_ws_snapshot(self):
        realtime = RealtimeClient("usdc-clp")

        class NewerSnapshotClient(RepricingClient):
            def get_order_book(inner_self, market_id: str) -> dict:
                realtime.book_state.apply_snapshot(
                    [["935.00", "10"]], [["936.00", "12"]]
                )
                return super().get_order_book(market_id)

        bot = make_bot()
        bot.client = NewerSnapshotClient("buy")
        bot._realtime = realtime

        bids, asks = bot.get_order_book_levels()

        self.assertEqual(bids[0][0], Decimal("935.00"))
        self.assertEqual(asks[0][0], Decimal("936.00"))

    def test_book_read_respects_snapshot_retry_cooldown(self):
        class FailingClient:
            def __init__(self) -> None:
                self.book_reads = 0

            def get_order_book(self, _market_id: str) -> dict:
                self.book_reads += 1
                raise BudaAPIError("temporary failure")

        client = FailingClient()
        realtime = RealtimeClient("usdc-clp")
        realtime.book_state.reset()
        bot = make_bot()
        bot.client = client
        bot._realtime = realtime

        with redirect_stdout(io.StringIO()):
            bot._refresh_realtime_book_if_needed()
        with self.assertRaises(BudaAPIError):
            bot.get_order_book_levels()

        self.assertEqual(client.book_reads, 1)

    def test_delta_during_sanity_fetch_keeps_sanity_pending(self):
        realtime = RealtimeClient("usdc-clp")
        realtime.book_state.apply_snapshot(
            [["931.12", "100"]], [["940.00", "100"]]
        )

        class SanityRaceClient(RepricingClient):
            def get_order_book(inner_self, market_id: str) -> dict:
                realtime.book_state.apply_change("bid", "931.12", "1")
                return super().get_order_book(market_id)

        client = SanityRaceClient("buy")
        bot = make_bot()
        bot.client = client
        bot._realtime = realtime
        bot._last_sanity_ts = 123.0

        bot._refresh_realtime_book_if_needed()

        self.assertEqual(bot._last_sanity_ts, 123.0)
        self.assertEqual(client.book_reads, 1)
        self.assertLessEqual(
            bot._realtime_wait_timeout(), bot._snapshot_retry_interval
        )


if __name__ == "__main__":
    unittest.main()
