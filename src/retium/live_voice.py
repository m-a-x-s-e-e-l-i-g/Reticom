from __future__ import annotations

import json
import math
import struct
import time
import uuid
from typing import Any

import RNS


LIVE_VOICE_VERSION = 1
LIVE_VOICE_MIN_BPS = 24_000
LIVE_VOICE_MAX_CHUNK = 64_000
LIVE_VOICE_MAX_PARTS = 256
LIVE_VOICE_MAX_CONTROL = 320
LIVE_VOICE_MIME_TYPES = {
    "audio/webm",
    "audio/webm;codecs=opus",
    "audio/ogg",
    "audio/ogg;codecs=opus",
}


class LiveVoiceError(ValueError):
    pass


class LiveVoiceMessage(RNS.MessageBase):
    MSGTYPE = 0x5256

    HELLO = 0
    START = 1
    CHUNK = 2
    END = 3
    CANCEL = 4

    _HEADER = struct.Struct(">B16sIHH")

    def __init__(
        self,
        kind: int = HELLO,
        stream_id: bytes | None = None,
        sequence: int = 0,
        part: int = 0,
        parts: int = 1,
        payload: bytes = b"",
    ) -> None:
        self.kind = kind
        self.stream_id = stream_id or bytes(16)
        self.sequence = sequence
        self.part = part
        self.parts = parts
        self.payload = payload

    @staticmethod
    def stream_bytes(stream_id: str) -> bytes:
        try:
            return uuid.UUID(stream_id).bytes
        except (ValueError, TypeError, AttributeError) as exc:
            raise LiveVoiceError("invalid live voice stream id") from exc

    @property
    def stream_uuid(self) -> str:
        return str(uuid.UUID(bytes=self.stream_id))

    @classmethod
    def hello(cls) -> LiveVoiceMessage:
        return cls(kind=cls.HELLO)

    @classmethod
    def control(
        cls, kind: int, stream_id: str, metadata: dict[str, Any] | None = None
    ) -> LiveVoiceMessage:
        if kind not in {cls.START, cls.END, cls.CANCEL}:
            raise LiveVoiceError("invalid live voice control kind")
        payload = b""
        if metadata:
            payload = json.dumps(
                metadata, ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
        if len(payload) > LIVE_VOICE_MAX_CONTROL:
            raise LiveVoiceError("live voice control metadata is too large")
        return cls(kind=kind, stream_id=cls.stream_bytes(stream_id), payload=payload)

    @classmethod
    def chunk_messages(
        cls,
        stream_id: str,
        sequence: int,
        audio: bytes,
        message_mdu: int,
    ) -> list[LiveVoiceMessage]:
        if not isinstance(audio, bytes) or not audio or len(audio) > LIVE_VOICE_MAX_CHUNK:
            raise LiveVoiceError("invalid live voice audio chunk")
        if not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFFFFFF:
            raise LiveVoiceError("invalid live voice chunk sequence")
        part_size = message_mdu - cls._HEADER.size
        if part_size < 32:
            raise LiveVoiceError("Reticulum channel MDU is too small for live voice")
        parts = math.ceil(len(audio) / part_size)
        if parts > LIVE_VOICE_MAX_PARTS:
            raise LiveVoiceError("live voice audio chunk requires too many packets")
        stream_bytes = cls.stream_bytes(stream_id)
        return [
            cls(
                kind=cls.CHUNK,
                stream_id=stream_bytes,
                sequence=sequence,
                part=index,
                parts=parts,
                payload=audio[index * part_size : (index + 1) * part_size],
            )
            for index in range(parts)
        ]

    def metadata(self) -> dict[str, Any]:
        if not self.payload:
            return {}
        try:
            value = json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveVoiceError("invalid live voice control metadata") from exc
        if not isinstance(value, dict):
            raise LiveVoiceError("invalid live voice control metadata")
        return value

    def pack(self) -> bytes:
        if self.kind not in {self.HELLO, self.START, self.CHUNK, self.END, self.CANCEL}:
            raise LiveVoiceError("invalid live voice message kind")
        if len(self.stream_id) != 16:
            raise LiveVoiceError("invalid live voice stream id")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise LiveVoiceError("invalid live voice chunk sequence")
        if not 1 <= self.parts <= LIVE_VOICE_MAX_PARTS or not 0 <= self.part < self.parts:
            raise LiveVoiceError("invalid live voice fragment")
        if not isinstance(self.payload, bytes):
            raise LiveVoiceError("invalid live voice payload")
        return self._HEADER.pack(
            self.kind, self.stream_id, self.sequence, self.part, self.parts
        ) + self.payload

    def unpack(self, raw: bytes) -> None:
        if not isinstance(raw, bytes) or len(raw) < self._HEADER.size:
            raise LiveVoiceError("invalid live voice message")
        (
            self.kind,
            self.stream_id,
            self.sequence,
            self.part,
            self.parts,
        ) = self._HEADER.unpack(raw[: self._HEADER.size])
        self.payload = raw[self._HEADER.size :]
        self.pack()
        if self.kind != self.CHUNK and len(self.payload) > LIVE_VOICE_MAX_CONTROL:
            raise LiveVoiceError("live voice control metadata is too large")


class LiveVoiceAssembler:
    def __init__(self) -> None:
        self._pending: dict[tuple[bytes, int], dict[str, Any]] = {}

    def clear_stream(self, stream_id: bytes) -> None:
        for key in [key for key in self._pending if key[0] == stream_id]:
            self._pending.pop(key, None)

    def add(self, message: LiveVoiceMessage) -> bytes | None:
        if message.kind != LiveVoiceMessage.CHUNK:
            raise LiveVoiceError("only live voice chunks can be assembled")
        now = time.monotonic()
        for key, value in tuple(self._pending.items()):
            if now - float(value["created_at"]) > 15:
                self._pending.pop(key, None)
        key = (message.stream_id, message.sequence)
        state = self._pending.setdefault(
            key,
            {
                "created_at": now,
                "parts": message.parts,
                "payloads": [None] * message.parts,
            },
        )
        if state["parts"] != message.parts:
            self._pending.pop(key, None)
            raise LiveVoiceError("live voice fragment count changed")
        state["payloads"][message.part] = message.payload
        if any(payload is None for payload in state["payloads"]):
            return None
        audio = b"".join(state["payloads"])
        self._pending.pop(key, None)
        if not audio or len(audio) > LIVE_VOICE_MAX_CHUNK:
            raise LiveVoiceError("assembled live voice chunk is invalid")
        return audio


def validate_live_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise LiveVoiceError("invalid live voice metadata")
    mime_type = str(metadata.get("mime_type", "")).lower().replace(" ", "")
    if mime_type not in LIVE_VOICE_MIME_TYPES:
        raise LiveVoiceError("unsupported live voice format")
    callsign = str(metadata.get("callsign", "")).strip()
    if not callsign or len(callsign) > 24:
        raise LiveVoiceError("invalid live voice callsign")
    return {
        "v": LIVE_VOICE_VERSION,
        "mime_type": mime_type,
        "callsign": callsign,
        "icon": str(metadata.get("icon", "dot"))[:16],
        "color": str(metadata.get("color", "moss"))[:16],
        "sender_hash": str(metadata.get("sender_hash", ""))[:64],
    }
