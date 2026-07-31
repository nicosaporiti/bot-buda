"""Tests for realtime order-book state."""

import json
import threading
import time
import unittest
from decimal import Decimal

from src.ws import OrderBookState, RealtimeClient


class TestOrderBookState(unittest.TestCase):
    def test_change_before_snapshot_is_ignored(self):
        state = OrderBookState()

        state.apply_change("bid", "100", "5")
        state.apply_change("ask", "101", "4")

        self.assertIsNone(state.get_best())
        self.assertEqual(state.get_snapshot(), ({}, {}))
        self.assertTrue(state.needs_snapshot())
        self.assertTrue(state.wait_for_top_change(0))

        started = time.monotonic()
        self.assertFalse(state.wait_ready(0.2))
        self.assertLess(time.monotonic() - started, 0.1)

        state.apply_snapshot([["100", "5"]], [["101", "4"]])
        self.assertFalse(state.needs_snapshot())
        self.assertTrue(state.wait_ready(0))

    def test_change_is_applied_as_volume_delta(self):
        state = OrderBookState()
        state.apply_snapshot([["100", "5"]], [["101", "4"]])

        state.apply_change("bid", "100", "-2")
        state.apply_change("ask", "101", "3")

        bids, asks = state.get_snapshot()
        self.assertEqual(bids[Decimal("100")], Decimal("3"))
        self.assertEqual(asks[Decimal("101")], Decimal("7"))

    def test_change_removes_level_when_volume_reaches_zero(self):
        state = OrderBookState()
        state.apply_snapshot([["100", "5"]], [["101", "4"]])

        state.apply_change("bid", "100", "-5")

        bids, _ = state.get_snapshot()
        self.assertNotIn(Decimal("100"), bids)

    def test_reset_requests_snapshot_and_wakes_monitor(self):
        state = OrderBookState()
        state.apply_snapshot([["100", "5"]], [["101", "4"]])
        self.assertTrue(state.wait_for_top_change(0))

        state.reset()

        self.assertEqual(state.get_snapshot(), ({}, {}))
        self.assertTrue(state.needs_snapshot())
        self.assertFalse(state.wait_ready(0))
        self.assertTrue(state.wait_for_top_change(0))

    def test_additional_pre_sync_deltas_do_not_repeat_wake_signal(self):
        state = OrderBookState()
        state.reset()
        self.assertTrue(state.wait_for_top_change(0))
        version = state.snapshot_version()

        state.apply_change("bid", "100", "1")

        self.assertFalse(state.wait_for_top_change(0))
        self.assertGreater(state.snapshot_version(), version)

    def test_blocked_ready_waiter_is_woken_by_pre_sync_delta(self):
        state = OrderBookState()
        started = threading.Event()
        result: list[bool] = []

        def wait_for_book() -> None:
            started.set()
            result.append(state.wait_ready(1))

        waiter = threading.Thread(target=wait_for_book)
        waiter.start()
        self.assertTrue(started.wait(0.2))
        time.sleep(0.01)

        state.apply_change("bid", "100", "5")

        waiter.join(0.2)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(result, [False])

    def test_stale_rest_snapshot_cannot_clear_newer_resync_request(self):
        state = OrderBookState()
        state.reset()
        version = state.snapshot_version()

        state.apply_change("bid", "100", "1")
        applied = state.apply_snapshot_if_current(
            version, [["100", "5"]], [["101", "4"]]
        )

        self.assertFalse(applied)
        self.assertTrue(state.needs_snapshot())
        self.assertIsNone(state.get_best())

    def test_stale_rest_snapshot_cannot_overwrite_newer_ws_snapshot(self):
        state = OrderBookState()
        version = state.snapshot_version()
        state.apply_snapshot([["100", "7"]], [["101", "4"]])

        applied = state.apply_snapshot_if_current(
            version, [["100", "5"]], [["101", "4"]]
        )

        self.assertFalse(applied)
        bids, _ = state.get_snapshot()
        self.assertEqual(bids[Decimal("100")], Decimal("7"))

    def test_stale_rest_can_seed_baseline_without_clearing_resync(self):
        state = OrderBookState()
        state.reset()
        version = state.snapshot_version()
        state.apply_change("bid", "100", "1")
        self.assertFalse(
            state.apply_snapshot_if_current(
                version, [["100", "5"]], [["101", "4"]]
            )
        )

        seeded = state.seed_snapshot_if_unready(
            [["100", "5"]], [["101", "4"]]
        )
        state.apply_change("bid", "100", "1")

        self.assertTrue(seeded)
        self.assertTrue(state.needs_snapshot())
        bids, _ = state.get_snapshot()
        self.assertEqual(bids[Decimal("100")], Decimal("6"))


class TestRealtimeBookMessages(unittest.TestCase):
    def test_reads_official_top_level_book_sync_payload(self):
        realtime = RealtimeClient("usdc-clp")
        payload = {
            "ev": "book-sync",
            "order_book": {
                "bids": [["927.85", "10"]],
                "asks": [["931.13", "12"]],
            },
        }

        realtime._on_book(None, json.dumps(payload))

        self.assertEqual(
            realtime.book_state.get_best(),
            (Decimal("927.85"), Decimal("931.13")),
        )

    def test_reads_official_book_change_as_delta(self):
        realtime = RealtimeClient("usdc-clp")
        realtime.book_state.apply_snapshot(
            [["927.85", "10"]], [["931.13", "12"]]
        )
        payload = {
            "ev": "book-changed",
            "change": ["bids", "927.85", "-3"],
        }

        realtime._on_book(None, json.dumps(payload))

        bids, _ = realtime.book_state.get_snapshot()
        self.assertEqual(bids[Decimal("927.85")], Decimal("7"))

    def test_legacy_data_level_is_treated_as_absolute(self):
        realtime = RealtimeClient("usdc-clp")
        realtime.book_state.apply_snapshot(
            [["927.85", "10"]], [["931.13", "12"]]
        )
        payload = {
            "ev": "book-changed",
            "data": {"bids": [["927.85", "7"]]},
        }

        realtime._on_book(None, json.dumps(payload))

        bids, _ = realtime.book_state.get_snapshot()
        self.assertEqual(bids[Decimal("927.85")], Decimal("7"))


if __name__ == "__main__":
    unittest.main()
