"""Bounded microphone capture without leaving the Textual interface."""
from time import monotonic

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..assistant import AssistantError
from ..voice import MicrophoneRecorder


class VoiceScreen(ModalScreen[bytes | None]):
    CSS = '''
    VoiceScreen { align: center middle; }
    #voice-dialog { width: 76; max-width: 95%; height: auto; padding: 1 2;
                    border: round $accent; background: $surface; }
    #voice-state { height: auto; margin: 1 0; }
    #voice-buttons { height: 3; margin-top: 1; }
    '''
    BINDINGS = [Binding('enter,ctrl+t', 'finish', 'Terminar', priority=True),
                Binding('escape,ctrl+c,ctrl+q', 'cancel', 'Descartar', priority=True)]

    def __init__(self):
        super().__init__()
        self.recorder = MicrophoneRecorder()
        self.recording = False
        self.released = False

    def compose(self):
        with Vertical(id='voice-dialog'):
            yield Static('Asistente · Dictado de voz', classes='heading')
            yield Static('Abriendo micrófono…', id='voice-state', markup=False)
            yield Static('Enter o Ctrl+T: terminar y transcribir. Escape o Ctrl+C: descartar.\n'
                         'El audio se envía a Groq al terminar; no se guarda en disco.', markup=False)
            with Horizontal(id='voice-buttons'):
                yield Button('Descartar', id='voice-cancel')
                yield Button('Terminar y transcribir', id='voice-finish', variant='primary')

    def on_mount(self):
        self.query_one('#voice-cancel', Button).focus()
        try:
            self.recorder.start()
            self.recording = True
            self.started = monotonic()
            self.tick()
            self.set_interval(0.2, self.tick)
        except Exception as error:
            self.show_error(error)

    def tick(self):
        if not self.recording:
            return
        elapsed = monotonic() - self.started
        if elapsed >= self.recorder.MAX_SECONDS:
            self.action_finish()
        else:
            self.query_one('#voice-state', Static).update(
                f'Grabando… {int(elapsed)} / {self.recorder.MAX_SECONDS} segundos. Hablá ahora.')

    def release(self):
        if not self.released:
            self.released = True
            self.recorder.discard()

    def show_error(self, error):
        self.recording = False
        try:
            self.release()
        except AssistantError:
            pass
        message = str(error) if isinstance(error, AssistantError) else 'No se pudo grabar. Revisá el micrófono y sus permisos.'
        self.query_one('#voice-state', Static).update(message)
        self.query_one('#voice-finish', Button).disabled = True

    def action_finish(self):
        if not self.recording:
            return
        self.recording = False
        try:
            audio = self.recorder.finish()
            self.release()
        except Exception as error:
            self.show_error(error)
            return
        self.dismiss(audio)

    def action_cancel(self):
        self.recording = False
        try:
            self.release()
        except AssistantError:
            pass
        self.dismiss(None)

    def on_unmount(self):
        try:
            self.release()
        except AssistantError:
            pass

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'voice-finish':
            self.action_finish()
        elif event.button.id == 'voice-cancel':
            self.action_cancel()
