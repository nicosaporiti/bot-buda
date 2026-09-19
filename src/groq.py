"""Groq HTTP client with explicit timeouts and no automatic retries."""
import os

import requests

from .assistant import AssistantError
from .config import load_env_file


# Whisper uses a short vocabulary/context hint, not chat-style instructions.
TRANSCRIPTION_CONTEXT = (
    'Conversación en español sobre criptomonedas en Buda. '
    'Bitcoin (BTC, be te ce), Ethereum (ETH), Litecoin (LTC), Bitcoin Cash (BCH). '
    'USD Coin (USDC, u ese de ce), Tether (USDT, u ese de te). '
    'USD son dólares; CLP pesos chilenos, COP pesos colombianos, PEN soles. '
    'Saldo disponible, comprar, vender, libro de órdenes, top, depth, market.'
)


class GroqClient:
    BASE_URL = 'https://api.groq.com/openai/v1'

    def __init__(self, api_key: str, model: str = 'openai/gpt-oss-20b'):
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls):
        settings = load_env_file()
        return cls(os.environ.get('GROQ_API_KEY', settings.get('GROQ_API_KEY', '')).strip(),
                   os.environ.get('GROQ_MODEL', settings.get('GROQ_MODEL', 'openai/gpt-oss-20b')).strip())

    def _post(self, path: str, **kwargs) -> dict:
        if not self.api_key:
            raise AssistantError('Configurá GROQ_API_KEY en .env para usar el asistente. Los menús manuales siguen disponibles.')
        try:
            response = requests.post(f'{self.BASE_URL}/{path}',
                                     headers={'Authorization': f'Bearer {self.api_key}'},
                                     timeout=(5, 30), **kwargs)
        except requests.RequestException:
            raise AssistantError('No se pudo conectar con Groq. No se reintentó automáticamente.') from None
        if response.status_code in (401, 403):
            raise AssistantError('Groq rechazó el acceso. Revisá GROQ_API_KEY y los permisos del modelo.')
        if response.status_code == 429:
            raise AssistantError('Se alcanzó la cuota de Groq. Esperá o usá los menús manuales.')
        if response.status_code != 200:
            raise AssistantError(f'Groq no pudo responder (HTTP {response.status_code}).')
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise AssistantError('Groq devolvió una respuesta inválida.') from None

    def complete(self, messages: list, tools: list) -> dict:
        data = self._post('chat/completions', json={
            'model': self.model, 'messages': messages, 'tools': tools,
            'tool_choice': 'auto', 'parallel_tool_calls': False,
            'max_completion_tokens': 1024,
        })
        try:
            choice = data['choices'][0]
            message = choice['message']
            if choice['finish_reason'] not in ('stop', 'tool_calls') or not isinstance(message, dict):
                raise ValueError
            return message
        except (KeyError, IndexError, TypeError, ValueError):
            raise AssistantError('Groq devolvió una respuesta incompleta. No se ejecutó esa respuesta.') from None

    def transcribe(self, audio: bytes) -> str:
        data = self._post('audio/transcriptions',
                          files={'file': ('command.wav', audio, 'audio/wav')},
                          data={'model': 'whisper-large-v3-turbo', 'language': 'es',
                                'response_format': 'json', 'temperature': '0',
                                'prompt': TRANSCRIPTION_CONTEXT})
        text = data.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise AssistantError('No se obtuvo un dictado válido. Volvé a grabar o escribí el pedido.')
        return text.strip()
