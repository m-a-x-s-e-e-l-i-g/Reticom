"""Adapter for the APK's native Whisper engine and Android audio decoder."""
from types import SimpleNamespace


class AndroidWhisperModel:
    def __init__(self, *args, **kwargs):
        from java import jclass
        self.bridge = jclass("com.retium.field.WhisperTranscriber")

    def transcribe(self, path, *, language=None, **kwargs):
        text = str(self.bridge.transcribe(str(path), language or "auto")).strip()
        return [SimpleNamespace(text=text)], SimpleNamespace(
            language=language or "", language_probability=0.0)
