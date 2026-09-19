"""Minimal synthetic regressions for market execution; no live orders."""
import io
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from src.api import BudaClient, BudaAPIError
from src.main import create_parser
from tests.test_bot import make_bot


class MarketTests(unittest.TestCase):
    def bot(self, state="traded"):
        bot = make_bot(strategy="market")
        bot.client = Mock()
        bot.client.get_balance.return_value = {"available_amount": ["100000"]}
        bot.client.get_order_book.return_value = {
            "asks": [["100", "1"], ["200", "10"]], "bids": [],
        }
        bot.client.create_market_order.return_value = {
            "id": "one", "state": state, "limit": None,
            "traded_amount": ["2"], "total_exchanged": ["300"],
        }
        bot.client.create_reserved_price_order.return_value = {
            "id": "quote-one", "state": "quoted",
            "base_amount": ["2", "USDC"],
            "quote_amount": ["300", "CLP"],
            "fee": ["3", "CLP"],
            "quotation_incomplete": False,
            "trade_order_id": None,
        }
        bot.client.confirm_reserved_price_order.return_value = {
            **bot.client.create_reserved_price_order.return_value,
            "state": "confirmed",
            "trade_order_id": "one",
        }
        return bot

    def test_buy_uses_reserved_quote_budget_instead_of_unbounded_base_order(self):
        bot = self.bot()
        with redirect_stdout(io.StringIO()):
            bot.execute_buy_order(Decimal("300"))
        bot.client.create_reserved_price_order.assert_called_once_with(
            "usdc-clp", "bid_given_value", "300"
        )
        bot.client.confirm_reserved_price_order.assert_called_once_with("quote-one")
        bot.client.create_market_order.assert_not_called()
        bot.client.get_order_book.assert_not_called()
        self.assertEqual(bot._total_clp_executed, Decimal("300"))
        self.assertEqual(bot._total_crypto_received, Decimal("2"))

    def test_buy_rejects_incomplete_or_over_budget_reserved_quote(self):
        for field, value in (("quotation_incomplete", True), ("quote_amount", ["301", "CLP"])):
            with self.subTest(field=field), redirect_stdout(io.StringIO()):
                bot = self.bot()
                bot.client.create_reserved_price_order.return_value[field] = value
                with self.assertRaises(BudaAPIError):
                    bot.execute_buy_order(Decimal("300"))
                bot.client.confirm_reserved_price_order.assert_not_called()
                bot.client.create_market_order.assert_not_called()

    def test_sell_partial_terminal_does_not_resubmit(self):
        bot = self.bot("canceled_and_traded")
        with redirect_stdout(io.StringIO()):
            bot.execute_sell_order(Decimal("3"))
        bot.client.create_market_order.assert_called_once_with("usdc-clp", "Ask", "3.000000")
        self.assertEqual(bot._total_crypto_executed, Decimal("2"))
        bot.client.get_order_book.assert_not_called()

    def test_pending_order_is_polled_until_terminal(self):
        bot = self.bot("received")
        bot.client.get_order.return_value = dict(bot.client.create_market_order.return_value, state="traded")
        with patch("src.bot.time.sleep"), redirect_stdout(io.StringIO()):
            bot.execute_sell_order(Decimal("2"))
        bot.client.get_order.assert_called_once_with("one")
        self.assertIsNone(bot._current_order_id)

    def test_market_monitor_stops_after_consecutive_poll_failures(self):
        bot = self.bot("received")
        bot.client.get_order.side_effect = BudaAPIError("permanent failure")

        with patch("src.bot.time.sleep"), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(BudaAPIError, "stopped after 3 consecutive"):
                bot.execute_sell_order(Decimal("2"))

        self.assertEqual(bot.client.get_order.call_count, 3)
        self.assertEqual(bot._current_order_id, "one")
        bot.client.create_market_order.assert_called_once()

    def test_successful_poll_resets_consecutive_error_count(self):
        bot = self.bot("received")
        received = dict(bot.client.create_market_order.return_value)
        traded = dict(received, state="traded")
        bot.client.get_order.side_effect = [
            BudaAPIError("temporary failure"),
            received,
            BudaAPIError("temporary failure"),
            traded,
        ]

        with patch("src.bot.time.sleep"), redirect_stdout(io.StringIO()):
            bot.execute_sell_order(Decimal("2"))

        self.assertEqual(bot.client.get_order.call_count, 4)
        self.assertIsNone(bot._current_order_id)

    def test_reserved_monitor_stops_after_consecutive_poll_failures(self):
        bot = self.bot()
        bot.client.confirm_reserved_price_order.return_value["state"] = "prepared"
        bot.client.get_reserved_price_order.side_effect = BudaAPIError(
            "permanent failure"
        )

        with patch("src.bot.time.sleep"), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(BudaAPIError, "stopped after 3 consecutive"):
                bot.execute_buy_order(Decimal("300"))

        self.assertEqual(bot.client.get_reserved_price_order.call_count, 3)
        self.assertEqual(bot._current_reserved_order_id, "quote-one")
        bot.client.confirm_reserved_price_order.assert_called_once_with("quote-one")

    def test_dry_run_never_submits_or_polls(self):
        for side, amount in [("buy", "300"), ("sell", "2")]:
            with self.subTest(side=side), redirect_stdout(io.StringIO()):
                bot = self.bot()
                bot.dry_run = True
                getattr(bot, f"execute_{side}_order")(Decimal(amount))
                bot.client.create_market_order.assert_not_called()
                bot.client.create_reserved_price_order.assert_not_called()
                bot.client.get_order.assert_not_called()

    def test_invalid_size_or_liquidity_never_submits(self):
        cases = [
            ("buy", "50", {"base_amount": ["0.5", "USDC"]}),
            ("buy", "5000", {"quotation_incomplete": True}),
            ("sell", "0.1", {}),
            ("sell", "NaN", {}),
        ]
        for side, amount, quote_override in cases:
            with self.subTest(side=side, amount=amount), redirect_stdout(io.StringIO()):
                bot = self.bot()
                bot.client.create_reserved_price_order.return_value.update(quote_override)
                with self.assertRaises(BudaAPIError):
                    getattr(bot, f"execute_{side}_order")(Decimal(amount))
                bot.client.create_market_order.assert_not_called()
                bot.client.confirm_reserved_price_order.assert_not_called()

    def test_cli_accepts_market_for_both_sides(self):
        for side in ("buy", "sell"):
            args = create_parser().parse_args([side, "btc", "1", "--strategy", "market"])
            self.assertEqual(args.strategy, "market")

    def test_api_payload_has_no_limit_and_disables_transport_retry(self):
        client = BudaClient(Mock())
        client._make_request = Mock(return_value={"order": {"id": "one"}})
        self.assertEqual(client.create_market_order("BTC-CLP", "Ask", "1"), {"id": "one"})
        client._make_request.assert_called_once_with(
            "POST", "/markets/btc-clp/orders",
            body={"type": "Ask", "price_type": "market", "amount": "1"},
            retry_network_errors=False,
        )

    def test_reserved_price_payloads_disable_transport_retry(self):
        client = BudaClient(Mock())
        client._make_request = Mock(
            side_effect=[
                {"reserved_price_order": {"id": "quote-one"}},
                {"reserved_price_order": {"id": "quote-one", "state": "prepared"}},
            ]
        )

        client.create_reserved_price_order("BTC-CLP", "bid_given_value", "100000")
        client.confirm_reserved_price_order("quote-one")

        self.assertEqual(
            client._make_request.call_args_list,
            [
                unittest.mock.call(
                    "POST",
                    "/reserved_price_orders",
                    body={
                        "market_name": "btc-clp",
                        "amount": "100000",
                        "quotation_type": "bid_given_value",
                        "payment_type": "immediate",
                    },
                    retry_network_errors=False,
                ),
                unittest.mock.call(
                    "PUT",
                    "/reserved_price_orders/quote-one",
                    body={"state": "commited"},
                    retry_network_errors=False,
                ),
            ],
        )

    def test_market_transport_failure_is_not_retried(self):
        for error in (requests.exceptions.Timeout(), requests.exceptions.ConnectionError()):
            with self.subTest(error=type(error)):
                client = BudaClient(Mock())
                client.session.request = Mock(side_effect=error)
                with patch("src.api.get_auth_headers", return_value={}), self.assertRaises(BudaAPIError):
                    client.create_market_order("btc-clp", "Bid", "1")
                self.assertEqual(client.session.request.call_count, 1)

    def test_successful_invalid_response_becomes_ambiguous_api_error(self):
        for content in (b"{", b"[]"):
            with self.subTest(content=content):
                response = requests.Response()
                response.status_code = 201
                response._content = content
                response.encoding = "utf-8"
                client = BudaClient(Mock())
                client.session.request = Mock(return_value=response)

                with patch("src.api.get_auth_headers", return_value={}):
                    with self.assertRaisesRegex(BudaAPIError, "result is ambiguous"):
                        client.create_market_order("btc-clp", "Ask", "1")

                self.assertEqual(client.session.request.call_count, 1)
