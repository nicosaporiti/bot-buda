#!/usr/bin/env python3
"""CLI entry point for Buda.com trading bot."""

import argparse
import sys
from decimal import Decimal

from .api import BudaClient, AuthenticationError, BudaAPIError
from .bot import TradingBot
from .config import Config, ConfigError
from .utils import format_clp, print_status


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        description="Buda.com Trading Bot - Maintain best bid position",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main buy btc 100000        # Buy BTC with 100,000 CLP
  python -m src.main buy usdc 50000        # Buy USDC with 50,000 CLP
  python -m src.main buy btc 100000 --interval 60   # Check every 60 seconds
  python -m src.main buy btc 100000 --dry-run       # Simulate without trading
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Buy command
    buy_parser = subparsers.add_parser("buy", help="Place and maintain a buy order")
    buy_parser.add_argument(
        "currency",
        type=str,
        choices=["btc", "usdc", "BTC", "USDC"],
        help="Currency to buy (btc or usdc)"
    )
    buy_parser.add_argument(
        "amount",
        type=int,
        help="Amount of CLP to spend"
    )
    buy_parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=30,
        help="Monitoring interval in seconds (default: 30)"
    )
    buy_parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="Simulate without placing real orders"
    )

    # Balance command (for testing)
    balance_parser = subparsers.add_parser("balance", help="Check account balances")
    balance_parser.add_argument(
        "currency",
        type=str,
        nargs="?",
        default=None,
        help="Currency to check (default: all)"
    )

    # Orderbook command (for testing)
    orderbook_parser = subparsers.add_parser("orderbook", help="Show order book")
    orderbook_parser.add_argument(
        "market",
        type=str,
        nargs="?",
        default="btc-clp",
        help="Market to show (default: btc-clp)"
    )

    return parser


def cmd_buy(args, client: BudaClient) -> int:
    """Execute the buy command."""
    currency = args.currency.lower()
    clp_amount = Decimal(args.amount)

    if clp_amount <= 0:
        print_status("Amount must be positive", "ERROR")
        return 1

    print_status(f"Buda.com Trading Bot", "INFO")
    print_status(f"=" * 40, "INFO")
    print()

    bot = TradingBot(
        client=client,
        currency=currency,
        interval=args.interval,
        dry_run=args.dry_run
    )

    try:
        bot.execute_buy_order(clp_amount)
        return 0
    except BudaAPIError as e:
        print_status(f"Trading error: {e}", "ERROR")
        return 1


def cmd_balance(args, client: BudaClient) -> int:
    """Execute the balance command."""
    def _print_balance(balance: dict, currency: str) -> None:
        available = balance.get("available_amount", ["0", currency.upper()])
        frozen = balance.get("frozen_amount", ["0", currency.upper()])

        print(f"Balance for {currency.upper()}:")
        if isinstance(available, list):
            print(f"  Available: {available[0]} {available[1]}")
        else:
            print(f"  Available: {available}")
        if isinstance(frozen, list):
            print(f"  Frozen: {frozen[0]} {frozen[1]}")
        else:
            print(f"  Frozen: {frozen}")

    try:
        if args.currency:
            currency = args.currency.lower()
            balance = client.get_balance(currency)
            _print_balance(balance, currency)
        else:
            balances = client.get_balances()
            if not balances:
                print("No balances found.")
                return 0
            for balance in balances:
                currency = balance.get("id")
                if not currency and isinstance(balance.get("available_amount"), list):
                    currency = balance["available_amount"][1]
                currency = currency or "unknown"
                _print_balance(balance, currency)
                print()

        return 0
    except BudaAPIError as e:
        print_status(f"Error: {e}", "ERROR")
        return 1


def cmd_orderbook(args, client: BudaClient) -> int:
    """Execute the orderbook command."""
    market = args.market.lower()

    try:
        order_book = client.get_order_book(market)

        bids = order_book.get("bids", [])[:5]
        asks = order_book.get("asks", [])[:5]

        print(f"Order Book for {market.upper()}:")
        print()
        print("  ASKS (sell orders):")
        for ask in reversed(asks):
            price, amount = ask[0], ask[1]
            print(f"    {format_clp(price)} | {amount}")

        print("  ---")

        print("  BIDS (buy orders):")
        for bid in bids:
            price, amount = bid[0], bid[1]
            print(f"    {format_clp(price)} | {amount}")

        return 0
    except BudaAPIError as e:
        print_status(f"Error: {e}", "ERROR")
        return 1


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Load configuration
    try:
        config = Config.load()
    except ConfigError as e:
        print_status(str(e), "ERROR")
        print_status("Create a .env file with your API credentials.", "INFO")
        print_status("See .env.example for the required format.", "INFO")
        return 1

    # Create API client
    client = BudaClient(config)

    # Execute command
    try:
        if args.command == "buy":
            return cmd_buy(args, client)
        elif args.command == "balance":
            return cmd_balance(args, client)
        elif args.command == "orderbook":
            return cmd_orderbook(args, client)
        else:
            parser.print_help()
            return 0
    except AuthenticationError as e:
        print_status("Authentication failed!", "ERROR")
        print_status("Check your API key and secret in .env", "INFO")
        return 1
    except KeyboardInterrupt:
        print()
        print_status("Interrupted by user.", "WARN")
        return 0


if __name__ == "__main__":
    sys.exit(main())
