"""InquirerPy prompt definitions for the TUI."""

from decimal import Decimal, InvalidOperation

from InquirerPy import inquirer
from InquirerPy.separator import Separator


def prompt_main_menu() -> str:
    """Show the main menu and return the selected action."""
    result = inquirer.select(
        message="Selecciona una opcion:",
        choices=[
            {"name": "Comprar", "value": "buy"},
            {"name": "Vender", "value": "sell"},
            Separator(),
            {"name": "Ver Balances", "value": "balance"},
            {"name": "Ver Order Book", "value": "orderbook"},
            Separator(),
            {"name": "Salir", "value": "exit"},
        ],
        default="buy",
    ).execute()
    return result


def _prompt_currency() -> str | None:
    """Prompt for currency selection. Returns None if back."""
    result = inquirer.select(
        message="Moneda:",
        choices=[
            {"name": "BTC", "value": "btc"},
            {"name": "USDC", "value": "usdc"},
            Separator(),
            {"name": "<- Volver", "value": None},
        ],
    ).execute()
    return result


def _prompt_strategy() -> tuple[str, Decimal]:
    """Prompt for strategy selection. Returns (strategy, depth_ratio)."""
    strategy = inquirer.select(
        message="Estrategia de precio:",
        choices=[
            {"name": "Top of book (mejor posicion)", "value": "top"},
            {"name": "Depth-based (profundidad de mercado)", "value": "depth"},
        ],
        default="top",
    ).execute()

    depth_ratio = Decimal("0.9")
    if strategy == "depth":
        ratio_str = inquirer.text(
            message="Ratio de profundidad (0-1):",
            default="0.9",
            validate=lambda val: _validate_depth_ratio(val),
            invalid_message="Debe ser un numero entre 0 y 1",
        ).execute()
        depth_ratio = Decimal(ratio_str)

    return strategy, depth_ratio


def _prompt_interval() -> int:
    """Prompt for monitoring interval."""
    result = inquirer.text(
        message="Intervalo de monitoreo (segundos):",
        default="30",
        validate=lambda val: val.isdigit() and int(val) > 0,
        invalid_message="Debe ser un numero entero positivo",
    ).execute()
    return int(result)


def _prompt_dry_run() -> bool:
    """Prompt for dry run toggle."""
    return inquirer.confirm(
        message="Dry run (simulacion sin ordenes reales)?",
        default=False,
    ).execute()


def _prompt_confirm() -> bool:
    """Prompt for order confirmation."""
    return inquirer.confirm(
        message="Confirmar y ejecutar?",
        default=True,
    ).execute()


def _validate_clp_amount(val: str) -> bool:
    """Validate a CLP amount input."""
    try:
        amount = int(val)
        return amount > 0
    except (ValueError, TypeError):
        return False


def _validate_crypto_amount(val: str) -> bool:
    """Validate a crypto amount input."""
    try:
        amount = Decimal(val)
        return amount > 0
    except (InvalidOperation, TypeError):
        return False


def _validate_depth_ratio(val: str) -> bool:
    """Validate a depth ratio input."""
    try:
        ratio = Decimal(val)
        return Decimal("0") < ratio <= Decimal("1")
    except (InvalidOperation, TypeError):
        return False


def prompt_buy_params() -> dict | None:
    """Run the buy flow prompts. Returns params dict or None if cancelled."""
    currency = _prompt_currency()
    if currency is None:
        return None

    # Ask unit for amount
    unit = inquirer.select(
        message="Ingresar monto en:",
        choices=[
            {"name": "CLP", "value": "clp"},
            {"name": "USD", "value": "usd"},
            {"name": currency.upper(), "value": "crypto"},
        ],
        default="clp",
    ).execute()

    if unit == "clp":
        amount_str = inquirer.text(
            message="Monto en CLP:",
            validate=_validate_clp_amount,
            invalid_message="Debe ser un numero entero positivo",
        ).execute()
        raw_amount = Decimal(amount_str)
    elif unit == "usd":
        amount_str = inquirer.text(
            message="Monto en USD:",
            validate=_validate_crypto_amount,
            invalid_message="Debe ser un numero decimal positivo",
        ).execute()
        raw_amount = Decimal(amount_str)
    else:
        amount_str = inquirer.text(
            message=f"Cantidad de {currency.upper()}:",
            validate=_validate_crypto_amount,
            invalid_message="Debe ser un numero decimal positivo",
        ).execute()
        raw_amount = Decimal(amount_str)

    strategy, depth_ratio = _prompt_strategy()
    interval = _prompt_interval()
    dry_run = _prompt_dry_run()

    return {
        "side": "buy",
        "currency": currency,
        "amount_unit": unit,
        "raw_amount": raw_amount,
        "amount": int(amount_str) if unit == "clp" else raw_amount,
        "strategy": strategy,
        "depth_ratio": depth_ratio,
        "interval": interval,
        "dry_run": dry_run,
    }


def prompt_sell_params() -> dict | None:
    """Run the sell flow prompts. Returns params dict or None if cancelled."""
    currency = _prompt_currency()
    if currency is None:
        return None

    # Ask unit for amount
    unit = inquirer.select(
        message="Ingresar cantidad en:",
        choices=[
            {"name": currency.upper(), "value": "crypto"},
            {"name": "USD", "value": "usd"},
            {"name": "CLP", "value": "clp"},
        ],
        default="crypto",
    ).execute()

    if unit == "crypto":
        amount_str = inquirer.text(
            message=f"Cantidad de {currency.upper()} a vender:",
            validate=_validate_crypto_amount,
            invalid_message="Debe ser un numero decimal positivo",
        ).execute()
        raw_amount = Decimal(amount_str)
    elif unit == "usd":
        amount_str = inquirer.text(
            message="Monto en USD:",
            validate=_validate_crypto_amount,
            invalid_message="Debe ser un numero decimal positivo",
        ).execute()
        raw_amount = Decimal(amount_str)
    else:
        amount_str = inquirer.text(
            message="Monto en CLP:",
            validate=_validate_clp_amount,
            invalid_message="Debe ser un numero entero positivo",
        ).execute()
        raw_amount = Decimal(amount_str)

    strategy, depth_ratio = _prompt_strategy()
    interval = _prompt_interval()
    dry_run = _prompt_dry_run()

    return {
        "side": "sell",
        "currency": currency,
        "amount_unit": unit,
        "raw_amount": raw_amount,
        "amount": amount_str if unit == "crypto" else raw_amount,
        "strategy": strategy,
        "depth_ratio": depth_ratio,
        "interval": interval,
        "dry_run": dry_run,
    }


def prompt_balance_currency() -> str | None:
    """Prompt for balance currency. Returns currency string, 'all', or None."""
    result = inquirer.select(
        message="Moneda:",
        choices=[
            {"name": "Todas", "value": "all"},
            {"name": "CLP", "value": "clp"},
            {"name": "BTC", "value": "btc"},
            {"name": "USDC", "value": "usdc"},
            Separator(),
            {"name": "<- Volver", "value": None},
        ],
    ).execute()
    return result


def prompt_orderbook_market() -> str | None:
    """Prompt for order book market. Returns market string or None."""
    result = inquirer.select(
        message="Mercado:",
        choices=[
            {"name": "BTC-CLP", "value": "btc-clp"},
            {"name": "USDC-CLP", "value": "usdc-clp"},
            Separator(),
            {"name": "<- Volver", "value": None},
        ],
    ).execute()
    return result
