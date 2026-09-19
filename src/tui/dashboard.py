"""Full-screen account interface, sharing Buda's validated trading flows."""
import asyncio
import io
from threading import Event

from rich.console import Console
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.worker import get_current_worker
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, DataTable, Footer, Header, Input, Label, RichLog, Select, Static, Switch

from ..assistant import Assistant, AssistantError, AssistantTools, PreparedOrder
from ..groq import GroqClient
from .order_process import OrderProcess
from .order_review import OrderReview
from .voice_screen import VoiceScreen


class BudaApp(App):
    TITLE = 'Buda · Mi cuenta'
    CSS = '''
    #main { height: 1fr; }
    #navigation { overflow-y: auto; width: 24; padding: 1; border-right: solid $accent; }
    #navigation Button { width: 100%; margin-bottom: 1; }
    ContentSwitcher { width: 1fr; padding: 1 2; }
    .heading { text-style: bold; margin-bottom: 1; }
    .hint { height: auto; margin-bottom: 1; color: $text-muted; }
    DataTable { height: 1fr; min-height: 5; margin-bottom: 1; }
    #order Input, #order Select { margin-bottom: 1; }
    #status { height: auto; max-height: 3; padding: 0 1; color: $text-muted; }
    #command-bar { height: 3; }
    #command { height: 3; width: 1fr; }
    #voice { width: 14; min-width: 14; }
    #chat, #execution-log { height: 1fr; }
    #execution-status { height: auto; margin-bottom: 1; }
    '''
    BINDINGS = [Binding('ctrl+c', 'stop_order', 'Detener', priority=True),
                ('ctrl+q', 'quit', 'Salir'), ('ctrl+r', 'refresh', 'Actualizar'),
                ('ctrl+l', 'command', 'Comando'), ('ctrl+t', 'voice', 'Hablar')]
    PAGES = [('balances', 'Balances'), ('order', 'Nueva orden'),
             ('orders', 'Mis órdenes'), ('execution', 'Proceso'), ('book', 'Libro de órdenes'),
             ('grid', 'Grilla'), ('assistant', 'Asistente · AI')]

    def __init__(self, client, registry):
        super().__init__()
        self.client = client
        self.registry = registry
        self.tools = AssistantTools(client, registry)
        self.assistant = Assistant(GroqClient.from_env(), self.tools)
        self.busy = False
        self.order_process = None
        self.assistant_worker = None
        self.sub_title = f'Tu cuenta Buda · {registry.quote_currency.upper()}'

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='main'):
            with Vertical(id='navigation'):
                for page, title in self.PAGES:
                    yield Button(title, id=f'nav-{page}', variant='primary' if page == 'balances' else 'default')
            with ContentSwitcher(initial='balances', id='pages'):
                with Vertical(id='balances'):
                    yield Label('Mis balances', classes='heading')
                    yield Static('Saldos disponibles y reservados de tu cuenta. Cada activo conserva su moneda.', classes='hint')
                    yield DataTable(id='balance-table', cursor_type='row')
                    yield Button('Actualizar balances', id='refresh-balances')
                with VerticalScroll(id='order'):
                    yield Label('Nueva orden', classes='heading')
                    yield Label('Operación')
                    yield Select([('Comprar', 'buy'), ('Vender', 'sell')], value='buy', allow_blank=False, id='side')
                    yield Label('Criptomoneda')
                    currencies = self.registry.currencies()
                    yield Select([(c.upper(), c) for c in currencies], value=currencies[0], allow_blank=False, id='currency')
                    yield Label('Importe')
                    yield Input(placeholder='Ej.: 10000', id='amount')
                    yield Label('Unidad del importe')
                    yield Select(self.unit_options(currencies[0]), value=self.registry.quote_currency, allow_blank=False, id='unit')
                    yield Label('Estrategia')
                    yield Select([('Top of book', 'top'), ('Profundidad', 'depth'), ('Mercado', 'market')], value='top', allow_blank=False, id='strategy')
                    yield Label('Ratio de profundidad (sólo para depth)')
                    yield Input(value='0.9', id='depth')
                    yield Label('Intervalo de monitoreo (segundos)')
                    yield Input(value='30', id='interval')
                    yield Label('Simulación (desactivar para operar en modo real)')
                    yield Switch(value=True, id='dry-run')
                    yield Static('Revisá y confirmá la orden aquí. Seguí su ejecución en Proceso; Detener o Ctrl+C solicita la cancelación sin cerrar la interfaz.', classes='hint')
                    yield Button('Revisar orden', id='review', variant='primary')
                for page, title in [('orders', 'Mis órdenes'), ('book', 'Libro de órdenes')]:
                    with Vertical(id=page):
                        yield Label(title, classes='heading')
                        yield Select([(m.upper(), m) for m in self.registry.market_ids()], value=self.registry.market_ids()[0], allow_blank=False, id=f'{page}-market')
                        yield Static('Última consulta manual por mercado.', classes='hint')
                        yield DataTable(id=f'{page}-table', cursor_type='row')
                        yield Button(f'Actualizar {title.lower()}', id=f'refresh-{page}')
                with Vertical(id='execution'):
                    yield Label('Proceso de la orden', classes='heading')
                    yield Static('Sin ejecución activa.', id='execution-status', markup=False)
                    yield RichLog(id='execution-log', wrap=True, markup=False, highlight=False, max_lines=2000)
                    yield Button('Detener orden', id='stop-order', variant='error', disabled=True)
                with Vertical(id='grid'):
                    yield Label('Estrategia de grilla', classes='heading')
                    yield Static('Configurá el rango, niveles y presupuesto en el asistente de terminal. Revisá el resumen antes de confirmar. Ctrl+C detiene la grilla y vuelve a esta pantalla.', classes='hint')
                    yield Button('Configurar grilla', id='start-grid', variant='primary')
                with Vertical(id='assistant'):
                    yield Label('Asistente Buda · texto y voz', classes='heading')
                    yield Static('Groq recibe el pedido y los datos consultados. Historial sólo en memoria. Las operaciones requieren revisión por teclado. /limpiar borra la conversación.', classes='hint')
                    yield RichLog(id='chat', wrap=True, markup=False, highlight=False, max_lines=500)
        yield Static('Listo', id='status', markup=False)
        with Horizontal(id='command-bar'):
            yield Input(placeholder='Escribí al asistente o usá /balances · /orden · /libro · /ayuda', id='command')
            yield Button('🎙 Hablar', id='voice')
        yield Footer()

    def unit_options(self, currency):
        units = [self.registry.quote_currency, currency]
        if self.registry.has_market(f'usdc-{self.registry.quote_currency}'):
            units.append('usd')
        return [(unit.upper(), unit) for unit in units]

    def on_mount(self):
        for table, columns in [('balance', ('Moneda', 'Disponible', 'Reservado')),
                               ('orders', ('ID', 'Operación', 'Cantidad', 'Precio', 'Estado')),
                               ('book', ('Lado', 'Precio', 'Cantidad'))]:
            self.query_one(f'#{table}-table', DataTable).add_columns(*columns)
        self.refresh_page('balances')

    def status(self, text):
        self.query_one('#status', Static).update(text)

    def show_page(self, page):
        self.query_one('#pages', ContentSwitcher).current = page
        for key, _ in self.PAGES:
            self.query_one(f'#nav-{key}', Button).variant = 'primary' if key == page else 'default'
        if page in ('balances', 'orders', 'book'):
            self.refresh_page(page)

    @staticmethod
    def amount_text(value):
        return ' '.join(str(part) for part in value) if isinstance(value, list) else str(value)

    @work
    async def refresh_page(self, page):
        if self.busy:
            return
        self.busy = True
        self.status('Consultando…')
        table = self.query_one(f'#{"balance" if page == "balances" else page}-table', DataTable)
        try:
            rows = []
            if page == 'balances':
                for balance in await asyncio.to_thread(self.client.get_balances):
                    available = balance.get('available_amount', ['0', ''])
                    currency = balance.get('id') or (available[1] if isinstance(available, list) and len(available) > 1 else '—')
                    rows.append((str(currency).upper(), self.amount_text(available), self.amount_text(balance.get('frozen_amount', '—'))))
            else:
                market = self.query_one(f'#{page}-market', Select).value
                if page == 'book':
                    book = await asyncio.to_thread(self.client.get_order_book, market)
                    for side, key in [('Venta', 'asks'), ('Compra', 'bids')]:
                        rows.extend((side, str(price), str(amount)) for price, amount, *_ in book.get(key, [])[:10])
                else:
                    orders = await asyncio.to_thread(self.client.get_my_orders, market)
                    rows = [tuple(self.amount_text(order.get(key, '—')) for key in ('id', 'type', 'amount', 'limit', 'state')) for order in orders]
                if market != self.query_one(f'#{page}-market', Select).value:
                    table.clear()
                    self.status('Mercado cambiado. Actualizá para consultar.')
                    return
            table.clear()
            table.add_rows(rows)
            self.status('Listo' if rows else 'Sin datos para mostrar.')
        except Exception:
            table.clear()
            self.status('No se pudo consultar Buda. Revisá la conexión y actualizá para reintentar.')
        finally:
            self.busy = False

    def on_select_changed(self, event):
        if event.select.id == 'currency':
            unit = self.query_one('#unit', Select)
            unit.set_options(self.unit_options(event.value))
            unit.value = self.registry.quote_currency
        elif event.select.id == 'strategy':
            self.query_one('#interval', Input).value = '1' if event.value == 'market' else '30'
        elif event.select.id in ('orders-market', 'book-market'):
            page = event.select.id.removesuffix('-market')
            self.query_one(f'#{page}-table', DataTable).clear()
            if self.query_one('#pages', ContentSwitcher).current == page:
                self.refresh_page(page)

    def on_button_pressed(self, event):
        key = event.button.id or ''
        if key.startswith('nav-'):
            self.show_page(key[4:])
        elif key.startswith('refresh-'):
            self.refresh_page(key[8:])
        elif key == 'review' and not self.busy:
            self.prepare_form()
        elif key == 'stop-order':
            self.action_stop_order()
        elif key == 'voice':
            self.action_voice()
        elif key == 'start-grid' and not self.busy:
            self.exit(result='grid')

    def prepare_form(self):
        try:
            args = {key: self.query_one(f'#{key}', Select).value for key in ('side', 'currency', 'unit', 'strategy')}
            args.update(amount=self.query_one('#amount', Input).value.strip(),
                        interval=int(self.query_one('#interval', Input).value),
                        dry_run=self.query_one('#dry-run', Switch).value)
            if args['strategy'] == 'depth':
                args['depth_ratio'] = self.query_one('#depth', Input).value.strip()
            self.review(self.tools.execute('preparar_orden', args))
        except (AssistantError, ValueError) as error:
            self.status(str(error))

    @work
    async def review(self, order):
        if not self.busy:
            await self.review_in_panel(order)

    async def review_in_panel(self, order):
        from .assistant import prepare_review

        was_busy = self.busy
        self.busy = True
        try:
            self.status('Preparando revisión…')
            output = io.StringIO()
            console = Console(file=output, color_system=None, width=70)
            params = await asyncio.to_thread(
                prepare_review, console, self.client, self.registry, order,
            )
            if params is None:
                raise AssistantError('No se pudo convertir el importe. No se envió la orden.')
            approved = await self.push_screen_wait(OrderReview(output.getvalue()))
            if approved:
                await self.execute_order(params)
            else:
                self.status('Operación descartada. No se envió la orden.')
        except AssistantError as error:
            self.status(str(error))
        except Exception as error:
            self.status(f'No se completó la operación ({type(error).__name__}). '
                        'Consultá las órdenes antes de repetir.')
        finally:
            self.assistant.clear()
            self.busy = was_busy

    async def execute_order(self, params):
        self.show_page('execution')
        log = self.query_one('#execution-log', RichLog)
        log.clear()
        market = self.registry.get_by_currency(params['currency'])
        mode = 'SIMULACIÓN' if params['dry_run'] else 'REAL'
        log.write(f"{mode} · {params['side']} · {market.market_id.upper()} · {params['strategy']}")
        self.order_process = OrderProcess()
        self.query_one('#stop-order', Button).disabled = False
        self.execution_status('Orden en ejecución. Detener o Ctrl+C solicita la limpieza del bot.')
        try:
            code = await self.order_process.run(params, market, log.write)
            if code:
                message = 'Ejecución con errores. Revisá el registro y las órdenes antes de repetir.'
            elif self.order_process.stop_requested:
                message = 'Proceso detenido. Revisá el resumen y los avisos de cancelación.'
            else:
                message = 'Proceso finalizado. El resultado está en el registro.'
            self.execution_status(message)
        except Exception as error:
            self.execution_status(f'Error de seguimiento ({type(error).__name__}). Revisá las órdenes antes de repetir.')
            raise
        finally:
            self.order_process = None
            self.query_one('#stop-order', Button).disabled = True

    def execution_status(self, text):
        self.query_one('#execution-status', Static).update(text)
        self.status(text)

    def action_stop_order(self):
        if self.order_process is not None:
            self.order_process.stop()
            self.query_one('#stop-order', Button).disabled = True
            self.execution_status('Deteniendo… Esperando la limpieza del bot. La interfaz permanecerá abierta.')
        elif isinstance(self.screen, (OrderReview, VoiceScreen)):
            self.screen.action_cancel()
        elif self.assistant_worker is not None:
            self.assistant_worker.cancel()

    async def action_quit(self):
        if self.order_process is not None:
            self.action_stop_order()
        elif self.busy:
            self.status('Esperá a que termine la operación antes de salir.')
        else:
            await super().action_quit()

    def action_refresh(self):
        page = self.query_one('#pages', ContentSwitcher).current
        if page in ('balances', 'orders', 'book'):
            self.refresh_page(page)

    def action_command(self):
        self.query_one('#command', Input).focus()

    def on_input_submitted(self, event):
        if event.input.id != 'command' or self.busy:
            return
        prompt = event.value.strip()
        event.input.value = ''
        commands = {'/balances': 'balances', '/orden': 'order', '/ordenes': 'orders',
                    '/libro': 'book', '/proceso': 'execution', '/grilla': 'grid', '/asistente': 'assistant'}
        if prompt in commands:
            self.show_page(commands[prompt])
        elif prompt == '/limpiar':
            self.assistant.clear()
            self.query_one('#chat', RichLog).clear()
            self.status('Conversación borrada.')
        elif prompt.startswith('/'):
            self.status('/balances · /orden · /ordenes · /libro · /grilla · /asistente · /limpiar')
        elif prompt:
            self.ask_assistant(prompt)

    @work
    async def ask_assistant(self, prompt=None, audio=None):
        if self.busy:
            return
        self.busy = True
        self.assistant_worker = get_current_worker()
        cancelled = Event()
        self.show_page('assistant')
        log = self.query_one('#chat', RichLog)
        self.status('Consultando asistente…')
        try:
            if audio is not None:
                self.status('Transcribiendo audio… Ctrl+C cancela la espera.')
                prompt = await asyncio.to_thread(self.assistant.model.transcribe, audio)
                log.write(f'Dictado: {prompt}')
            else:
                log.write(f'Vos: {prompt}')
            self.status('Consultando asistente… Ctrl+C cancela la espera.')
            result = await asyncio.to_thread(self.assistant.reply, prompt, cancelled=cancelled)
            self.status('Listo')
            if isinstance(result, PreparedOrder):
                await self.review_in_panel(result)
                log.write('Revisión terminada. Consultá Proceso para ver el resultado y Mis órdenes para verificar operaciones reales.')
            else:
                log.write(f'Asistente: {result}')
        except asyncio.CancelledError:
            cancelled.set()
            self.status('Consulta cancelada. Los datos enviados pueden haber llegado a Groq.')
            raise
        except AssistantError as error:
            self.status(str(error))
        except Exception:
            self.status('No se pudo completar el pedido. Revisá la conexión y GROQ_API_KEY.')
        finally:
            self.assistant_worker = None
            self.busy = False

    def action_voice(self):
        if isinstance(self.screen, VoiceScreen):
            self.screen.action_finish()
        elif not self.busy:
            self.capture_voice()

    @work
    async def capture_voice(self):
        if self.busy:
            return
        self.busy = True
        self.show_page('assistant')
        self.status('Dictado de voz')
        try:
            audio = await self.push_screen_wait(VoiceScreen())
        finally:
            self.busy = False
        if audio is None:
            self.status('Dictado descartado. No se envió audio.')
        else:
            self.ask_assistant(audio=audio)
