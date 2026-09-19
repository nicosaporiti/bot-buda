"""Text/voice menu and keyboard-only order review for the existing TUI."""
import asyncio
from decimal import Decimal, DecimalException

from InquirerPy import inquirer
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

from ..api import BudaAPIError
from ..assistant import Assistant, AssistantError, AssistantTools, PreparedOrder
from ..groq import GroqClient
from ..voice import MicrophoneRecorder
from .prompts import execute_with_voice_shortcut


def record_voice() -> bytes | None:
    recorder = MicrophoneRecorder()
    bindings = KeyBindings()

    @bindings.add('enter')
    @bindings.add('c-t')
    def finish(event):
        event.app.exit(result=True)

    @bindings.add('escape')
    @bindings.add('c-c')
    def cancel(event):
        event.app.exit(result=False)

    session = PromptSession(key_bindings=bindings)

    async def stop_at_limit():
        await asyncio.sleep(recorder.MAX_SECONDS)
        if not session.app.is_done:
            session.app.exit(result=True)

    try:
        recorder.start()
        finished = session.prompt(
            'Grabando (máx. 30 s). Enter/Ctrl+T termina; Escape/Ctrl+C descarta: ',
            pre_run=lambda: session.app.create_background_task(stop_at_limit()),
        )
        return recorder.finish() if finished else None
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        recorder.discard()


def review_order(console, client, registry, order: PreparedOrder) -> None:
    """Only this UI path can dispatch an assistant-prepared order."""
    from .app import _resolve_amount, _run_bot
    from .display import print_order_summary

    params = dict(order.params)
    market = registry.get_by_currency(params['currency'])
    console.print(f"Pedido: {params['raw_amount']} {params['source_unit'].upper()} · "
                  f"Mercado: {market.market_id.upper()}", markup=False)
    if not _resolve_amount(console, client, registry, params):
        return
    amount = Decimal(str(params['amount']))
    if not amount.is_finite() or amount <= 0:
        raise AssistantError('La conversión no produjo una cantidad positiva válida.')
    native_unit = market.quote_currency if params['side'] == 'buy' else market.base_currency
    params['converted_display'] = f'{amount} {native_unit.upper()}'
    print_order_summary(console, params)
    if not inquirer.confirm(message='Confirmar y ejecutar esta operación?', default=False).execute():
        console.print('Operación descartada.', markup=False)
        return
    _run_bot(console, client, registry, params)


def launch_assistant(console, client, registry, *, start_with_voice=False) -> None:
    model = GroqClient.from_env()
    if not model.api_key:
        console.print('Configurá GROQ_API_KEY en .env. Los menús manuales siguen disponibles.', markup=False)
        return
    assistant = Assistant(model, AssistantTools(client, registry))
    console.print(
        'Asistente Buda · texto y voz · Ctrl+T: hablar\n'
        'Groq recibe el pedido, hasta tres intercambios recientes y los datos consultados. '
        'El audio se envía al terminar. No guardamos audio ni historial en disco.\n'
        'Las operaciones requieren revisión y confirmación por teclado. '
        'Sin modo real explícito, se prepara una simulación.\n'
        'Durante una estrategia el asistente queda en pausa. Ctrl+C detiene el bot. '
        'Grillas y control de estrategias se manejan desde el menú principal.', markup=False,
    )
    pending_voice = start_with_voice
    while True:
        try:
            if pending_voice:
                action = 'voice'
                pending_voice = False
            else:
                action = execute_with_voice_shortcut(inquirer.select(
                    message='Asistente (Ctrl+T: hablar):', choices=[
                        {'name': 'Escribir pedido', 'value': 'text'},
                        {'name': 'Hablar', 'value': 'voice'},
                        {'name': 'Limpiar conversación', 'value': 'clear'},
                        {'name': 'Volver al menú', 'value': 'back'},
                    ],
                ))
            if action == 'back':
                return
            if action == 'clear':
                assistant.clear()
                console.print('Conversación borrada.', markup=False)
                continue
            if action == 'voice':
                audio = record_voice()
                if audio is None:
                    continue
                console.print('Transcribiendo con Groq… Ctrl+C cancela la espera.', markup=False)
                try:
                    prompt = model.transcribe(audio)
                finally:
                    del audio
                console.print(f'Dictado: {prompt}', markup=False)
            else:
                prompt = inquirer.text(message='Pedido:').execute()
            if not prompt or not prompt.strip():
                continue
            console.print('Consultando asistente…', markup=False)
            result = assistant.reply(prompt)
            if isinstance(result, PreparedOrder):
                try:
                    review_order(console, client, registry, result)
                finally:
                    # Do not retain an actionable instruction after approval or cancellation.
                    assistant.clear()
            else:
                console.print(result, markup=False)
        except (KeyboardInterrupt, EOFError):
            return
        except AssistantError as error:
            console.print(str(error), markup=False)
        except BudaAPIError:
            console.print('Falló la consulta o ejecución en Buda. Revisá el estado antes de repetir una operación.', markup=False)
        except (DecimalException, KeyError, TypeError, ValueError):
            console.print('Los datos recibidos no permiten completar el pedido. No se reintentó.', markup=False)
