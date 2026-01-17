# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bot CLI en Python para comprar BTC o USDC en Buda.com con órdenes límite que mantienen la mejor posición de compra (best bid) automáticamente. The bot monitors the order book and automatically replaces orders to stay at the top of the buy side.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run buy commands
python3 -m src.main buy btc 100000           # Buy BTC with 100,000 CLP
python3 -m src.main buy usdc 50000           # Buy USDC with 50,000 CLP
python3 -m src.main buy btc 100000 --dry-run # Dry run (no real orders)
python3 -m src.main buy btc 100000 --interval 60  # Custom check interval (default 30s)

# Check balances
python3 -m src.main balance clp
python3 -m src.main balance btc

# View order book
python3 -m src.main orderbook btc-clp
```

## Architecture

**Entry point**: `src/main.py` - CLI argument parsing with argparse, routes to command handlers.

**Module responsibilities**:
- `config.py` - Loads API credentials from `.env` file
- `auth.py` - HMAC-SHA384 signature generation per Buda.com API spec
- `api.py` - REST client with retry logic, rate limit handling (429), and auth error detection (401)
- `bot.py` - Core trading logic: order book analysis, price calculation, order placement, continuous monitoring loop with signal handling (SIGINT/SIGTERM for graceful shutdown with order cancellation)
- `utils.py` - Formatting utilities (CLP with thousand separators, BTC to 8 decimals, USDC to 6 decimals)

**Flow for buy command**:
```
main.py → TradingBot.execute_buy_order()
  ├─ verify_balance()
  ├─ get_best_prices()
  ├─ calculate_optimal_price() → best_bid + 1 CLP
  ├─ place_order()
  └─ monitoring loop: check if still best bid, reposition if outbid
```

## Key Technical Details

- Uses Python `Decimal` for all financial calculations (no floating-point precision issues)
- CLP has no decimals (integer), BTC uses 8 decimals, USDC uses 6 decimals
- Prices rounded up when placing orders
- Minimum orders: BTC-CLP 2,000 CLP, USDC-CLP 1,000 CLP
- Custom exception hierarchy: `BudaAPIError`, `AuthenticationError`, `RateLimitError`, `InsufficientBalanceError`
- API reference in `buda-api-documentation.md`
