"""Captura acotada en memoria; no abre el micrófono hasta pulsar Hablar."""
import io
import wave

from .assistant import AssistantError


class MicrophoneRecorder:
    SAMPLE_RATE = 16000
    MAX_SECONDS = 30

    def __init__(self, *, backend=None):
        self.backend = backend
        self.stream = None
        self.audio = bytearray()
        self.overflow = False

    def start(self):
        if self.backend is None:
            try:
                import sounddevice
            except (ImportError, OSError):
                raise AssistantError('No está disponible el audio. Instalá con pip install -r requirements-voice.txt; en Linux también necesitás PortAudio.') from None
            self.backend = sounddevice
        self.audio.clear()
        self.overflow = False
        try:
            self.stream = self.backend.RawInputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype='int16', callback=self.capture)
            self.stream.start()
        except Exception:
            self.close()
            raise AssistantError('No se pudo abrir el micrófono. Revisá el dispositivo y el permiso de micrófono de tu terminal en el sistema.') from None

    def capture(self, data, frames, time, status):
        self.overflow |= bool(status)
        remaining = self.SAMPLE_RATE * self.MAX_SECONDS * 2 - len(self.audio)
        self.audio.extend(bytes(data)[:remaining])
        if len(self.audio) >= self.SAMPLE_RATE * self.MAX_SECONDS * 2:
            raise self.backend.CallbackStop

    def close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                try:
                    stream.stop()
                finally:
                    stream.close()
            except Exception:
                raise AssistantError("Se interrumpió la captura del micrófono. Volvé a grabar.") from None

    def discard(self):
        try:
            self.close()
        finally:
            self.audio.clear()

    def finish(self):
        self.close()
        audio = bytes(self.audio)
        self.audio.clear()
        if self.overflow:
            raise AssistantError('La captura de audio se interrumpió. Volvé a grabar.')
        if len(audio) < self.SAMPLE_RATE or not any(audio):
            raise AssistantError('No se capturó audio suficiente. Hablá al menos medio segundo y volvé a intentar.')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(audio)
        return output.getvalue()
