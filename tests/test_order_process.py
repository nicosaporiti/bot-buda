"""Process isolation and cleanup regressions; no credentials or network."""
import asyncio
import sys
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from src.tui.order_process import OrderProcess
from src.tui.order_runner import execute
from tests.test_assistant import make_tools


class OrderProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_output_stops_child_and_waits_for_cleanup(self):
        script = '''
import json, sys, time
from src.tui.order_runner import execute
payload = json.load(sys.stdin)
class Bot:
    def execute_buy_order(self, amount):
        print('x' * 70000, flush=True)
        while True:
            time.sleep(0.1)
    def cleanup(self):
        time.sleep(0.1)
sys.exit(execute(Bot(), payload['params']))
'''
        process = OrderProcess()
        with self.assertRaises(ValueError):
            await asyncio.wait_for(process.run(
                {'side': 'buy', 'amount': '10'},
                make_tools().registry.get_by_currency('btc'), lambda line: None,
                command=[sys.executable, '-u', '-c', script]), timeout=5)
        self.assertTrue(process.stop_sent)
        self.assertEqual(process.process.returncode, 0)

    async def test_stop_during_error_cleanup_waits_for_cancellation(self):
        script = '''
import json, sys, time
from src.tui.order_runner import execute
payload = json.load(sys.stdin)
class Bot:
    def execute_buy_order(self, amount):
        raise RuntimeError('synthetic failure')
    def cleanup(self):
        print('cleanup started', flush=True)
        time.sleep(0.3)
        print('cancelled synthetic-1', flush=True)
sys.exit(execute(Bot(), payload['params']))
'''
        process = OrderProcess()
        lines = []

        def on_line(line):
            lines.append(line)
            if line == 'cleanup started':
                process.stop()

        code = await asyncio.wait_for(process.run(
            {'side': 'buy', 'amount': '10'},
            make_tools().registry.get_by_currency('btc'), on_line,
            command=[sys.executable, '-u', '-c', script]), timeout=5)
        self.assertEqual(code, 1)
        self.assertTrue(process.stop_sent)
        self.assertIn('cancelled synthetic-1', lines)

    async def test_stop_streams_cleanup_and_preserves_parent_process(self):
        # A real child process exercises the pipe, ready handshake and SIGINT.
        script = '''
import json, sys, time
from src.tui.order_runner import execute
payload = json.load(sys.stdin)
class Bot:
    def execute_buy_order(self, amount):
        print('running ' + str(amount), flush=True)
        while True:
            time.sleep(0.1)
    def cleanup(self):
        print('cancelled synthetic-1', flush=True)
sys.exit(execute(Bot(), payload['params']))
'''
        for mode in ('normal', 'early', 'cancel_worker'):
            with self.subTest(mode=mode):
                process = OrderProcess()
                lines = []
                if mode == 'early':
                    process.stop()

                def on_line(line):
                    lines.append(line)
                    if line.startswith('running'):
                        if mode == 'cancel_worker':
                            task.cancel()
                        else:
                            process.stop()
                            process.stop()

                market = make_tools().registry.get_by_currency('btc')
                task = asyncio.create_task(process.run(
                    {'side': 'buy', 'amount': Decimal('10000')}, market, on_line,
                    command=[sys.executable, '-u', '-c', script]))
                if mode == 'cancel_worker':
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=5)
                    self.assertEqual(process.process.returncode, 0)
                else:
                    self.assertEqual(await asyncio.wait_for(task, timeout=5), 0)
                self.assertIn('cancelled synthetic-1', lines)
                self.assertTrue(process.stop_sent)


class OrderRunnerTests(unittest.TestCase):
    def test_interrupt_cleans_up_without_exiting_the_parent(self):
        bot = Mock()
        bot.execute_buy_order.side_effect = KeyboardInterrupt
        with patch('src.tui.order_runner.signal.signal'), patch('builtins.print'):
            self.assertEqual(execute(bot, {'side': 'buy', 'amount': '10'}), 0)
        bot.execute_buy_order.assert_called_once_with(Decimal('10'))
        bot.cleanup.assert_called_once()

    def test_execution_error_is_nonzero_and_cleans_up(self):
        bot = Mock()
        bot.execute_sell_order.side_effect = RuntimeError('synthetic failure')
        with patch('src.tui.order_runner.signal.signal'), patch('builtins.print'):
            self.assertEqual(execute(bot, {'side': 'sell', 'amount': '0.1'}), 1)
        bot.cleanup.assert_called_once()

    def test_success_does_not_cancel_completed_order(self):
        bot = Mock()
        with patch('src.tui.order_runner.signal.signal'), patch('builtins.print'):
            self.assertEqual(execute(bot, {'side': 'sell', 'amount': '0.1'}), 0)
        bot.execute_sell_order.assert_called_once_with(Decimal('0.1'))
        bot.cleanup.assert_not_called()
