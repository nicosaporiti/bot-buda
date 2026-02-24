"""Display functions using rich for the TUI."""

from decimal import Decimal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..utils import format_clp, format_crypto


def print_header(console: Console) -> None:
    """Print the application banner."""
    banner = Text()
    banner.append("Buda Trading Bot", style="bold green")
    console.print(Panel(banner, border_style="green", expand=False))
    console.print()


def print_balances_table(console: Console, balances: list[dict]) -> None:
    """Print a formatted table of balances."""
    table = Table(title="Account Balances", border_style="blue")
    table.add_column("Currency", style="bold cyan", justify="center")
    table.add_column("Available", justify="right")
    table.add_column("Frozen", justify="right", style="yellow")

    for balance in balances:
        currency_id = balance.get("id", "")
        if not currency_id and isinstance(balance.get("available_amount"), list):
            currency_id = balance["available_amount"][1]
        currency_id = (currency_id or "unknown").upper()

        available = balance.get("available_amount", ["0", currency_id])
        frozen = balance.get("frozen_amount", ["0", currency_id])

        avail_str = f"{available[0]} {available[1]}" if isinstance(available, list) else str(available)
        frozen_str = f"{frozen[0]} {frozen[1]}" if isinstance(frozen, list) else str(frozen)

        table.add_row(currency_id, avail_str, frozen_str)

    console.print(table)
    console.print()


def print_single_balance(console: Console, balance: dict, currency: str) -> None:
    """Print a formatted table for a single currency balance."""
    table = Table(title=f"Balance: {currency.upper()}", border_style="blue")
    table.add_column("Field", style="bold cyan")
    table.add_column("Value", justify="right")

    available = balance.get("available_amount", ["0", currency.upper()])
    frozen = balance.get("frozen_amount", ["0", currency.upper()])

    avail_str = f"{available[0]} {available[1]}" if isinstance(available, list) else str(available)
    frozen_str = f"{frozen[0]} {frozen[1]}" if isinstance(frozen, list) else str(frozen)

    table.add_row("Available", avail_str)
    table.add_row("Frozen", frozen_str)

    console.print(table)
    console.print()


def print_order_book_table(console: Console, order_book: dict, market: str) -> None:
    """Print the order book with colored asks/bids."""
    bids = order_book.get("bids", [])[:10]
    asks = order_book.get("asks", [])[:10]

    table = Table(title=f"Order Book: {market.upper()}", border_style="blue")
    table.add_column("Side", style="bold", justify="center")
    table.add_column("Price (CLP)", justify="right")
    table.add_column("Amount", justify="right")

    for ask in reversed(asks):
        price, amount = ask[0], ask[1]
        table.add_row(
            Text("ASK", style="red"),
            Text(format_clp(price), style="red"),
            Text(str(amount), style="red"),
        )

    if asks and bids:
        best_ask = Decimal(str(asks[0][0]))
        best_bid = Decimal(str(bids[0][0]))
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask * 100).quantize(Decimal("0.01"))
        table.add_row(
            Text("---", style="dim"),
            Text(f"Spread: {format_clp(spread)} ({spread_pct}%)", style="bold dim"),
            Text("", style="dim"),
        )

    for bid in bids:
        price, amount = bid[0], bid[1]
        table.add_row(
            Text("BID", style="green"),
            Text(format_clp(price), style="green"),
            Text(str(amount), style="green"),
        )

    console.print(table)
    console.print()


def print_order_summary(console: Console, params: dict) -> None:
    """Print a confirmation panel before executing an order."""
    side = params.get("side", "buy")
    currency = params["currency"].upper()
    strategy = "Top of book" if params["strategy"] == "top" else f"Depth-based (ratio: {params['depth_ratio']})"

    lines = []
    lines.append(f"[bold]Side:[/bold] {'Comprar' if side == 'buy' else 'Vender'}")
    lines.append(f"[bold]Moneda:[/bold] {currency}")
    if "converted_display" in params:
        label = "Monto" if side == "buy" else "Cantidad"
        lines.append(f"[bold]{label}:[/bold] {params['converted_display']}")
    elif side == "buy":
        lines.append(f"[bold]Monto:[/bold] {format_clp(params['amount'])}")
    else:
        lines.append(f"[bold]Cantidad:[/bold] {format_crypto(params['amount'], currency)}")
    lines.append(f"[bold]Estrategia:[/bold] {strategy}")
    lines.append(f"[bold]Intervalo:[/bold] {params['interval']}s")
    lines.append(f"[bold]Dry run:[/bold] {'Si' if params['dry_run'] else 'No'}")

    title = "Resumen de Orden"
    border = "yellow" if params["dry_run"] else "red"

    console.print(Panel("\n".join(lines), title=title, border_style=border, expand=False))
    console.print()
