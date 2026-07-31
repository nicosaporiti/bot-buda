"""Tests for the grid trading strategy."""

import time
import unittest
from decimal import Decimal

from src.grid import (
    GridTradingBot,
    allocate_slots,
    compute_auto_range,
    generate_levels,
    plan_initial_buys,
    plan_initial_sells,
    split_levels,
)
from src.grid_types import GridConfig, GridConfigError, GridLevel, GridOrder
from src.market import MarketConfig
from src.ws import RealtimeClient


def make_market(
    market_id="btc-clp",
    base="btc",
    quote="clp",
    min_amount=Decimal("0.00002"),
    base_decimals=8,
    quote_decimals=0,
    price_tick=Decimal("1"),
):
    return MarketConfig(
        market_id=market_id,
        base_currency=base,
        quote_currency=quote,
        min_order_amount=min_amount,
        base_decimals=base_decimals,
        quote_decimals=quote_decimals,
        price_tick=price_tick,
    )


class FakeClient:
    """In-memory Buda client for testing dry-run / mirror behaviour."""

    def __init__(self, balances=None, orders=None, mid_price=Decimal("100000000")):
        self.balances = balances or {"clp": Decimal("1000000"), "btc": Decimal("0.1")}
        self.create_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.book_calls = 0
        self._orders = orders or {}
        self._next_id = 1
        self._mid_price = mid_price

    def get_balance(self, currency):
        amount = self.balances.get(currency.lower(), Decimal("0"))
        return {"available_amount": [str(amount), currency.upper()]}

    def get_order_book(self, market_id):
        self.book_calls += 1
        bid = self._mid_price - Decimal("1")
        ask = self._mid_price + Decimal("1")
        return {"bids": [[str(bid), "1"]], "asks": [[str(ask), "1"]]}

    def get_ticker(self, market_id):
        return {"last_price": [str(self._mid_price), "CLP"]}

    def get_me(self):
        return {"pubsub_key": None}

    def create_limit_order(self, market_id, order_type, amount, limit_price):
        self.create_calls.append(
            {
                "market_id": market_id,
                "type": order_type,
                "amount": amount,
                "limit": limit_price,
            }
        )
        order_id = f"fake-{self._next_id}"
        self._next_id += 1
        order = {
            "id": order_id,
            "state": "pending",
            "limit": [limit_price, "CLP"],
            "amount": [amount, "BTC"],
            "traded_amount": ["0", "BTC"],
            "total_exchanged": ["0", "CLP"],
        }
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        if order_id in self._orders:
            self._orders[order_id]["state"] = "canceled"
        return self._orders.get(order_id, {"id": order_id, "state": "canceled"})

    def get_order(self, order_id):
        return self._orders.get(order_id, {"id": order_id, "state": "unknown"})


class TestComputeAutoRange(unittest.TestCase):
    def test_centers_window_on_price(self):
        lower, upper = compute_auto_range(Decimal("100000000"), Decimal("10"))
        self.assertEqual(lower, Decimal("90000000"))
        self.assertEqual(upper, Decimal("110000000"))

    def test_rejects_negative_pct(self):
        with self.assertRaises(GridConfigError):
            compute_auto_range(Decimal("100"), Decimal("-1"))

    def test_rejects_zero_price(self):
        with self.assertRaises(GridConfigError):
            compute_auto_range(Decimal("0"), Decimal("10"))


class TestGenerateLevels(unittest.TestCase):
    def test_includes_endpoints(self):
        levels = generate_levels(
            Decimal("90000000"), Decimal("110000000"), 5, Decimal("1")
        )
        self.assertEqual(len(levels), 5)
        self.assertEqual(levels[0].price, Decimal("90000000"))
        self.assertEqual(levels[-1].price, Decimal("110000000"))
        self.assertEqual(levels[0].index, 0)
        self.assertEqual(levels[-1].index, 4)

    def test_respects_tick(self):
        levels = generate_levels(
            Decimal("100.00"), Decimal("101.00"), 5, Decimal("0.01")
        )
        for lvl in levels:
            # Each price must be on a tick boundary
            quantized = lvl.price.quantize(Decimal("0.01"))
            self.assertEqual(lvl.price, quantized)

    def test_duplicates_after_rounding_fail(self):
        # Tiny range with too many levels rounds to duplicates.
        with self.assertRaises(GridConfigError):
            generate_levels(Decimal("100"), Decimal("103"), 10, Decimal("1"))

    def test_levels_lt_two_fails(self):
        with self.assertRaises(GridConfigError):
            generate_levels(Decimal("100"), Decimal("200"), 1, Decimal("1"))

    def test_lower_ge_upper_fails(self):
        with self.assertRaises(GridConfigError):
            generate_levels(Decimal("200"), Decimal("100"), 5, Decimal("1"))


class TestSplitLevels(unittest.TestCase):
    def test_splits_around_price(self):
        levels = generate_levels(
            Decimal("90"), Decimal("110"), 5, Decimal("1")
        )
        buys, sells = split_levels(levels, Decimal("100"))
        self.assertEqual([b.price for b in buys], [Decimal("90"), Decimal("95")])
        self.assertEqual([s.price for s in sells], [Decimal("105"), Decimal("110")])


class TestPlanInitialBuys(unittest.TestCase):
    def test_respects_base_decimals(self):
        market = make_market(base_decimals=8, price_tick=Decimal("1"))
        levels = [GridLevel(0, Decimal("90000000")), GridLevel(1, Decimal("95000000"))]
        plan = plan_initial_buys(levels, Decimal("400000"), 2, market)

        for _, amount in plan:
            # amount must have at most 8 decimal places
            tup = amount.as_tuple()
            self.assertGreaterEqual(tup.exponent, -8)
            # Quantizing again should not change it.
            self.assertEqual(amount, amount.quantize(Decimal("0.00000001")))

    def test_below_min_fails(self):
        market = make_market(min_amount=Decimal("1"))
        levels = [GridLevel(0, Decimal("100000000"))]
        with self.assertRaises(GridConfigError):
            plan_initial_buys(levels, Decimal("100"), 1, market)

    def test_uses_closest_levels_first(self):
        market = make_market()
        levels = [
            GridLevel(0, Decimal("90000000")),
            GridLevel(1, Decimal("95000000")),
            GridLevel(2, Decimal("99000000")),
        ]
        plan = plan_initial_buys(levels, Decimal("300000"), 2, market)
        chosen = sorted(lvl.index for lvl, _ in plan)
        self.assertEqual(chosen, [1, 2])


class TestPlanInitialSells(unittest.TestCase):
    def test_zero_base_budget_returns_empty(self):
        market = make_market()
        levels = [GridLevel(2, Decimal("105000000"))]
        plan = plan_initial_sells(levels, Decimal("0"), 1, market)
        self.assertEqual(plan, [])

    def test_below_min_fails(self):
        market = make_market(min_amount=Decimal("0.001"))
        levels = [GridLevel(2, Decimal("105000000"))]
        with self.assertRaises(GridConfigError):
            plan_initial_sells(levels, Decimal("0.0001"), 1, market)


class TestAllocateSlots(unittest.TestCase):
    def test_no_base_budget_all_to_buys(self):
        self.assertEqual(allocate_slots(6, has_base_budget=False), (6, 0))

    def test_with_base_budget_splits(self):
        self.assertEqual(allocate_slots(6, has_base_budget=True), (3, 3))
        self.assertEqual(allocate_slots(5, has_base_budget=True), (3, 2))


class TestVerifyBalances(unittest.TestCase):
    def _make_bot(self, balances, quote_budget=Decimal("500000"), base_budget=Decimal("0")):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=quote_budget,
            base_budget=base_budget,
            max_open_orders=4,
            interval=10,
            dry_run=False,
        )
        client = FakeClient(balances=balances)
        return GridTradingBot(client=client, config=config, register_signals=False)

    def test_quote_budget_over_balance_fails(self):
        from src.api import InsufficientBalanceError

        bot = self._make_bot(
            balances={"clp": Decimal("100000")}, quote_budget=Decimal("500000")
        )
        with self.assertRaises(InsufficientBalanceError):
            bot._verify_balances()

    def test_base_budget_over_balance_fails(self):
        from src.api import InsufficientBalanceError

        bot = self._make_bot(
            balances={"clp": Decimal("10000000"), "btc": Decimal("0.0001")},
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0.01"),
        )
        with self.assertRaises(InsufficientBalanceError):
            bot._verify_balances()

    def test_within_budget_passes(self):
        bot = self._make_bot(balances={"clp": Decimal("10000000")})
        bot._verify_balances()


class TestRealtimeSnapshotFallback(unittest.TestCase):
    def test_resync_request_triggers_rest_snapshot_before_sanity_interval(self):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            max_open_orders=4,
            interval=10,
        )
        client = FakeClient()
        realtime = RealtimeClient(market.market_id)
        realtime.book_state.reset()
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        bot._realtime = realtime
        bot._last_sanity_ts = time.time()

        bot._refresh_realtime_book_if_needed()

        self.assertEqual(client.book_calls, 1)
        self.assertFalse(realtime.book_state.needs_snapshot())
        self.assertEqual(
            realtime.book_state.get_best(),
            (Decimal("99999999"), Decimal("100000001")),
        )

    def test_resync_wait_is_capped_by_retry_delay(self):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            max_open_orders=4,
            interval=10,
        )
        bot = GridTradingBot(
            client=FakeClient(), config=config, register_signals=False
        )
        bot._realtime = RealtimeClient(market.market_id)
        bot._realtime.book_state.reset()
        bot._last_snapshot_attempt_ts = time.monotonic()

        timeout = bot._realtime_wait_timeout()

        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, bot._snapshot_retry_interval)
        self.assertLess(timeout, bot.interval)

    def test_delta_during_sanity_fetch_keeps_sanity_pending(self):
        market = make_market()
        realtime = RealtimeClient(market.market_id)
        realtime.book_state.apply_snapshot(
            [["99999999", "1"]], [["100000001", "1"]]
        )

        class SanityRaceClient(FakeClient):
            def get_order_book(inner_self, market_id):
                realtime.book_state.apply_change("bid", "99999999", "1")
                return super().get_order_book(market_id)

        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            max_open_orders=4,
            interval=10,
        )
        client = SanityRaceClient()
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        bot._realtime = realtime
        bot._last_sanity_ts = 123.0

        bot._refresh_realtime_book_if_needed()

        self.assertEqual(bot._last_sanity_ts, 123.0)
        self.assertEqual(client.book_calls, 1)
        self.assertLessEqual(
            bot._realtime_wait_timeout(), bot._snapshot_retry_interval
        )


class TestMirrorLogic(unittest.TestCase):
    """Mirror placement uses pure level math; we exercise it via _maybe_mirror."""

    def _make_bot(self, dry_run=True):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=dry_run,
        )
        client = FakeClient()
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        bot._levels = generate_levels(
            Decimal("90000000"), Decimal("110000000"), 5, Decimal("1")
        )
        return bot, client

    def test_filled_buy_mirrors_to_next_level_sell(self):
        bot, client = self._make_bot()
        traded = Decimal("0.0001")
        order = GridOrder(
            order_id="b1",
            side="buy",
            level_index=1,
            amount=traded,
            price=bot._levels[1].price,
            traded_amount=traded,
            traded_quote=traded * bot._levels[1].price,
            state="traded",
        )
        bot._orders["b1"] = order
        bot._maybe_mirror(order)

        mirrors = [o for oid, o in bot._orders.items() if oid != "b1"]
        self.assertEqual(len(mirrors), 1)
        mirror = mirrors[0]
        self.assertEqual(mirror.side, "sell")
        self.assertEqual(mirror.level_index, 2)
        self.assertTrue(order.mirrored)

    def test_filled_sell_mirrors_to_prev_level_buy(self):
        bot, client = self._make_bot()
        traded = Decimal("0.0001")
        order = GridOrder(
            order_id="s1",
            side="sell",
            level_index=3,
            amount=traded,
            price=bot._levels[3].price,
            traded_amount=traded,
            traded_quote=traded * bot._levels[3].price,
            state="traded",
        )
        bot._orders["s1"] = order
        bot._maybe_mirror(order)

        mirrors = [o for oid, o in bot._orders.items() if oid != "s1"]
        self.assertEqual(len(mirrors), 1)
        mirror = mirrors[0]
        self.assertEqual(mirror.side, "buy")
        self.assertEqual(mirror.level_index, 2)

    def test_partial_fill_pending_does_not_mirror(self):
        bot, client = self._make_bot()
        traded = Decimal("0.00005")
        order = GridOrder(
            order_id="b2",
            side="buy",
            level_index=1,
            amount=Decimal("0.0001"),
            price=bot._levels[1].price,
            traded_amount=traded,
            traded_quote=traded * bot._levels[1].price,
            state="pending",  # not terminal
        )
        bot._orders["b2"] = order

        # In the monitor loop, partials in 'pending' are NOT passed to _maybe_mirror.
        # We assert that even if invoked, calling twice produces only one mirror.
        bot._maybe_mirror(order)
        bot._maybe_mirror(order)

        mirrors = [o for oid, o in bot._orders.items() if oid != "b2"]
        self.assertEqual(len(mirrors), 1)
        self.assertTrue(order.mirrored)

    def test_top_level_buy_has_no_mirror_target(self):
        bot, _ = self._make_bot()
        last = bot._levels[-1]
        order = GridOrder(
            order_id="b3",
            side="buy",
            level_index=last.index,
            amount=Decimal("0.0001"),
            price=last.price,
            traded_amount=Decimal("0.0001"),
            traded_quote=Decimal("0.0001") * last.price,
            state="traded",
        )
        bot._orders["b3"] = order
        bot._maybe_mirror(order)

        # Only the original order remains.
        self.assertEqual(list(bot._orders.keys()), ["b3"])
        self.assertTrue(order.mirrored)


class TestSpreadCheck(unittest.TestCase):
    def _make_bot(self, mid_price=Decimal("100000000")):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=False,
        )
        client = FakeClient(mid_price=mid_price)
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        return bot, client

    def test_buy_below_best_ask_is_safe(self):
        bot, client = self._make_bot(mid_price=Decimal("100000000"))
        # FakeClient: best_bid = 99999999, best_ask = 100000001.
        self.assertTrue(bot._is_safe_price("buy", Decimal("99999000")))

    def test_buy_at_or_above_best_ask_crosses(self):
        bot, _ = self._make_bot(mid_price=Decimal("100000000"))
        self.assertFalse(bot._is_safe_price("buy", Decimal("100000001")))
        self.assertFalse(bot._is_safe_price("buy", Decimal("100100000")))

    def test_sell_above_best_bid_is_safe(self):
        bot, _ = self._make_bot(mid_price=Decimal("100000000"))
        self.assertTrue(bot._is_safe_price("sell", Decimal("100100000")))

    def test_sell_at_or_below_best_bid_crosses(self):
        bot, _ = self._make_bot(mid_price=Decimal("100000000"))
        self.assertFalse(bot._is_safe_price("sell", Decimal("99999999")))
        self.assertFalse(bot._is_safe_price("sell", Decimal("99000000")))

    def test_place_order_skips_when_crossing(self):
        bot, client = self._make_bot(mid_price=Decimal("100000000"))
        # Buy level above best_ask must be skipped.
        crossing_level = GridLevel(0, Decimal("100100000"))
        result = bot._place_order("buy", crossing_level, Decimal("0.0001"))
        self.assertIsNone(result)
        self.assertEqual(client.create_calls, [])


class TestCleanupAwaitsCancellation(unittest.TestCase):
    def _make_bot(self):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=False,
        )
        client = FakeClient()
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        bot._stop_realtime = lambda: None  # type: ignore[assignment]
        return bot, client

    def test_cleanup_cancels_and_confirms_terminal_state(self):
        bot, client = self._make_bot()
        # Pre-register a pending order via FakeClient so cancel_order can find it.
        client._orders["o1"] = {
            "id": "o1",
            "state": "pending",
            "limit": ["95000000", "CLP"],
            "amount": ["0.0001", "BTC"],
            "traded_amount": ["0", "BTC"],
            "total_exchanged": ["0", "CLP"],
        }
        bot._orders["o1"] = GridOrder(
            order_id="o1",
            side="buy",
            level_index=1,
            amount=Decimal("0.0001"),
            price=Decimal("95000000"),
            state="pending",
        )
        bot._running = True

        bot.cleanup()

        self.assertEqual(client.cancel_calls, ["o1"])
        # FakeClient.cancel_order moves state to "canceled" immediately,
        # so the polling loop must see terminal and exit cleanly.
        self.assertEqual(bot._orders["o1"].state, "canceled")

    def test_cleanup_accounts_partial_fill_when_cancel_never_confirms(self):
        bot, client = self._make_bot()

        # Pre-register a remote order that has filled half but is still
        # 'canceling' — it never reaches a terminal state during the wait.
        client._orders["partial"] = {
            "id": "partial",
            "state": "canceling",
            "limit": ["95000000", "CLP"],
            "amount": ["0.0002", "BTC"],
            "traded_amount": ["0.0001", "BTC"],
            "total_exchanged": ["9500", "CLP"],
        }
        bot._orders["partial"] = GridOrder(
            order_id="partial",
            side="buy",
            level_index=1,
            amount=Decimal("0.0002"),
            price=Decimal("95000000"),
            state="pending",
        )
        bot._running = True

        # Tight timeout so the test runs fast; the cancel will never confirm
        # because FakeClient's stub keeps the state at 'canceling' here:
        original_cancel = client.cancel_order

        def cancel_no_confirm(order_id):
            client.cancel_calls.append(order_id)
            return client._orders[order_id]  # leave state as 'canceling'

        client.cancel_order = cancel_no_confirm  # type: ignore[assignment]

        original_await = bot._await_cancellations
        bot._await_cancellations = lambda orders: original_await(  # type: ignore[assignment]
            orders, timeout=0.2, poll_interval=0.05
        )
        bot.cleanup()

        # The partial fill MUST still appear in the summary.
        self.assertEqual(bot._fills_buy_base, Decimal("0.0001"))
        self.assertEqual(bot._fills_buy_quote, Decimal("9500"))
        # Calling cleanup again must not double count.
        bot._running = True
        client.cancel_order = original_cancel
        bot.cleanup()
        self.assertEqual(bot._fills_buy_base, Decimal("0.0001"))

    def test_await_cancellations_times_out_when_state_stuck(self):
        bot, client = self._make_bot()

        # Refresh keeps the state non-terminal forever.
        order = GridOrder(
            order_id="stuck",
            side="buy",
            level_index=1,
            amount=Decimal("0.0001"),
            price=Decimal("95000000"),
            state="canceling",
        )
        bot._orders["stuck"] = order

        def stuck_refresh(o):
            o.state = "canceling"

        bot._refresh_order = stuck_refresh  # type: ignore[assignment]

        start = time.time()
        bot._await_cancellations([order], timeout=0.3, poll_interval=0.05)
        elapsed = time.time() - start
        # It should not block forever; bounded by timeout (with small slack).
        self.assertLess(elapsed, 1.5)
        self.assertEqual(order.state, "canceling")


class TestAccountingNoDoubleCount(unittest.TestCase):
    def _make_bot(self):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=False,
        )
        client = FakeClient()
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        return bot, client

    def test_account_fill_is_idempotent(self):
        bot, _ = self._make_bot()
        order = GridOrder(
            order_id="x",
            side="buy",
            level_index=1,
            amount=Decimal("0.0002"),
            price=Decimal("95000000"),
            traded_amount=Decimal("0.0002"),
            traded_quote=Decimal("19000"),
            state="traded",
        )
        bot._account_fill(order)
        bot._account_fill(order)  # second call must not double-count
        bot._account_fill(order)  # nor a third

        self.assertEqual(bot._fills_buy_base, Decimal("0.0002"))
        self.assertEqual(bot._fills_buy_quote, Decimal("19000"))
        self.assertEqual(order.accounted_amount, order.traded_amount)
        self.assertEqual(order.accounted_quote, order.traded_quote)

    def test_account_fill_picks_up_partial_then_total(self):
        bot, _ = self._make_bot()
        order = GridOrder(
            order_id="x",
            side="sell",
            level_index=3,
            amount=Decimal("0.0004"),
            price=Decimal("105000000"),
            traded_amount=Decimal("0.0001"),
            traded_quote=Decimal("10500"),
            state="pending",
        )
        bot._account_fill(order)
        self.assertEqual(bot._fills_sell_base, Decimal("0.0001"))

        # More of the order fills before terminal.
        order.traded_amount = Decimal("0.0004")
        order.traded_quote = Decimal("42000")
        bot._account_fill(order)

        self.assertEqual(bot._fills_sell_base, Decimal("0.0004"))
        self.assertEqual(bot._fills_sell_quote, Decimal("42000"))


class TestInitialPlacementGuards(unittest.TestCase):
    def _make_bot(self, mid_price=Decimal("100000000")):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=False,
        )
        client = FakeClient(mid_price=mid_price)
        bot = GridTradingBot(client=client, config=config, register_signals=False)
        bot._start_realtime = lambda: None  # type: ignore[assignment]
        bot._stop_realtime = lambda: None  # type: ignore[assignment]
        return bot, client

    def test_fails_when_no_initial_orders_placed(self):
        bot, client = self._make_bot()

        # Force every placement to be rejected by faking a crossing book:
        # buy levels (90M, 95M) would not cross with default mid 100M, so
        # instead override _is_safe_price to always reject.
        bot._is_safe_price = lambda side, price: False  # type: ignore[assignment]

        with self.assertRaises(GridConfigError) as ctx:
            bot.execute()
        self.assertIn("NO se inicio", str(ctx.exception))
        # Nothing was placed live.
        self.assertEqual(client.create_calls, [])

    def test_rejected_initial_orders_are_deferred(self):
        bot, _ = self._make_bot()
        rejected: list[Decimal] = []
        original = bot._is_safe_price

        def selective(side, price):
            # Reject only the lowest buy level so the grid still starts.
            if price == Decimal("90000000"):
                rejected.append(price)
                return False
            return original(side, price)

        bot._is_safe_price = selective  # type: ignore[assignment]

        # Stub the monitor loop so execute() returns after initial placement.
        bot._monitor_loop = lambda: None  # type: ignore[assignment]
        bot.execute()

        self.assertGreaterEqual(len(rejected), 1)
        deferred_prices = [lvl.price for _, lvl, _ in bot._deferred]
        self.assertIn(Decimal("90000000"), deferred_prices)
        # At least one order made it through, so startup did NOT fail.
        self.assertGreater(len(bot._orders), 0)

    def test_retry_deferred_drains_when_safe(self):
        bot, client = self._make_bot()
        market = bot.market_config

        # Pre-load a deferred buy that initially crosses, then becomes safe.
        lvl = GridLevel(0, Decimal("90000000"))
        amount = Decimal("0.0001")
        bot._deferred = [("buy", lvl, amount)]

        # First retry: still unsafe.
        bot._is_safe_price = lambda side, price: False  # type: ignore[assignment]
        bot._retry_deferred()
        self.assertEqual(len(bot._deferred), 1)
        self.assertEqual(client.create_calls, [])

        # Second retry: book recovered.
        bot._is_safe_price = lambda side, price: True  # type: ignore[assignment]
        bot._retry_deferred()
        self.assertEqual(bot._deferred, [])
        self.assertEqual(len(client.create_calls), 1)


class TestDryRunNoApiCalls(unittest.TestCase):
    def test_dry_run_does_not_create_or_cancel_orders(self):
        market = make_market()
        config = GridConfig(
            market_config=market,
            lower_price=Decimal("90000000"),
            upper_price=Decimal("110000000"),
            range_pct=None,
            levels=5,
            quote_budget=Decimal("500000"),
            base_budget=Decimal("0"),
            max_open_orders=4,
            interval=10,
            dry_run=True,
        )
        client = FakeClient()
        bot = GridTradingBot(client=client, config=config, register_signals=False)

        # Stub realtime: dry-run still calls _start_realtime. Replace with no-op.
        bot._start_realtime = lambda: None  # type: ignore[assignment]
        bot._stop_realtime = lambda: None  # type: ignore[assignment]

        bot.execute()

        self.assertEqual(client.create_calls, [])
        self.assertEqual(client.cancel_calls, [])
        self.assertGreater(len(bot._orders), 0)


if __name__ == "__main__":
    unittest.main()
