"""Synthetic PCM and fake microphone lifecycle tests."""
import io
import unittest
import wave
from unittest.mock import Mock, patch

from src.assistant import AssistantError
from src.voice import MicrophoneRecorder
from src.tui.assistant import record_voice


class VoiceTests(unittest.TestCase):
    def recorder(self):
        backend = Mock()
        backend.CallbackStop = type('CallbackStop', (Exception,), {})
        return MicrophoneRecorder(backend=backend)

    def test_wav_is_mono_16khz_and_buffer_is_cleared(self):
        recorder = self.recorder()
        recorder.start()
        recorder.capture(b'\x01\x00' * 8000, 8000, None, False)
        audio = recorder.finish()
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 16000, 2))
            self.assertEqual(wav.getnframes(), 8000)
        self.assertEqual(recorder.audio, bytearray())
        recorder.backend.RawInputStream.return_value.close.assert_called_once()

    def test_cancel_discards_audio_and_closes_once(self):
        recorder = self.recorder()
        recorder.start()
        recorder.capture(b'\x01\x00' * 10, 10, None, False)
        recorder.discard()
        recorder.discard()
        self.assertEqual(recorder.audio, bytearray())
        recorder.backend.RawInputStream.return_value.close.assert_called_once()

    def test_short_silent_and_overflow_capture_are_rejected(self):
        for audio, overflow in [(b'\x01\x00', False), (bytes(16000), False),
                                (b'\x01\x00' * 8000, True)]:
            with self.subTest(overflow=overflow, size=len(audio)):
                recorder = self.recorder()
                recorder.start()
                recorder.capture(audio, len(audio) // 2, None, overflow)
                with self.assertRaises(AssistantError):
                    recorder.finish()
                self.assertIsNone(recorder.stream)
                self.assertEqual(recorder.audio, bytearray())

    def test_capture_stops_at_limit(self):
        recorder = self.recorder()
        recorder.MAX_SECONDS = 1
        recorder.start()
        with self.assertRaises(recorder.backend.CallbackStop):
            recorder.capture(b'\x01\x00' * 17000, 17000, None, False)
        self.assertEqual(len(recorder.audio), 32000)
        recorder.discard()

    def test_open_failure_closes_stream(self):
        recorder = self.recorder()
        recorder.backend.RawInputStream.return_value.start.side_effect = OSError('device unavailable')
        with self.assertRaises(AssistantError):
            recorder.start()
        recorder.backend.RawInputStream.return_value.close.assert_called_once()

    def test_record_dialog_always_releases_recorder(self):
        for result in (True, False, KeyboardInterrupt(), EOFError()):
            with self.subTest(result=result), \
                    patch('src.tui.assistant.MicrophoneRecorder') as factory, \
                    patch('src.tui.assistant.PromptSession') as session:
                recorder = factory.return_value
                recorder.finish.return_value = b'wav'
                if isinstance(result, BaseException):
                    session.return_value.prompt.side_effect = result
                else:
                    session.return_value.prompt.return_value = result
                audio = record_voice()
                self.assertEqual(audio, b'wav' if result is True else None)
                self.assertEqual(recorder.finish.call_count, int(result is True))
                recorder.discard.assert_called_once()

    def test_real_terminal_bindings_and_automatic_stop(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        for keys, expected in [('\r', b'wav'), ('\x14', b'wav'), ('\x1b', None), ('\x03', None), ('', b'wav')]:
            with self.subTest(keys=keys), create_pipe_input() as pipe, \
                    patch('src.tui.assistant.MicrophoneRecorder') as factory:
                factory.return_value.MAX_SECONDS = 2 if keys else 0.01
                factory.return_value.finish.return_value = b'wav'
                def session_factory(**kwargs):
                    return PromptSession(input=pipe, output=DummyOutput(), **kwargs)
                with patch('src.tui.assistant.PromptSession', side_effect=session_factory):
                    if keys:
                        pipe.send_text(keys)
                    self.assertEqual(record_voice(), expected)
                factory.return_value.discard.assert_called_once()

    def test_device_disconnect_still_closes_stream_and_discards_buffer(self):
        recorder = self.recorder()
        recorder.start()
        recorder.capture(b'\x01\x00', 1, None, False)
        recorder.backend.RawInputStream.return_value.stop.side_effect = OSError('disconnected')
        with self.assertRaises(AssistantError):
            recorder.discard()
        recorder.backend.RawInputStream.return_value.close.assert_called_once()
        self.assertEqual(recorder.audio, bytearray())

    def test_audio_dependency_is_optional(self):
        with patch.dict('sys.modules', {'sounddevice': None}):
            with self.assertRaisesRegex(AssistantError, 'requirements-voice'):
                MicrophoneRecorder().start()
