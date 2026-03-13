#!/usr/bin/env python3
"""CLI entry point for Buda.com trading bot."""

import argparse
import sys
from decimal import Decimal

from .api import BudaClient, AuthenticationError, BudaAPIError
from .bot import TradingBot
from .config import Config, ConfigError
from .market import MarketRegistry
from .utils import format_clp, print_status


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        description="Buda.com Trading Bot - Maintain best bid/ask position",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main buy btc 100000        # Buy BTC with 100,000 CLP
  python -m src.main buy usdc 50000        # Buy USDC with 50,000 CLP
  python -m src.main buy btc 100000 --interval 60   # Check every 60 seconds
  python -m src.main buy btc 100000 --dry-run       # Simulate without trading
  python -m src.main buy btc 100000 --strategy depth --depth 0.9
  python -m src.main sell btc 0.001        # Sell 0.001 BTC
  python -m src.main sell usdc 50          # Sell 50 USDC
  python -m src.main sell btc 0.001 --strategy depth --depth 0.9
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Buy command
    buy_parser = subparsers.add_parser("buy", help="Place and maintain a buy order")
    buy_parser.add_argument(
        "currency",
        type=str,
        help="Currency to buy (e.g. btc, usdc, eth)"
    )
    buy_parser.add_argument(
        "amount",
        type=str,
        help="Amount of quote currency to spend"
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
    buy_parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        choices=["top", "depth"],
        default="top",
        help="Pricing strategy: top (best bid/ask) or depth (cumulative volume)"
    )
    buy_parser.add_argument(
        "--depth",
        type=float,
        default=0.9,
        help="Depth ratio for strategy=depth (0-1, default: 0.9)"
    )

    # Sell command
    sell_parser = subparsers.add_parser("sell", help="Place and maintain a sell order")
    sell_parser.add_argument(
        "currency",
        type=str,
        help="Currency to sell (e.g. btc, usdc, eth)"
    )
    sell_parser.add_argument(
        "amount",
        type=str,
        help="Amount of crypto to sell"
    )
    sell_parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=30,
        help="Monitoring interval in seconds (default: 30)"
    )
    sell_parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="Simulate without placing real orders"
    )
    sell_parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        choices=["top", "depth"],
        default="top",
        help="Pricing strategy: top (best bid/ask) or depth (cumulative volume)"
    )
    sell_parser.add_argument(
        "--depth",
        type=float,
        default=0.9,
        help="Depth ratio for strategy=depth (0-1, default: 0.9)"
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
        default=None,
        help="Market to show (e.g. btc-clp)"
    )

    return parser


def cmd_buy(args, client: BudaClient, registry: MarketRegistry) -> int:
    """Execute the buy command."""
    currency = args.currency.lower()
    depth_ratio = Decimal(str(args.depth))

    try:
        clp_amount = Decimal(args.amount)
    except Exception:
        print_status(f"Invalid amount: {args.amount}", "ERROR")
        return 1

    # Validate currency against registry
    available = registry.currencies()
    if currency not in available:
        print_status(
            f"Currency '{currency}' not available. Options: {', '.join(available)}",
            "ERROR",
        )
        return 1

    if clp_amount <= 0:
        print_status("Amount must be positive", "ERROR")
        return 1
    if not (Decimal("0") < depth_ratio <= Decimal("1")):
        print_status("Depth ratio must be between 0 and 1", "ERROR")
        return 1

    print_status(f"Buda.com Trading Bot", "INFO")
    print_status(f"=" * 40, "INFO")
    print()

    market_config = registry.get_by_currency(currency)
    bot = TradingBot(
        client=client,
        market_config=market_config,
        interval=args.interval,
        dry_run=args.dry_run,
        strategy=args.strategy,
        depth_ratio=depth_ratio,
    )

    try:
        bot.execute_buy_order(clp_amount)
        return 0
    except BudaAPIError as e:
        print_status(f"Trading error: {e}", "ERROR")
        return 1


def cmd_sell(args, client: BudaClient, registry: MarketRegistry) -> int:
    """Execute the sell command."""
    currency = args.currency.lower()
    crypto_amount = Decimal(str(args.amount))
    depth_ratio = Decimal(str(args.depth))

    # Validate currency against registry
    available = registry.currencies()
    if currency not in available:
        print_status(
            f"Currency '{currency}' not available. Options: {', '.join(available)}",
            "ERROR",
        )
        return 1

    if crypto_amount <= 0:
        print_status("Amount must be positive", "ERROR")
        return 1
    if not (Decimal("0") < depth_ratio <= Decimal("1")):
        print_status("Depth ratio must be between 0 and 1", "ERROR")
        return 1

    print_status(f"Buda.com Trading Bot", "INFO")
    print_status(f"=" * 40, "INFO")
    print()

    market_config = registry.get_by_currency(currency)
    bot = TradingBot(
        client=client,
        market_config=market_config,
        interval=args.interval,
        dry_run=args.dry_run,
        strategy=args.strategy,
        depth_ratio=depth_ratio,
    )

    try:
        bot.execute_sell_order(crypto_amount)
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


def cmd_orderbook(args, client: BudaClient, registry: MarketRegistry) -> int:
    """Execute the orderbook command."""
    market = args.market
    if market is None:
        # Default to first available market
        market_ids = registry.market_ids()
        if market_ids:
            market = market_ids[0]
        else:
            print_status("No markets available", "ERROR")
            return 1
    market = market.lower()

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
        from .tui import launch_tui
        return launch_tui()

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

    # Build market registry
    try:
        registry = MarketRegistry(client, config.quote_currency)
    except Exception as e:
        print_status(f"Failed to load markets: {e}", "ERROR")
        return 1

    # Execute command
    try:
        if args.command == "buy":
            return cmd_buy(args, client, registry)
        elif args.command == "sell":
            return cmd_sell(args, client, registry)
        elif args.command == "balance":
            return cmd_balance(args, client)
        elif args.command == "orderbook":
            return cmd_orderbook(args, client, registry)
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
