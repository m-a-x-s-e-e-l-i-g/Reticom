from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

PTT_MAX_BYTES = 512_000
PTT_MAX_DURATION_MS = 10_000
PTT_MIME_TYPES = {"audio/webm", "audio/ogg", "audio/mp4"}
_CLIP_ID = re.compile(r"^[0-9a-f-]{36}$")


class PTTError(ValueError):
    pass


class PTTStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, clip_id: str, audio: bytes, mime_type: str) -> None:
        if not _CLIP_ID.fullmatch(clip_id):
            raise PTTError("invalid voice clip id")
        if mime_type not in PTT_MIME_TYPES:
            raise PTTError("unsupported voice clip format")
        if not audio or len(audio) > PTT_MAX_BYTES:
            raise PTTError(f"voice clip must be 1-{PTT_MAX_BYTES} bytes")
        with self._lock:
            (self.path / f"{clip_id}.audio").write_bytes(audio)
            (self.path / f"{clip_id}.json").write_text(
                json.dumps({"mime_type": mime_type}, separators=(",", ":")),
                encoding="utf-8",
            )

    def read(self, clip_id: str) -> tuple[bytes, str]:
        if not _CLIP_ID.fullmatch(clip_id):
            raise PTTError("invalid voice clip id")
        audio_path = self.path / f"{clip_id}.audio"
        metadata_path = self.path / f"{clip_id}.json"
        if not audio_path.exists() or not metadata_path.exists():
            raise FileNotFoundError("voice clip not found")
        with self._lock:
            metadata: dict[str, Any] = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            return audio_path.read_bytes(), str(metadata["mime_type"])

    def delete(self, clip_id: str) -> bool:
        if not _CLIP_ID.fullmatch(clip_id):
            raise PTTError("invalid voice clip id")
        removed = False
        with self._lock:
            for suffix in (".audio", ".json"):
                target = self.path / f"{clip_id}{suffix}"
                if target.exists():
                    target.unlink()
                    removed = True
        return removed
