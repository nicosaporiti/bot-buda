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

    def test_main_menu_routes_shortcut_to_immediate_voice(self):
        from src.tui.app import launch_tui
        tools = make_tools()
        config = Mock(quote_currency='clp')
        with patch('src.tui.app.Config.load', return_value=config), \
                patch('src.tui.app.BudaClient', return_value=tools.client), \
                patch('src.tui.app.MarketRegistry', return_value=tools.registry), \
                patch('src.tui.app.Console', return_value=Console(file=io.StringIO())), \
                patch('src.tui.app.prompt_main_menu', side_effect=['voice', 'exit']), \
                patch('src.tui.assistant.launch_assistant') as launch:
            self.assertEqual(launch_tui(), 0)
            self.assertTrue(launch.call_args.kwargs['start_with_voice'])
