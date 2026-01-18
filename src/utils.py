"""Utility functions for the trading bot."""

from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Tuple


def format_clp(amount: float | str | Decimal) -> str:
    """
    Format a CLP amount with thousand separators.

    Args:
        amount: Amount in CLP.

    Returns:
        Formatted string (e.g., "1,234,567 CLP").
    """
    amount = Decimal(str(amount))
    sign = "-" if amount < 0 else ""
    amount = abs(amount)

    quantized = amount.quantize(Decimal("0.01"))
    formatted_int = f"{int(quantized):,}".replace(",", ".")

    if quantized == quantized.to_integral_value():
        return f"{sign}${formatted_int} CLP"

    decimals = f"{quantized:.2f}".split(".")[1]
    return f"{sign}${formatted_int},{decimals} CLP"


def format_crypto(amount: float | str | Decimal, currency: str) -> str:
    """
    Format a cryptocurrency amount.

    Args:
        amount: Amount of cryptocurrency.
        currency: Currency code (e.g., 'BTC', 'USDC').

    Returns:
        Formatted string (e.g., "0.00123456 BTC").
    """
    amount = Decimal(str(amount))
    if currency.upper() == "BTC":
        formatted = f"{amount:.8f}"
    else:
        formatted = f"{amount:.6f}"
    return f"{formatted} {currency.upper()}"


def parse_order_book_entry(entry: list) -> Tuple[Decimal, Decimal]:
    """
    Parse an order book entry into price and amount.

    Args:
        entry: Order book entry as [price, amount].

    Returns:
        Tuple of (price, amount) as Decimals.
    """
    price = Decimal(str(entry[0]))
    amount = Decimal(str(entry[1]))
    return price, amount


def calculate_amount_for_clp(clp_amount: Decimal, price: Decimal, min_amount: Decimal) -> Decimal:
    """
    Calculate how much crypto can be bought with a given CLP amount.

    Args:
        clp_amount: Amount of CLP to spend.
        price: Price per unit of crypto in CLP.
        min_amount: Minimum order amount for the market.

    Returns:
        Amount of crypto to buy, rounded down to appropriate precision.
    """
    raw_amount = clp_amount / price

    # Round down to 8 decimal places (BTC precision)
    amount = raw_amount.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

    # Ensure amount is at least the minimum
    if amount < min_amount:
        return Decimal("0")

    return amount


def round_price_up(price: Decimal) -> Decimal:
    """
    Round price up to the nearest integer (CLP has no decimals).

    Args:
        price: Price to round.

    Returns:
        Rounded price.
    """
    return price.quantize(Decimal("1"), rounding=ROUND_UP)


def round_price_down(price: Decimal) -> Decimal:
    """
    Round price down to the nearest integer (CLP has no decimals).

    Args:
        price: Price to round.

    Returns:
        Rounded price.
    """
    return price.quantize(Decimal("1"), rounding=ROUND_DOWN)


def print_status(message: str, status: str = "INFO") -> None:
    """
    Print a status message with a prefix.

    Args:
        message: The message to print.
        status: Status type (INFO, OK, WARN, ERROR).
    """
    prefixes = {
        "INFO": "[*]",
        "OK": "[+]",
        "WARN": "[!]",
        "ERROR": "[-]",
    }
    prefix = prefixes.get(status, "[*]")
    print(f"{prefix} {message}")


def print_order_info(order: dict, currency: str) -> None:
    """
    Print formatted order information.

    Args:
        order: Order dictionary from API.
        currency: The cryptocurrency being traded.
    """
    order_id = order.get("id", "N/A")
    state = order.get("state", "unknown")
    price = order.get("limit", ["0"])[0] if isinstance(order.get("limit"), list) else order.get("limit", "0")
    amount = order.get("amount", ["0"])[0] if isinstance(order.get("amount"), list) else order.get("amount", "0")
    traded = order.get("traded_amount", ["0"])[0] if isinstance(order.get("traded_amount"), list) else order.get("traded_amount", "0")

    print(f"    Order ID: {order_id}")
    print(f"    State: {state}")
    print(f"    Price: {format_clp(price)}")
    print(f"    Amount: {format_crypto(amount, currency)}")
    print(f"    Traded: {format_crypto(traded, currency)}")
