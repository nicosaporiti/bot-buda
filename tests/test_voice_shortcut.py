"""Real terminal shortcut and direct voice entry regressions."""
import io
import unittest
from unittest.mock import Mock, patch

from InquirerPy import inquirer
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from src.tui.prompts import execute_with_voice_shortcut, prompt_main_menu
from src.tui.assistant import launch_assistant
from tests.test_assistant import make_tools


class VoiceShortcutTests(unittest.TestCase):
    def test_ctrl_t_from_main_menu_and_assistant_selection(self):
        for prompt in (prompt_main_menu, lambda: execute_with_voice_shortcut(
                inquirer.select(message='Asistente', choices=['text', 'back']))):
            with self.subTest(prompt=prompt), create_pipe_input() as pipe:
                with create_app_session(input=pipe, output=DummyOutput()):
                    pipe.send_text('\x14')
                    self.assertEqual(prompt(), 'voice')

    def test_enter_keeps_normal_menu_selection(self):
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                pipe.send_text('\r')
                self.assertEqual(prompt_main_menu(), 'buy')

    def test_direct_entry_records_before_showing_assistant_menu(self):
        tools = make_tools()
        model = Mock(api_key='test')
        model.transcribe.return_value = 'saldo bitcoin'
        model.complete.return_value = {'content': 'Respuesta'}
        events = []
        def record():
            events.append('record')
            return b'wav'
        def menu(prompt):
            events.append('menu')
            return 'back'
        with patch('src.tui.assistant.GroqClient.from_env', return_value=model), \
                patch('src.tui.assistant.record_voice', side_effect=record), \
                patch('src.tui.assistant.execute_with_voice_shortcut', side_effect=menu), \
                patch('src.tui.assistant.inquirer.select'):
            launch_assistant(Console(file=io.StringIO()), tools.client, tools.registry,
                             start_with_voice=True)
        self.assertEqual(events, ['record', 'menu'])
        model.transcribe.assert_called_once_with(b'wav')

    def test_launch_opens_full_screen_account(self):
        from src.tui.app import launch_tui
        tools = make_tools()
        config = Mock(quote_currency='clp')
        with patch('src.tui.app.Config.load', return_value=config), \
                patch('src.tui.app.BudaClient', return_value=tools.client), \
                patch('src.tui.app.MarketRegistry', return_value=tools.registry), \
                patch('src.tui.dashboard.BudaApp') as app:
            self.assertEqual(launch_tui(), 0)
            app.assert_called_once_with(tools.client, tools.registry)
            app.return_value.run.assert_called_once()

    def test_grid_prompt_runs_outside_dashboard_event_loop(self):
        import asyncio
        from src.tui.app import launch_tui

        tools = make_tools()

        def grid(console, client, registry):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            # Exercise a real InquirerPy prompt with cancellation by default.
            with create_pipe_input() as pipe:
                with create_app_session(input=pipe, output=DummyOutput()):
                    pipe.send_text('\r')
                    self.assertFalse(inquirer.confirm(message='Confirmar', default=False).execute())

        with patch('src.tui.app.Config.load', return_value=Mock(quote_currency='clp')), \
                patch('src.tui.app.BudaClient', return_value=tools.client), \
                patch('src.tui.app.MarketRegistry', return_value=tools.registry), \
                patch('src.tui.dashboard.BudaApp') as app, \
                patch('src.tui.app._handle_grid', side_effect=grid) as handler:
            app.return_value.run.side_effect = ['grid', None]
            self.assertEqual(launch_tui(), 0)
            handler.assert_called_once()
            self.assertEqual(app.return_value.run.call_count, 2)
