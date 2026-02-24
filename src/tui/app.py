"""Main TUI application loop."""

from decimal import Decimal, ROUND_DOWN

from rich.console import Console

from ..api import BudaClient, BudaAPIError, AuthenticationError
from ..bot import TradingBot
from ..config import Config, ConfigError
from ..utils import format_clp, format_crypto
from .display import (
    print_header,
    print_balances_table,
    print_single_balance,
    print_order_book_table,
    print_order_summary,
)
from .prompts import (
    prompt_main_menu,
    prompt_buy_params,
    prompt_sell_params,
    prompt_balance_currency,
    prompt_orderbook_market,
    _prompt_confirm,
)


def launch_tui() -> int:
    """Launch the interactive TUI. Returns exit code."""
    console = Console()

    # Load config
    try:
        config = Config.load()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("[dim]Crea un archivo .env con tus credenciales API. Ver .env.example[/dim]")
        return 1

    client = BudaClient(config)
    print_header(console)

    while True:
        try:
            action = prompt_main_menu()
        except KeyboardInterrupt:
            console.print("\n[dim]Saliendo...[/dim]")
            return 0

        if action == "exit":
            console.print("[dim]Hasta luego![/dim]")
            return 0

        try:
            if action == "buy":
                _handle_buy(console, client)
            elif action == "sell":
                _handle_sell(console, client)
            elif action == "balance":
                _handle_balance(console, client)
            elif action == "orderbook":
                _handle_orderbook(console, client)
        except AuthenticationError:
            console.print("[red]Error de autenticacion. Verifica tu API key y secret en .env[/red]")
        except KeyboardInterrupt:
            console.print("\n")
            continue


def _resolve_amount(console: Console, client: BudaClient, params: dict) -> bool:
    """Resolve amount to native unit (CLP for buy, crypto for sell).

    Modifies params in-place, setting 'amount' to the converted value and
    'converted_display' with a human-readable string of the conversion.

    Returns True on success, False on error.
    """
    side = params["side"]
    unit = params.get("amount_unit", "clp" if side == "buy" else "crypto")
    raw = params["raw_amount"]
    currency = params["currency"]
    market_id = f"{currency}-clp"

    # Already in native unit — no conversion needed
    if (side == "buy" and unit == "clp") or (side == "sell" and unit == "crypto"):
        return True

    try:
        console.print("[dim]Consultando precio...[/dim]")

        if unit == "usd":
            usdc_ticker = client.get_ticker("usdc-clp")
            usdc_price = Decimal(str(usdc_ticker["last_price"][0]))
            console.print(f"[dim]  1 USD ≈ {format_clp(usdc_price)}[/dim]")

            if side == "buy":
                # USD → CLP
                clp_amount = (raw * usdc_price).quantize(Decimal("1"), rounding=ROUND_DOWN)
                params["amount"] = int(clp_amount)
                params["converted_display"] = f"{raw} USD (~{format_clp(clp_amount)})"
                console.print(f"[dim]  Monto equivalente: ~{format_clp(clp_amount)}[/dim]")
            else:
                # USD → crypto: first USD→CLP, then CLP→crypto
                crypto_ticker = client.get_ticker(market_id)
                crypto_price = Decimal(str(crypto_ticker["last_price"][0]))
                clp_amount = raw * usdc_price
                crypto_amount = (clp_amount / crypto_price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                params["amount"] = str(crypto_amount)
                params["converted_display"] = f"{raw} USD (~{format_crypto(crypto_amount, currency)})"
                console.print(f"[dim]  Cantidad equivalente: ~{format_crypto(crypto_amount, currency)}[/dim]")

        elif unit == "crypto":
            # Buy with crypto amount → CLP
            crypto_ticker = client.get_ticker(market_id)
            crypto_price = Decimal(str(crypto_ticker["last_price"][0]))
            console.print(f"[dim]  1 {currency.upper()} ≈ {format_clp(crypto_price)}[/dim]")
            clp_amount = (raw * crypto_price).quantize(Decimal("1"), rounding=ROUND_DOWN)
            params["amount"] = int(clp_amount)
            params["converted_display"] = f"{format_crypto(raw, currency)} (~{format_clp(clp_amount)})"
            console.print(f"[dim]  Monto equivalente: ~{format_clp(clp_amount)}[/dim]")

        elif unit == "clp":
            # Sell with CLP amount → crypto
            crypto_ticker = client.get_ticker(market_id)
            crypto_price = Decimal(str(crypto_ticker["last_price"][0]))
            console.print(f"[dim]  1 {currency.upper()} ≈ {format_clp(crypto_price)}[/dim]")
            crypto_amount = (raw / crypto_price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            params["amount"] = str(crypto_amount)
            params["converted_display"] = f"{format_clp(raw)} (~{format_crypto(crypto_amount, currency)})"
            console.print(f"[dim]  Cantidad equivalente: ~{format_crypto(crypto_amount, currency)}[/dim]")

        console.print()
        return True

    except BudaAPIError as e:
        console.print(f"[red]Error al consultar precio: {e}[/red]\n")
        return False


def _handle_buy(console: Console, client: BudaClient) -> None:
    """Handle the buy flow."""
    try:
        params = prompt_buy_params()
    except KeyboardInterrupt:
        console.print()
        return

    if params is None:
        return

    if not _resolve_amount(console, client, params):
        return

    print_order_summary(console, params)

    try:
        if not _prompt_confirm():
            console.print("[dim]Orden cancelada.[/dim]\n")
            return
    except KeyboardInterrupt:
        console.print()
        return

    _run_bot(console, client, params)


def _handle_sell(console: Console, client: BudaClient) -> None:
    """Handle the sell flow."""
    try:
        params = prompt_sell_params()
    except KeyboardInterrupt:
        console.print()
        return

    if params is None:
        return

    if not _resolve_amount(console, client, params):
        return

    print_order_summary(console, params)

    try:
        if not _prompt_confirm():
            console.print("[dim]Orden cancelada.[/dim]\n")
            return
    except KeyboardInterrupt:
        console.print()
        return

    _run_bot(console, client, params)


def _run_bot(console: Console, client: BudaClient, params: dict) -> None:
    """Create and run the trading bot with the given params."""
    bot = TradingBot(
        client=client,
        currency=params["currency"],
        interval=params["interval"],
        dry_run=params["dry_run"],
        strategy=params["strategy"],
        depth_ratio=Decimal(str(params["depth_ratio"])),
        register_signals=False,
    )

    console.print("[bold green]Bot iniciado.[/bold green] Presiona Ctrl+C para detener y volver al menu.\n")

    try:
        if params["side"] == "buy":
            bot.execute_buy_order(Decimal(str(params["amount"])))
        else:
            bot.execute_sell_order(Decimal(str(params["amount"])))
    except KeyboardInterrupt:
        bot.cleanup()
        console.print("\n[yellow]Bot detenido. Volviendo al menu...[/yellow]\n")
    except BudaAPIError as e:
        console.print(f"\n[red]Error de trading: {e}[/red]\n")


def _handle_balance(console: Console, client: BudaClient) -> None:
    """Handle the balance view."""
    try:
        currency = prompt_balance_currency()
    except KeyboardInterrupt:
        console.print()
        return

    if currency is None:
        return

    try:
        if currency == "all":
            balances = client.get_balances()
            if not balances:
                console.print("[dim]No se encontraron balances.[/dim]\n")
                return
            print_balances_table(console, balances)
        else:
            balance = client.get_balance(currency)
            print_single_balance(console, balance, currency)
    except BudaAPIError as e:
        console.print(f"[red]Error: {e}[/red]\n")


def _handle_orderbook(console: Console, client: BudaClient) -> None:
    """Handle the order book view."""
    try:
        market = prompt_orderbook_market()
    except KeyboardInterrupt:
        console.print()
        return

    if market is None:
        return

    try:
        order_book = client.get_order_book(market)
        print_order_book_table(console, order_book, market)
    except BudaAPIError as e:
        console.print(f"[red]Error: {e}[/red]\n")
