"""Small synthetic assistant regressions; no network or microphone needed."""
import io
import json
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from rich.console import Console

from src.assistant import Assistant, AssistantError, AssistantTools, PreparedOrder
from src.groq import GroqClient
from src.market import MarketRegistry


def make_tools(quote='clp'):
    client = Mock()
    client.get_markets.return_value = [
        {'id': f'{base}-{quote}', 'minimum_order_amount': ['0.00001']}
        for base in ('btc', 'usdc')
    ]
    return AssistantTools(client, MarketRegistry(client, quote))


def order_args(**changes):
    return dict(side='buy', currency='btc', amount='10000', unit='clp',
                strategy='top', **changes)


def tool_message(name, arguments):
    return {'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}]}


class AssistantTests(unittest.TestCase):
    def test_preparation_never_calls_trading_api(self):
        tools = make_tools()
        tools.client.reset_mock()
        result = tools.execute('preparar_orden', order_args())
        self.assertIsInstance(result, PreparedOrder)
        self.assertEqual(result.params['amount'], Decimal('10000'))
        self.assertTrue(result.params['dry_run'])
        self.assertEqual(tools.client.mock_calls, [])

    def test_invalid_orders_are_rejected(self):
        for field, value in [('amount', 'NaN'), ('amount', 'Infinity'),
                             ('amount', '-1'), ('amount', '0'), ('amount', '1e9999'),
                             ('amount', '1.5'), ('currency', 'doge'), ('unit', 'cop'),
                             ('side', 'cancel'), ('strategy', 'grid'),
                             ('dry_run', 'false'), ('interval', True),
                             ('interval', 0), ('depth_ratio', '0'), ('unknown', 1)]:
            with self.subTest(field=field, value=value):
                args = order_args()
                args[field] = value
                with self.assertRaises(AssistantError):
                    make_tools().execute('preparar_orden', args)

    def test_quote_currency_is_not_silently_reinterpreted(self):
        tools = make_tools('pen')
        args = order_args()
        with self.assertRaises(AssistantError):
            tools.execute('preparar_orden', args)
        args.update(unit='pen', amount='10.25')
        result = tools.execute('preparar_orden', args)
        self.assertEqual(result.params['amount_unit'], 'clp')  # existing TUI convention
        self.assertEqual(result.params['raw_amount'], Decimal('10.25'))

    def test_balances_exclude_unneeded_private_fields(self):
        tools = make_tools()
        tools.client.get_balance.return_value = {
            'available_amount': ['2', 'BTC'], 'frozen_amount': ['1', 'BTC'],
            'account_id': 'private',
        }
        result = tools.execute('consultar_saldo', {'currency': 'btc'})
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['balances'][0]['available_amount'], ['2', 'BTC'])

    def test_only_one_validated_tool_call_per_response(self):
        tools = make_tools()
        tools.client.reset_mock()
        model = Mock()
        message = tool_message('consultar_saldo', {'currency': 'btc'})
        message['tool_calls'] *= 2
        model.complete.return_value = message
        with self.assertRaises(AssistantError):
            Assistant(model, tools).reply('saldo')
        self.assertEqual(tools.client.mock_calls, [])

    def test_unknown_and_malformed_tools_are_rejected(self):
        for message in [tool_message('confirmar', {}),
                        tool_message('consultar_saldo', {'currency': '../secrets'}),
                        {'tool_calls': [{'function': {'arguments': '['}}]}]:
            with self.subTest(message=message):
                model = Mock()
                model.complete.return_value = message
                with self.assertRaises(AssistantError):
                    Assistant(model, make_tools()).reply('confirmar')

    def test_read_results_then_answer_and_clear_history(self):
        tools = make_tools()
        tools.client.get_order_book.return_value = {'bids': [['10', '1']], 'asks': [['11', '2']]}
        model = Mock()
        model.complete.side_effect = [tool_message('consultar_precio', {'currency': 'btc'}),
                                      {'content': 'Compra 11, venta 10 CLP.'}]
        assistant = Assistant(model, tools)
        self.assertIn('11', assistant.reply('precio btc'))
        self.assertEqual(len(assistant.turns), 1)
        assistant.clear()
        self.assertEqual(assistant.turns, [])

    def test_prepared_order_ends_turn_without_another_model_call(self):
        model = Mock()
        model.complete.return_value = tool_message('preparar_orden', order_args())
        result = Assistant(model, make_tools()).reply('comprar')
        self.assertIsInstance(result, PreparedOrder)
        model.complete.assert_called_once()

    def test_review_requires_keyboard_confirmation(self):
        from src.tui.assistant import review_order
        console = Console(file=io.StringIO())
        tools = make_tools()
        result = tools.execute('preparar_orden', order_args())
        with patch('src.tui.assistant.inquirer.confirm') as confirm, \
                patch('src.tui.app._run_bot') as run:
            confirm.return_value.execute.return_value = False
            review_order(console, tools.client, tools.registry, result)
            run.assert_not_called()
            self.assertFalse(confirm.call_args.kwargs['default'])
            confirm.return_value.execute.return_value = True
            review_order(console, tools.client, tools.registry, result)
            run.assert_called_once()


class GroqTests(unittest.TestCase):
    def test_timeout_is_not_retried_and_does_not_expose_key(self):
        import requests
        with patch('src.groq.requests.post', side_effect=requests.Timeout('secret')) as post:
            with self.assertRaises(AssistantError) as error:
                GroqClient('secret').complete([], [])
            self.assertNotIn('secret', str(error.exception))
            post.assert_called_once()

    def test_bad_status_or_incomplete_response_is_rejected(self):
        for status, payload in [(401, {}), (429, {}), (500, {}), (200, {}),
                                (200, {'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]})]:
            with self.subTest(status=status, payload=payload):
                response = Mock(status_code=status)
                response.json.return_value = payload
                with patch('src.groq.requests.post', return_value=response):
                    with self.assertRaises(AssistantError):
                        GroqClient('test').complete([], [])

    def test_transcription_uses_spanish_and_in_memory_wav(self):
        response = Mock(status_code=200)
        response.json.return_value = {'text': '  saldo bitcoin  '}
        with patch('src.groq.requests.post', return_value=response) as post:
            self.assertEqual(GroqClient('test').transcribe(b'wav'), 'saldo bitcoin')
            self.assertEqual(post.call_args.kwargs['data']['language'], 'es')
            context = post.call_args.kwargs['data']['prompt']
            for symbol in ('BTC', 'USDC', 'USDT'):
                self.assertIn(symbol, context)
            self.assertIn('USD Coin', context)
            self.assertIn('Tether', context)
            self.assertEqual(post.call_args.kwargs['files']['file'][1], b'wav')

class AssistantMenuTests(unittest.TestCase):
    def test_missing_key_returns_to_manual_menu(self):
        from src.tui.assistant import launch_assistant
        tools = make_tools()
        console = Console(file=io.StringIO())
        with patch('src.tui.assistant.GroqClient.from_env', return_value=GroqClient('')), \
                patch('src.tui.assistant.inquirer.select') as select:
            launch_assistant(console, tools.client, tools.registry)
        select.assert_not_called()

    def test_voice_transcript_reaches_review_without_executing_directly(self):
        from src.tui.assistant import launch_assistant
        tools = make_tools()
        model = Mock(api_key='test')
        model.transcribe.return_value = 'comprar bitcoin'
        model.complete.return_value = tool_message('preparar_orden', order_args())
        with patch('src.tui.assistant.GroqClient.from_env', return_value=model), \
                patch('src.tui.assistant.inquirer.select') as select, \
                patch('src.tui.assistant.record_voice', return_value=b'wav'), \
                patch('src.tui.assistant.review_order') as review:
            select.return_value.execute.side_effect = ['voice', 'back']
            launch_assistant(Console(file=io.StringIO()), tools.client, tools.registry)
            model.transcribe.assert_called_once_with(b'wav')
            review.assert_called_once()
            self.assertIsInstance(review.call_args.args[-1], PreparedOrder)

    def test_cancelled_voice_does_not_contact_groq(self):
        from src.tui.assistant import launch_assistant
        tools = make_tools()
        model = Mock(api_key='test')
        with patch('src.tui.assistant.GroqClient.from_env', return_value=model), \
                patch('src.tui.assistant.inquirer.select') as select, \
                patch('src.tui.assistant.record_voice', return_value=None):
            select.return_value.execute.side_effect = ['voice', 'back']
            launch_assistant(Console(file=io.StringIO()), tools.client, tools.registry)
            model.transcribe.assert_not_called()
            model.complete.assert_not_called()

class AmountUnitRegressionTests(unittest.TestCase):
    def test_sell_crypto_to_quote_recovers_from_wrong_destination_unit(self):
        tools = make_tools()
        tools.client.reset_mock()
        model = Mock()
        args = dict(side='sell', currency='usdc', amount='0.3', unit='clp',
                    strategy='market', dry_run=False)
        model.complete.side_effect = [tool_message('preparar_orden', args),
                                      tool_message('preparar_orden', dict(args, unit='usdc'))]
        result = Assistant(model, tools).reply(
            'Vender 0.3 USDC a CLP, precio de mercado, orden real.')
        self.assertEqual(result.params['amount'], Decimal('0.3'))
        self.assertEqual(result.params['amount_unit'], 'crypto')
        self.assertEqual(result.params['source_unit'], 'usdc')
        self.assertFalse(result.params['dry_run'])
        self.assertEqual(tools.client.mock_calls, [])
        feedback = model.complete.call_args.args[0][-1]
        self.assertFalse(json.loads(feedback['content'])['order_prepared'])

    def test_sale_by_quote_equivalent_remains_supported(self):
        result = make_tools().execute('preparar_orden', dict(
            side='sell', currency='usdc', amount='10000', unit='clp', strategy='market'))
        self.assertEqual(result.params['amount_unit'], 'clp')
        self.assertEqual(result.params['amount'], Decimal('10000'))

    def test_invalid_order_repair_is_bounded_and_never_executes(self):
        tools = make_tools()
        tools.client.reset_mock()
        model = Mock()
        model.complete.return_value = tool_message('preparar_orden', dict(
            side='sell', currency='usdc', amount='0.3', unit='clp', strategy='market'))
        with self.assertRaises(AssistantError):
            Assistant(model, tools).reply('Vender 0.3 USDC a CLP')
        self.assertEqual(model.complete.call_count, 4)
        self.assertEqual(tools.client.mock_calls, [])
