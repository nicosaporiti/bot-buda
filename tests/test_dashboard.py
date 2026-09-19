"""Headless UI regressions with minimal account data and no external services."""
import unittest
from unittest.mock import Mock, patch

from textual.widgets import ContentSwitcher, DataTable, Input, Select

from src.tui.dashboard import BudaApp
from tests.test_assistant import make_tools


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        tools = make_tools()
        tools.client.get_balances.return_value = [
            {'id': 'btc', 'available_amount': ['0.01', 'BTC'], 'frozen_amount': ['0', 'BTC']}]
        tools.client.get_order_book.return_value = {'asks': [['101', '2']], 'bids': [['100', '3']]}
        tools.client.get_my_orders.return_value = []
        with patch('src.tui.dashboard.GroqClient.from_env', return_value=Mock(api_key='test')):
            return BudaApp(tools.client, tools.registry)

    async def test_balances_navigation_and_commands(self):
        app = self.make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one('#balance-table', DataTable).get_row_at(0), ['BTC', '0.01 BTC', '0 BTC'])
            await pilot.click('#nav-book')
            await pilot.pause()
            self.assertEqual(app.query_one('#book-table', DataTable).row_count, 2)
            await pilot.press('ctrl+l')
            app.query_one('#command', Input).value = '/ordenes'
            await pilot.press('enter')
            await pilot.pause()
            self.assertEqual(app.query_one('#pages', ContentSwitcher).current, 'orders')
            app.client.get_my_orders.assert_called_once_with('btc-clp')

    async def test_form_validates_and_prepares_without_execution(self):
        app = self.make_app()
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            app.show_page('order')
            app.query_one('#amount', Input).value = '-1'
            with patch.object(app, 'review') as review:
                app.prepare_form()
                review.assert_not_called()
                app.query_one('#amount', Input).value = '10000'
                app.prepare_form()
                order = review.call_args.args[0]
                self.assertTrue(order.params['dry_run'])
                self.assertEqual(order.params['currency'], 'btc')
            app.client.create_market_order.assert_not_called()
            app.client.create_limit_order.assert_not_called()
            app.query_one('#currency', Select).value = 'usdc'
            await pilot.pause()
            self.assertEqual(app.query_one('#unit', Select).value, 'clp')
            app.query_one('#strategy', Select).value = 'market'
            await pilot.pause()
            self.assertEqual(app.query_one('#interval', Input).value, '1')

    async def test_failed_refresh_clears_old_data_and_recovers(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.client.get_balances.side_effect = RuntimeError('network')
            app.action_refresh()
            await pilot.pause()
            self.assertEqual(app.query_one('#balance-table', DataTable).row_count, 0)
            self.assertFalse(app.busy)
            app.client.get_balances.side_effect = None
            app.action_refresh()
            await pilot.pause()
            self.assertEqual(app.query_one('#balance-table', DataTable).row_count, 1)

    async def test_voice_stays_in_panel_and_dispatches_only_finished_audio(self):
        from src.tui.voice_screen import VoiceScreen

        for finish in ('enter', 'ctrl+t', 'escape', 'ctrl+c', 'button', 'timeout'):
            with self.subTest(finish=finish):
                app = self.make_app()
                recorder = Mock(MAX_SECONDS=30)
                recorder.finish.return_value = b'wav'
                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.pause()
                    with patch('src.tui.voice_screen.MicrophoneRecorder', return_value=recorder), \
                            patch.object(app, 'suspend') as suspend, \
                            patch.object(app, 'ask_assistant') as ask:
                        await pilot.press('ctrl+t')
                        await pilot.pause()
                        self.assertIsInstance(app.screen, VoiceScreen)
                        recorder.start.assert_called_once()
                        if finish == 'button':
                            await pilot.click('#voice-finish')
                        elif finish == 'timeout':
                            app.screen.started -= 31
                            app.screen.tick()
                            await pilot.pause()
                        else:
                            await pilot.press(finish)
                        await pilot.pause()
                        approved = finish not in ('escape', 'ctrl+c')
                        self.assertEqual(ask.call_count, int(approved))
                        if approved:
                            ask.assert_called_once_with(audio=b'wav')
                        recorder.discard.assert_called_once()
                        suspend.assert_not_called()
                        self.assertTrue(app.is_running)
                        self.assertFalse(app.busy)

    async def test_microphone_failure_is_visible_and_releases_device(self):
        from src.assistant import AssistantError
        from src.tui.voice_screen import VoiceScreen
        from textual.widgets import Static

        app = self.make_app()
        recorder = Mock(MAX_SECONDS=30)
        recorder.start.side_effect = AssistantError('Permiso de micrófono denegado')
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            with patch('src.tui.voice_screen.MicrophoneRecorder', return_value=recorder), \
                    patch.object(app, 'ask_assistant') as ask:
                await pilot.click('#voice')
                await pilot.pause()
                self.assertIsInstance(app.screen, VoiceScreen)
                self.assertIn('Permiso', str(app.screen.query_one('#voice-state', Static).render()))
                await pilot.press('escape')
                await pilot.pause()
                recorder.discard.assert_called_once()
                ask.assert_not_called()
                self.assertFalse(app.busy)

    async def test_transcript_and_response_remain_visible_in_chat(self):
        from textual.widgets import RichLog

        app = self.make_app()
        app.assistant.model.transcribe.return_value = 'Mostrame mi saldo'
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app.assistant, 'reply', return_value='Saldo sintético: 0.01 BTC') as reply:
                await app.ask_assistant(audio=b'wav').wait()
                await pilot.pause()
                self.assertEqual(reply.call_args.args, ('Mostrame mi saldo',))
                self.assertFalse(reply.call_args.kwargs['cancelled'].is_set())
                text = '\n'.join(line.text for line in app.query_one('#chat', RichLog).lines)
                self.assertIn('Dictado: Mostrame mi saldo', text)
                self.assertIn('Saldo sintético', text)
                self.assertFalse(app.busy)

    async def test_cancel_transcription_never_sends_transcript_to_assistant(self):
        import asyncio
        from threading import Event
        from textual.widgets import Static

        app = self.make_app()
        release = Event()
        started = Event()

        def transcribe(audio):
            started.set()
            release.wait(3)
            return 'Texto que se descartó'

        app.assistant.model.transcribe.side_effect = transcribe
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app.assistant, 'reply') as reply:
                worker = app.ask_assistant(audio=b'wav')
                try:
                    await asyncio.to_thread(started.wait, 2)
                    self.assertIn('Transcribiendo', str(app.query_one('#status', Static).render()))
                    await pilot.press('ctrl+c')
                    await pilot.pause()
                    self.assertTrue(worker.is_cancelled)
                    self.assertFalse(app.busy)
                finally:
                    release.set()
                await pilot.pause()
                reply.assert_not_called()
                self.assertTrue(app.is_running)

    async def test_cancel_reply_then_clear_keeps_only_new_conversation(self):
        import asyncio
        from threading import Event
        from textual.widgets import RichLog

        app = self.make_app()
        started, release, finished = Event(), Event(), Event()
        original_reply = app.assistant.reply

        def complete(messages, tools):
            if messages[-1]['content'] == 'Viejo':
                started.set()
                if not release.wait(3):
                    raise TimeoutError('Test did not release request')
                return {'content': 'Respuesta vieja'}
            return {'content': 'Respuesta nueva'}

        def tracked_reply(prompt, **kwargs):
            try:
                return original_reply(prompt, **kwargs)
            finally:
                if prompt == 'Viejo':
                    finished.set()

        app.assistant.model.complete.side_effect = complete
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app.assistant, 'reply', side_effect=tracked_reply):
                worker = app.ask_assistant('Viejo')
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    await pilot.press('ctrl+c')
                    await pilot.pause()
                    self.assertTrue(worker.is_cancelled)
                    self.assertFalse(app.busy)
                    app.query_one('#command', Input).value = '/limpiar'
                    app.action_command()
                    await pilot.press('enter')
                    await app.ask_assistant('Nuevo').wait()
                finally:
                    release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
                self.assertEqual(len(app.assistant.turns), 1)
                self.assertEqual(app.assistant.turns[0][0]['content'], 'Nuevo')
                text = '\n'.join(line.text for line in app.query_one('#chat', RichLog).lines)
                self.assertIn('Respuesta nueva', text)
                self.assertNotIn('Respuesta vieja', text)

    async def test_native_confirmation_requires_explicit_approval(self):
        from unittest.mock import AsyncMock
        from src.tui.order_review import OrderReview

        cases = [(False, False, 'buy'), (True, False, 'buy'),
                 (True, False, 'sell'), (True, True, 'buy')]
        for confirm, from_assistant, side in cases:
            with self.subTest(confirm=confirm, assistant=from_assistant, side=side):
                app = self.make_app()
                order = app.tools.execute('preparar_orden', dict(
                    side=side, currency='btc',
                    amount='10000' if side == 'buy' else '0.001',
                    unit='clp' if side == 'buy' else 'btc', strategy='market', dry_run=True))
                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.pause()
                    with patch.object(app, 'execute_order', new_callable=AsyncMock) as execute:
                        if from_assistant:
                            with patch.object(app.assistant, 'reply', return_value=order):
                                worker = app.ask_assistant('Compra en simulación')
                                await pilot.pause()
                        else:
                            worker = app.review(order)
                            await pilot.pause()
                        self.assertIsInstance(app.screen, OrderReview)
                        execute.assert_not_called()
                        if confirm:
                            await pilot.click('#confirm-order')
                        else:
                            await pilot.press('enter')
                        await worker.wait()
                        self.assertEqual(execute.await_count, int(confirm))
                        if confirm:
                            self.assertEqual(execute.call_args.args[0]['amount'], order.params['amount'])
                            self.assertEqual(execute.call_args.args[0]['side'], side)
                    self.assertFalse(app.busy)
                    app.client.create_market_order.assert_not_called()

    async def test_stop_and_quit_keep_panel_open_until_cleanup_finishes(self):
        import asyncio
        from textual.widgets import Button

        for key in ('ctrl+c', 'ctrl+q', 'button'):
            with self.subTest(key=key):
                app = self.make_app()
                stopping = asyncio.Event()
                cleaned = asyncio.Event()

                class RunningOrder:
                    stop_requested = False

                    def stop(self):
                        self.stop_requested = True
                        stopping.set()

                    async def run(self, params, market, on_line):
                        on_line('Orden activa: synthetic-1')
                        await cleaned.wait()
                        on_line('Cancelación completada: synthetic-1')
                        return 0

                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.pause()
                    app.busy = True
                    with patch('src.tui.dashboard.OrderProcess', RunningOrder):
                        params = app.tools.execute('preparar_orden', dict(
                            side='buy', currency='btc', amount='10000', unit='clp',
                            strategy='top')).params
                        task = asyncio.create_task(app.execute_order(params))
                        await pilot.pause()
                        self.assertEqual(app.query_one('#pages', ContentSwitcher).current, 'execution')
                        if key == 'button':
                            await pilot.click('#stop-order')
                        else:
                            await pilot.press(key)
                        self.assertTrue(stopping.is_set())
                        self.assertFalse(task.done())
                        self.assertTrue(app.is_running)
                        cleaned.set()
                        await task
                        self.assertTrue(app.is_running)
                        self.assertIsNone(app.order_process)
                        self.assertTrue(app.query_one('#stop-order', Button).disabled)
                        app.busy = False
                        await pilot.click('#nav-order')
                        self.assertEqual(app.query_one('#pages', ContentSwitcher).current, 'order')
