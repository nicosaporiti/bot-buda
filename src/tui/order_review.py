"""Keyboard confirmation inside the account interface."""
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class OrderReview(ModalScreen[bool]):
    CSS = '''
    OrderReview { align: center middle; }
    #order-review { width: 80; max-width: 95%; height: 85%; padding: 1 2;
                    border: round $accent; background: $surface; }
    #review-text { height: 1fr; }
    #review-buttons { height: 3; }
    '''
    BINDINGS = [('escape', 'cancel', 'Volver'), ('ctrl+c', 'cancel', 'Cancelar')]

    def __init__(self, summary):
        super().__init__()
        self.summary = summary

    def compose(self):
        with Vertical(id='order-review'):
            with VerticalScroll(id='review-text'):
                yield Static(self.summary, markup=False)
            with Horizontal(id='review-buttons'):
                yield Button('Volver', id='cancel-order')
                yield Button('Confirmar y ejecutar', id='confirm-order', variant='primary')

    def on_mount(self):
        self.query_one('#cancel-order', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id in ('cancel-order', 'confirm-order'):
            self.dismiss(event.button.id == 'confirm-order')
