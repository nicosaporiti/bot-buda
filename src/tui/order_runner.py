"""Isolated order execution; stdout belongs to the dashboard's progress log."""
import json
import signal
import sys
from decimal import Decimal

from ..api import BudaClient
from ..bot import TradingBot
from ..config import Config
from ..market import MarketConfig
from .order_process import READY


def execute(bot, params):
    def protect_cleanup():
        # Stop requests must not interrupt cancellation, including after errors.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def stop(signum, frame):
        protect_cleanup()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        print(READY, flush=True)
        amount = Decimal(str(params['amount']))
        if params['side'] == 'buy':
            bot.execute_buy_order(amount)
        else:
            bot.execute_sell_order(amount)
        return 0
    except KeyboardInterrupt:
        protect_cleanup()
        bot.cleanup()
        print('Detención finalizada. Revisá el resumen y cualquier aviso de cancelación.', flush=True)
        return 0
    except Exception as error:
        protect_cleanup()
        print(f'Error de ejecución: {error}', flush=True)
        bot.cleanup()
        return 1


def main():
    try:
        payload = json.load(sys.stdin)
        params = payload['params']
        market = payload['market']
        for key in ('min_order_amount', 'price_tick'):
            market[key] = Decimal(market[key])
        bot = TradingBot(
            BudaClient(Config.load()), MarketConfig(**market),
            interval=params['interval'], dry_run=params['dry_run'],
            strategy=params['strategy'], depth_ratio=Decimal(str(params['depth_ratio'])),
            register_signals=False,
        )
        return execute(bot, params)
    except Exception as error:
        print(f'No se pudo completar la ejecución: {type(error).__name__}. Revisá las órdenes antes de repetir.', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
