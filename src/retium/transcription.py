from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .ptt import PTT_MAX_BYTES, PTT_MIME_TYPES

_CLIP_ID = re.compile(r"^[0-9a-f-]{36}$")
_SUFFIXES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4"}


class TranscriptionError(RuntimeError):
    pass


class LocalTranscriber:
    def __init__(
        self,
        path: Path,
        model_name: str | None = None,
        language: str | None = None,
        model_factory: Callable[..., Any] | None = None,
    ):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name or os.getenv("RETIUM_TRANSCRIPTION_MODEL", "base")
        self.language = language or os.getenv("RETIUM_TRANSCRIPTION_LANGUAGE", "en")
        self._model_factory = model_factory
        self._model: Any = None
        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: set[str] = set()
        self._suppressed: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retium-stt")

    @staticmethod
    def _validate(clip_id: str, audio: bytes | None = None, mime_type: str | None = None) -> None:
        if not _CLIP_ID.fullmatch(clip_id):
            raise TranscriptionError("invalid voice clip id")
        if audio is not None and (not audio or len(audio) > PTT_MAX_BYTES):
            raise TranscriptionError("invalid voice clip payload")
        if mime_type is not None and mime_type not in PTT_MIME_TYPES:
            raise TranscriptionError("unsupported voice clip format")

    def _cache_path(self, clip_id: str) -> Path:
        self._validate(clip_id)
        return self.path / f"{clip_id}.json"

    def cached(self, clip_id: str) -> dict[str, Any] | None:
        cache_path = self._cache_path(clip_id)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            if payload.get("model") != self.model_name:
                return None
            if payload.get("requested_language") != self.language:
                return None
            return payload
        except (OSError, json.JSONDecodeError):
            return None

    def status(self, clip_id: str) -> dict[str, Any]:
        cached = self.cached(clip_id)
        if cached is not None:
            return cached
        with self._state_lock:
            pending = clip_id in self._pending
        return {"status": "processing" if pending else "unavailable", "clip_id": clip_id}

    def _get_model(self) -> Any:
        with self._model_lock:
            if self._model is None:
                try:
                    factory = self._model_factory
                    if factory is None:
                        from faster_whisper import WhisperModel

                        factory = WhisperModel
                    self._model = factory(
                        self.model_name,
                        device="cpu",
                        compute_type="int8",
                    )
                except Exception as exc:
                    raise TranscriptionError(f"local transcription model unavailable: {exc}") from exc
            return self._model

    def transcribe(self, clip_id: str, audio: bytes, mime_type: str) -> dict[str, Any]:
        self._validate(clip_id, audio, mime_type)
        with self._state_lock:
            if clip_id in self._suppressed:
                return {"status": "unavailable", "clip_id": clip_id}
        digest = hashlib.sha256(audio).hexdigest()
        cached = self.cached(clip_id)
        if cached is not None and cached.get("audio_sha256") == digest:
            return cached

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path,
                suffix=_SUFFIXES[mime_type],
                delete=False,
            ) as temporary:
                temporary.write(audio)
                temporary_path = Path(temporary.name)
            model = self._get_model()
            segments, info = model.transcribe(
                str(temporary_path),
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                language=None if self.language == "auto" else self.language,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
            result: dict[str, Any] = {
                "status": "ready" if text else "no_speech",
                "clip_id": clip_id,
                "text": text,
                "language": str(getattr(info, "language", "") or ""),
                "language_probability": round(
                    float(getattr(info, "language_probability", 0.0) or 0.0), 3
                ),
                "model": self.model_name,
                "requested_language": self.language,
                "audio_sha256": digest,
            }
            with self._state_lock:
                if clip_id in self._suppressed:
                    return {"status": "unavailable", "clip_id": clip_id}
            cache_path = self._cache_path(clip_id)
            staging_path = cache_path.with_suffix(".tmp")
            staging_path.write_text(
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            staging_path.replace(cache_path)
            return result
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"local transcription failed: {exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def schedule(self, clip_id: str, audio: bytes, mime_type: str) -> bool:
        self._validate(clip_id, audio, mime_type)
        if self.cached(clip_id) is not None:
            return False
        with self._state_lock:
            if clip_id in self._suppressed:
                return False
            if clip_id in self._pending:
                return False
            self._pending.add(clip_id)

        def run() -> None:
            try:
                self.transcribe(clip_id, audio, mime_type)
            finally:
                with self._state_lock:
                    self._pending.discard(clip_id)

        self._executor.submit(run)
        return True

    def delete(self, clip_id: str) -> bool:
        cache_path = self._cache_path(clip_id)
        staging_path = cache_path.with_suffix(".tmp")
        with self._state_lock:
            self._suppressed.add(clip_id)
        removed = False
        for target in (cache_path, staging_path):
            if target.exists():
                target.unlink()
                removed = True
        return removed

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
