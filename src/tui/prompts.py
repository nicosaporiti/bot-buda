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
            {"name": "Grilla", "value": "grid"},
            Separator(),
            {"name": "Ver Balances", "value": "balance"},
            {"name": "Ver Order Book", "value": "orderbook"},
            Separator(),
            {"name": "Salir", "value": "exit"},
        ],
        default="buy",
    ).execute()
    return result


def _prompt_currency(currencies: list[str]) -> str | None:
    """Prompt for currency selection. Returns None if back."""
    choices = [{"name": c.upper(), "value": c} for c in currencies]
    choices.append(Separator())
    choices.append({"name": "<- Volver", "value": None})
    result = inquirer.select(
        message="Moneda:",
        choices=choices,
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


def _quote_amount_validator(quote_decimals: int):
    """Return a validator for quote-currency amounts.

    Integer-only when quote_decimals == 0, decimal otherwise.
    """
    if quote_decimals == 0:
        return _validate_clp_amount
    return _validate_crypto_amount


def prompt_buy_params(
    currencies: list[str],
    quote_currency: str = "clp",
    quote_decimals: int = 0,
    usd_unit_available: bool = True,
) -> dict | None:
    """Run the buy flow prompts. Returns params dict or None if cancelled."""
    currency = _prompt_currency(currencies)
    if currency is None:
        return None

    qc_label = quote_currency.upper()
    validate_quote = _quote_amount_validator(quote_decimals)
    invalid_msg = (
        "Debe ser un numero entero positivo"
        if quote_decimals == 0
        else "Debe ser un numero positivo"
    )

    # Build unit choices
    unit_choices = [{"name": qc_label, "value": "clp"}]
    if usd_unit_available:
        unit_choices.append({"name": "USD", "value": "usd"})
    unit_choices.append({"name": currency.upper(), "value": "crypto"})

    unit = inquirer.select(
        message="Ingresar monto en:",
        choices=unit_choices,
        default="clp",
    ).execute()

    if unit == "clp":
        amount_str = inquirer.text(
            message=f"Monto en {qc_label}:",
            validate=validate_quote,
            invalid_message=invalid_msg,
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
        "amount": raw_amount,
        "strategy": strategy,
        "depth_ratio": depth_ratio,
        "interval": interval,
        "dry_run": dry_run,
    }


def prompt_sell_params(
    currencies: list[str],
    quote_currency: str = "clp",
    quote_decimals: int = 0,
    usd_unit_available: bool = True,
) -> dict | None:
    """Run the sell flow prompts. Returns params dict or None if cancelled."""
    currency = _prompt_currency(currencies)
    if currency is None:
        return None

    qc_label = quote_currency.upper()
    validate_quote = _quote_amount_validator(quote_decimals)
    invalid_msg = (
        "Debe ser un numero entero positivo"
        if quote_decimals == 0
        else "Debe ser un numero positivo"
    )

    # Build unit choices
    unit_choices = [{"name": currency.upper(), "value": "crypto"}]
    if usd_unit_available:
        unit_choices.append({"name": "USD", "value": "usd"})
    unit_choices.append({"name": qc_label, "value": "clp"})

    unit = inquirer.select(
        message="Ingresar cantidad en:",
        choices=unit_choices,
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
            message=f"Monto en {qc_label}:",
            validate=validate_quote,
            invalid_message=invalid_msg,
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


def _validate_positive_decimal(val: str) -> bool:
    try:
        return Decimal(val) > 0
    except (InvalidOperation, TypeError):
        return False


def _validate_positive_int(val: str) -> bool:
    try:
        return int(val) > 0
    except (ValueError, TypeError):
        return False


def prompt_grid_params(currencies: list[str], quote_currency: str = "clp") -> dict | None:
    """Run the grid flow prompts. Returns params dict or None if cancelled."""
    currency = _prompt_currency(currencies)
    if currency is None:
        return None

    qc_label = quote_currency.upper()

    range_mode = inquirer.select(
        message="Modo de rango:",
        choices=[
            {"name": "Automatico (% sobre precio actual)", "value": "auto"},
            {"name": "Manual (lower/upper)", "value": "manual"},
        ],
        default="auto",
    ).execute()

    lower = upper = range_pct = None
    if range_mode == "auto":
        pct_str = inquirer.text(
            message="Rango como % del precio actual (ej. 10):",
            default="10",
            validate=_validate_positive_decimal,
            invalid_message="Debe ser un numero positivo",
        ).execute()
        range_pct = Decimal(pct_str)
    else:
        lower_str = inquirer.text(
            message=f"Precio inferior ({qc_label}):",
            validate=_validate_positive_decimal,
            invalid_message="Debe ser un numero positivo",
        ).execute()
        upper_str = inquirer.text(
            message=f"Precio superior ({qc_label}):",
            validate=_validate_positive_decimal,
            invalid_message="Debe ser un numero positivo",
        ).execute()
        lower = Decimal(lower_str)
        upper = Decimal(upper_str)
        if lower >= upper:
            return None

    levels_str = inquirer.text(
        message="Cantidad de niveles (>= 2):",
        default="12",
        validate=lambda v: _validate_positive_int(v) and int(v) >= 2,
        invalid_message="Debe ser un entero >= 2",
    ).execute()

    quote_budget_str = inquirer.text(
        message=f"Quote budget ({qc_label}):",
        validate=_validate_positive_decimal,
        invalid_message="Debe ser un numero positivo",
    ).execute()

    base_budget_str = inquirer.text(
        message=f"Base budget ({currency.upper()}, 0 si no quieres ventas iniciales):",
        default="0",
        validate=lambda v: _validate_positive_decimal(v) or v == "0",
        invalid_message="Debe ser un numero >= 0",
    ).execute()

    max_open_str = inquirer.text(
        message="max_open_orders:",
        default="6",
        validate=_validate_positive_int,
        invalid_message="Debe ser un entero positivo",
    ).execute()

    interval = _prompt_interval()
    dry_run = _prompt_dry_run()

    return {
        "side": "grid",
        "currency": currency,
        "lower": lower,
        "upper": upper,
        "range_pct": range_pct,
        "levels": int(levels_str),
        "quote_budget": Decimal(quote_budget_str),
        "base_budget": Decimal(base_budget_str),
        "max_open_orders": int(max_open_str),
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


def prompt_orderbook_market(market_ids: list[str]) -> str | None:
    """Prompt for order book market. Returns market string or None."""
    choices = [{"name": mid.upper(), "value": mid} for mid in market_ids]
    choices.append(Separator())
    choices.append({"name": "<- Volver", "value": None})
    result = inquirer.select(
        message="Mercado:",
        choices=choices,
    ).execute()
    return result
