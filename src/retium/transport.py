from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Any

import RNS

from .live_voice import (
    LiveVoiceAssembler,
    LiveVoiceError,
    LiveVoiceMessage,
    LIVE_VOICE_MIN_BPS,
    validate_live_metadata,
)
from .protocol import ProtocolError, new_event, sign_event, verify_envelope
from .ptt import PTTError
from .store import EventStore, PrivateMessageStore
from .team import AVAILABLE_MODULES, TEAM_NAME_MAX_LENGTH, encode_join_code

APP_NAME = "retium"
ASPECTS = ("event", "ingress")
TEAM_ANNOUNCE_KIND = "team"
TEAM_ANNOUNCE_VERSION = 1
TEAM_ANNOUNCE_MAX_AGE = 180
LINK_EVENT_TYPES = {
    "chat.message",
    "drawing.created",
    "drawing.deleted",
    "marker.created",
    "marker.deleted",
    "message.deleted",
    "position.updated",
    "task.completed",
}


def _send_channel_message(
    channel: Any, message: LiveVoiceMessage, timeout: float = 3.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if channel.is_ready_to_send():
            channel.send(message)
            return
        time.sleep(0.01)
    raise TimeoutError("Reticulum live voice channel is congested")


def _load_or_create_identity(path: Path) -> RNS.Identity:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        identity = RNS.Identity.from_file(str(path))
        if identity is None:
            raise RuntimeError(f"Could not load Reticulum identity at {path}")
        return identity
    identity = RNS.Identity()
    if not identity.to_file(str(path)):
        raise RuntimeError(f"Could not save Reticulum identity at {path}")
    return identity


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class GatewayReceiver:
    def __init__(
        self,
        config_dir: Path,
        data_dir: Path,
        store: EventStore,
        team_name: str | None = None,
        team_modules: list[str] | None = None,
        task_response: Callable[[], bytes] | None = None,
        feed_response: Callable[[], bytes] | None = None,
        save_ptt: Callable[[str, bytes, str], None] | None = None,
        audio_response: Callable[[str], tuple[bytes, str]] | None = None,
        transcription_response: Callable[[str, bool], dict[str, Any]] | None = None,
        admin_response: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        live_callback: Callable[[dict[str, Any]], None] | None = None,
        private_store: PrivateMessageStore | None = None,
        team_created_at: int | None = None,
        everyone_admin: bool = False,
    ):
        self.data_dir = data_dir
        self.store = store
        self.event_callback = event_callback
        self.live_callback = live_callback
        self.private_store = private_store
        self.team_created_at = team_created_at
        self.team_name = team_name
        self.team_modules = sorted(team_modules or [])
        self.everyone_admin = everyone_admin
        self.task_response = task_response
        self.feed_response = feed_response
        self.save_ptt = save_ptt
        self.audio_response = audio_response
        self.transcription_response = transcription_response
        self.admin_response = admin_response
        self.reticulum = RNS.Reticulum.get_instance() or RNS.Reticulum(
            str(config_dir)
        )
        self.identity = _load_or_create_identity(data_dir / "gateway.identity")
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            APP_NAME,
            *ASPECTS,
        )
        self.destination.set_proof_strategy(RNS.Destination.PROVE_ALL)
        self.destination.set_packet_callback(self._packet_received)
        self.destination.set_link_established_callback(self._link_established)
        self.destination.register_request_handler(
            "/tasks",
            response_generator=self._tasks_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.destination.register_request_handler(
            "/feed", response_generator=self._feed_request, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.register_request_handler(
            "/ptt", response_generator=self._ptt_request, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.register_request_handler(
            "/event", response_generator=self._event_request, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.register_request_handler(
            "/audio", response_generator=self._audio_request, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.register_request_handler(
            "/transcription",
            response_generator=self._transcription_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.destination.register_request_handler(
            "/private/messages",
            response_generator=self._private_messages_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.destination.register_request_handler(
            "/private/send",
            response_generator=self._private_send_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.destination.register_request_handler(
            "/private/ptt",
            response_generator=self._private_ptt_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.destination.register_request_handler(
            "/admin",
            response_generator=self._admin_request,
            allow=RNS.Destination.ALLOW_ALL,
        )
        self.last_error: str | None = None
        self.last_packet_at: int | None = None
        self._live_lock = threading.RLock()
        self._live_links: set[Any] = set()
        self._live_streams: dict[str, dict[str, Any]] = {}
        self._live_assembler = LiveVoiceAssembler()
        self._stop = threading.Event()
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)

        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "gateway.json").write_text(
            json.dumps(
                {
                    "destination": self.destination.hash.hex(),
                    "identity": self.identity.hash.hex(),
                    "aspects": f"{APP_NAME}.{'/'.join(ASPECTS)}",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._announce()
        self._announce_thread.start()

    def _emit_live(self, frame: dict[str, Any]) -> None:
        if self.live_callback is not None:
            self.live_callback(frame)

    def _live_transport_state(self) -> None:
        with self._live_lock:
            peers = len(self._live_links)
        self._emit_live({"type": "live.transport", "ready": peers > 0, "peers": peers})

    def _link_established(self, link: Any) -> None:
        channel = link.get_channel()
        channel.register_message_type(LiveVoiceMessage)
        channel.add_message_handler(lambda message: self._receive_live(link, message))
        link.set_link_closed_callback(self._live_link_closed)

    def _live_link_closed(self, link: Any) -> None:
        ended: list[dict[str, Any]] = []
        with self._live_lock:
            self._live_links.discard(link)
            for stream_id, state in tuple(self._live_streams.items()):
                if state.get("link") is link:
                    self._live_streams.pop(stream_id, None)
                    ended.append({"type": "live.cancel", "stream_id": stream_id})
        for frame in ended:
            self._emit_live(frame)
        self._live_transport_state()

    def _live_links_except(self, excluded: Any | None = None) -> list[Any]:
        with self._live_lock:
            return [
                link
                for link in self._live_links
                if link is not excluded and link.status == RNS.Link.ACTIVE
            ]

    def _drop_live_link(self, link: Any) -> None:
        with self._live_lock:
            self._live_links.discard(link)

    def _broadcast_live_control(
        self,
        kind: int,
        stream_id: str,
        metadata: dict[str, Any] | None = None,
        excluded: Any | None = None,
    ) -> int:
        message = LiveVoiceMessage.control(kind, stream_id, metadata)
        delivered = 0
        for link in self._live_links_except(excluded):
            try:
                _send_channel_message(link.get_channel(), message)
                delivered += 1
            except (TimeoutError, OSError, RuntimeError):
                self._drop_live_link(link)
        return delivered

    def _broadcast_live_chunk(
        self,
        stream_id: str,
        sequence: int,
        audio: bytes,
        excluded: Any | None = None,
    ) -> int:
        delivered = 0
        for link in self._live_links_except(excluded):
            try:
                channel = link.get_channel()
                for message in LiveVoiceMessage.chunk_messages(
                    stream_id, sequence, audio, channel.mdu
                ):
                    _send_channel_message(channel, message)
                delivered += 1
            except (LiveVoiceError, TimeoutError, OSError, RuntimeError):
                self._drop_live_link(link)
        return delivered

    def _receive_live(self, link: Any, message: Any) -> bool:
        if not isinstance(message, LiveVoiceMessage):
            return False
        try:
            remote_identity = link.get_remote_identity()
            if message.kind == LiveVoiceMessage.HELLO:
                if remote_identity is None:
                    return True
                with self._live_lock:
                    self._live_links.add(link)
                _send_channel_message(link.get_channel(), LiveVoiceMessage.hello())
                self._live_transport_state()
                return True
            if remote_identity is None:
                raise LiveVoiceError("live voice link is not identified")
            stream_id = message.stream_uuid
            sender_hash = remote_identity.hash.hex()
            with self._live_lock:
                state = self._live_streams.get(stream_id)
            if message.kind == LiveVoiceMessage.START:
                metadata = validate_live_metadata(
                    {**message.metadata(), "sender_hash": sender_hash}
                )
                with self._live_lock:
                    self._live_streams[stream_id] = {
                        "link": link,
                        "sender_hash": sender_hash,
                        "metadata": metadata,
                        "chunks": [],
                        "started_at": time.monotonic(),
                    }
                frame = {"type": "live.start", "stream_id": stream_id, **metadata}
                self._emit_live(frame)
                self._broadcast_live_control(
                    message.kind, stream_id, metadata, excluded=link
                )
                return True
            if state is None or state.get("link") is not link:
                raise LiveVoiceError("unknown live voice stream")
            if message.kind == LiveVoiceMessage.CHUNK:
                audio = self._live_assembler.add(message)
                if audio is not None:
                    state["chunks"].append(audio)
                    self._emit_live(
                        {
                            "type": "live.chunk",
                            "stream_id": stream_id,
                            "sequence": message.sequence,
                            "audio": audio,
                        }
                    )
                    self._broadcast_live_chunk(
                        stream_id, message.sequence, audio, excluded=link
                    )
                return True
            if message.kind in {LiveVoiceMessage.END, LiveVoiceMessage.CANCEL}:
                frame_type = (
                    "live.end"
                    if message.kind == LiveVoiceMessage.END
                    else "live.cancel"
                )
                with self._live_lock:
                    self._live_streams.pop(stream_id, None)
                self._live_assembler.clear_stream(message.stream_id)
                self._emit_live({"type": frame_type, "stream_id": stream_id})
                self._broadcast_live_control(message.kind, stream_id, excluded=link)
                if message.kind == LiveVoiceMessage.END:
                    self._archive_live_stream(stream_id, state)
                return True
        except (LiveVoiceError, TimeoutError, OSError, RuntimeError) as exc:
            RNS.log(f"Reticom rejected live voice frame: {exc}", RNS.LOG_WARNING)
        return True

    def live_ready(self) -> bool:
        return bool(self._live_links_except())

    def _archive_live_stream(self, stream_id: str, state: dict[str, Any]) -> None:
        chunks = state.get("chunks", [])
        metadata = state.get("metadata", {})
        if not chunks or not isinstance(metadata, dict) or self.save_ptt is None:
            return
        try:
            audio = b"".join(chunks)
            duration_ms = max(
                100,
                min(
                    10_000,
                    round((time.monotonic() - float(state["started_at"])) * 1000),
                ),
            )
            mime_type = str(metadata["mime_type"]).split(";", 1)[0]
            event = new_event(
                "ptt.broadcast",
                str(metadata["callsign"]),
                icon=str(metadata.get("icon", "dot")),
                color=str(metadata.get("color", "moss")),
                clip_id=stream_id,
                duration_ms=duration_ms,
                mime_type=mime_type,
            )
            if state.get("link") is None:
                self.publish_ptt(event, audio)
                return
            sender_hash = str(state["sender_hash"])
            self.save_ptt(stream_id, audio, mime_type)
            packet_hash = RNS.Identity.full_hash(uuid.UUID(stream_id).bytes + audio).hex()
            received_at = int(time.time())
            inserted = self.store.insert(
                event,
                sender_hash,
                packet_hash=packet_hash,
                interface_name="Authenticated Reticulum live channel",
            )
            if inserted and self.event_callback is not None:
                self.event_callback(
                    {
                        **event,
                        "network": {
                            "verified": True,
                            "sender_hash": sender_hash,
                            "packet_hash": packet_hash,
                            "interface": "Authenticated Reticulum live channel",
                            "received_at": received_at,
                            "rssi": None,
                            "snr": None,
                        },
                    }
                )
        except (KeyError, TypeError, ValueError, PTTError, ProtocolError) as exc:
            RNS.log(f"Reticom could not archive live voice: {exc}", RNS.LOG_WARNING)

    def send_live_start(self, stream_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        metadata = validate_live_metadata(
            {**metadata, "sender_hash": self.identity.hash.hex()}
        )
        if not self._live_links_except():
            raise TimeoutError("No Field live voice link is connected")
        with self._live_lock:
            self._live_streams[stream_id] = {
                "link": None,
                "sender_hash": self.identity.hash.hex(),
                "metadata": metadata,
                "chunks": [],
                "started_at": time.monotonic(),
            }
        peers = self._broadcast_live_control(
            LiveVoiceMessage.START, stream_id, metadata
        )
        if peers == 0:
            with self._live_lock:
                self._live_streams.pop(stream_id, None)
            raise TimeoutError("No Field live voice link accepted the transmission")
        return {"transport": "authenticated Reticulum channel", "peers": peers}

    def send_live_chunk(self, stream_id: str, sequence: int, audio: bytes) -> int:
        with self._live_lock:
            state = self._live_streams.get(stream_id)
        if state is None or state.get("link") is not None:
            raise LiveVoiceError("unknown Command live voice stream")
        peers = self._broadcast_live_chunk(stream_id, sequence, audio)
        if peers == 0:
            raise TimeoutError("Live voice link was lost")
        state["chunks"].append(audio)
        return peers

    def send_live_end(self, stream_id: str, cancel: bool = False) -> int:
        with self._live_lock:
            state = self._live_streams.pop(stream_id, None)
        if state is None or state.get("link") is not None:
            raise LiveVoiceError("unknown Command live voice stream")
        kind = LiveVoiceMessage.CANCEL if cancel else LiveVoiceMessage.END
        peers = self._broadcast_live_control(kind, stream_id)
        if not cancel:
            self._archive_live_stream(stream_id, state)
        return peers

    def _announce_data(self) -> bytes:
        return json.dumps(
            {
                "v": TEAM_ANNOUNCE_VERSION,
                "kind": TEAM_ANNOUNCE_KIND,
                "name": self.team_name,
                "modules": self.team_modules,
                "everyone_admin": self.everyone_admin,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _announce(self) -> None:
        self.destination.announce(app_data=self._announce_data())

    def set_team(
        self,
        team_name: str,
        modules: list[str],
        created_at: int | None = None,
        everyone_admin: bool = False,
    ) -> None:
        self.team_name = team_name
        self.team_modules = sorted(modules)
        self.everyone_admin = everyone_admin
        if created_at is not None:
            self.team_created_at = created_at
        self._announce()

    def _tasks_request(
        self,
        path: str,
        data: bytes | None,
        request_id: bytes,
        link_id: bytes,
        remote_identity: RNS.Identity | None,
        requested_at: float,
    ) -> bytes:
        del path, data, request_id, link_id, requested_at
        if remote_identity is None:
            return json.dumps(
                {"module": "tasks", "error": "field identity required"},
                separators=(",", ":"),
            ).encode("utf-8")
        if "tasks" not in self.team_modules or self.task_response is None:
            return json.dumps(
                {"module": "tasks", "enabled": False, "tasks": []},
                separators=(",", ":"),
            ).encode("utf-8")
        return self.task_response()

    @staticmethod
    def _identity_error(remote_identity: RNS.Identity | None) -> bytes | None:
        if remote_identity is None:
            return json.dumps(
                {"error": "field identity required"}, separators=(",", ":")
            ).encode("utf-8")
        return None

    def _feed_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, data, request_id, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        if self.feed_response is None:
            return b'{"events":[]}'
        try:
            payload = json.loads(self.feed_response().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid feed response")
            assert remote_identity is not None
            payload["private_events"] = (
                self.private_store.recent(
                    remote_identity.hash.hex(), since=self.team_created_at
                )
                if self.private_store is not None
                else []
            )
            return json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _ptt_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if not isinstance(data, dict):
                raise ProtocolError("invalid voice request")
            envelope = data.get("envelope")
            audio = data.get("audio")
            if not isinstance(envelope, bytes) or not isinstance(audio, bytes):
                raise ProtocolError("invalid voice request")
            event, sender_hash = verify_envelope(envelope)
            if event["type"] != "ptt.broadcast":
                raise ProtocolError("invalid voice event")
            assert remote_identity is not None
            if sender_hash != remote_identity.hash.hex():
                raise ProtocolError("voice signer does not match link identity")
            if self.save_ptt is None:
                raise ProtocolError("voice storage unavailable")
            self.save_ptt(event["clip_id"], audio, event["mime_type"])
            received_at = int(time.time())
            inserted = self.store.insert(
                event,
                sender_hash,
                packet_hash=request_id.hex(),
                interface_name="Authenticated Reticulum Link",
            )
            if inserted:
                self.last_packet_at = received_at
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            **event,
                            "network": {
                                "verified": True,
                                "sender_hash": sender_hash,
                                "packet_hash": request_id.hex(),
                                "interface": "Authenticated Reticulum Link",
                                "received_at": received_at,
                                "rssi": None,
                                "snr": None,
                            },
                        }
                    )
            return json.dumps(
                {"accepted": True, "event_id": event["id"]}, separators=(",", ":")
            ).encode("utf-8")
        except (ProtocolError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _event_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if not isinstance(data, dict) or not isinstance(data.get("envelope"), bytes):
                raise ProtocolError("invalid event request")
            event, sender_hash = verify_envelope(data["envelope"])
            assert remote_identity is not None
            if sender_hash != remote_identity.hash.hex():
                raise ProtocolError("event signer does not match link identity")
            if event["type"] not in LINK_EVENT_TYPES:
                raise ProtocolError("unsupported linked event")
            admin_moderation = False
            if (
                event["type"] == "message.deleted"
                and not self.store.message_owned_by(event["message_id"], sender_hash)
            ):
                if not self.everyone_admin:
                    raise ProtocolError("only the original sender can remove this message")
                if not self.store.event_exists(event["message_id"], "chat.message"):
                    raise ProtocolError("message not found")
                admin_moderation = True
            if (
                event["type"] == "marker.deleted"
                and not self.store.map_event_owned_by(
                    event["marker_id"], "marker.created", sender_hash
                )
            ):
                if not self.everyone_admin:
                    raise ProtocolError("only the original sender can remove this marker")
                if self.store.map_event(event["marker_id"], "marker.created") is None:
                    raise ProtocolError("marker not found")
                admin_moderation = True
            if (
                event["type"] == "drawing.deleted"
                and not self.store.map_event_owned_by(
                    event["drawing_id"], "drawing.created", sender_hash
                )
            ):
                if not self.everyone_admin:
                    raise ProtocolError("only the original sender can remove this drawing")
                if self.store.map_event(event["drawing_id"], "drawing.created") is None:
                    raise ProtocolError("drawing not found")
                admin_moderation = True
            received_at = int(time.time())
            interface_name = (
                "Team admin · Authenticated Reticulum Link"
                if admin_moderation
                else "Authenticated Reticulum Link"
            )
            inserted = self.store.insert(
                event,
                sender_hash,
                packet_hash=request_id.hex(),
                interface_name=interface_name,
            )
            if inserted:
                self.last_packet_at = received_at
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            **event,
                            "network": {
                                "verified": True,
                                "sender_hash": sender_hash,
                                "packet_hash": request_id.hex(),
                                "interface": interface_name,
                                "received_at": received_at,
                                "rssi": None,
                                "snr": None,
                            },
                        }
                    )
            return json.dumps(
                {"accepted": True, "event_id": event["id"]}, separators=(",", ":")
            ).encode("utf-8")
        except (ProtocolError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _audio_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> dict[str, Any] | bytes:
        del path, request_id, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if not isinstance(data, str) or self.audio_response is None:
                raise ValueError("invalid voice clip request")
            assert remote_identity is not None
            visibility = (
                self.private_store.clip_visibility(data, remote_identity.hash.hex())
                if self.private_store is not None
                else None
            )
            if visibility is False:
                raise PermissionError("private voice clip is not addressed to this identity")
            audio, mime_type = self.audio_response(data)
            return {"audio": audio, "mime_type": mime_type}
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _transcription_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> dict[str, Any] | bytes:
        del path, request_id, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if not isinstance(data, dict) or self.transcription_response is None:
                raise ValueError("invalid transcription request")
            clip_id = data.get("clip_id")
            start = data.get("start", False)
            if not isinstance(clip_id, str) or not isinstance(start, bool):
                raise ValueError("invalid transcription request")
            assert remote_identity is not None
            visibility = (
                self.private_store.clip_visibility(clip_id, remote_identity.hash.hex())
                if self.private_store is not None
                else None
            )
            if visibility is False:
                raise PermissionError("private voice transcript is not addressed to this identity")
            return self.transcription_response(clip_id, start)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _known_private_identity(self, identity_hash: str) -> bool:
        if identity_hash == self.identity.hash.hex():
            return True
        return any(
            operator["sender_hash"] == identity_hash
            for operator in self.store.operators(since=self.team_created_at)
        )

    def _private_messages_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, data, request_id, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        assert remote_identity is not None
        identity_hash = remote_identity.hash.hex()
        if not self._known_private_identity(identity_hash):
            return json.dumps(
                {"error": "operator identity is not part of this team"},
                separators=(",", ":"),
            ).encode("utf-8")
        if self.private_store is None:
            return b'{"messages":[]}'
        messages = self.private_store.recent(
            identity_hash, since=self.team_created_at
        )
        return json.dumps(
            {"messages": messages}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    def _admin_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, request_id, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            assert remote_identity is not None
            identity_hash = remote_identity.hash.hex()
            if not self.everyone_admin:
                raise PermissionError("team admin mode is not enabled")
            if not self._known_private_identity(identity_hash):
                raise PermissionError("operator identity is not part of this team")
            if self.admin_response is None:
                raise RuntimeError("team administration is unavailable")
            if not isinstance(data, dict):
                raise ValueError("invalid team admin request")
            action = data.get("action")
            payload = data.get("payload", {})
            if not isinstance(action, str) or not isinstance(payload, dict):
                raise ValueError("invalid team admin request")
            result = self.admin_response(action, payload, identity_hash)
            return json.dumps(
                {"accepted": True, **result},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (PermissionError, RuntimeError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _private_send_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if self.private_store is None:
                raise ProtocolError("private messaging is unavailable")
            if not isinstance(data, dict) or not isinstance(data.get("envelope"), bytes):
                raise ProtocolError("invalid private message request")
            event, sender_hash = verify_envelope(data["envelope"])
            assert remote_identity is not None
            if sender_hash != remote_identity.hash.hex():
                raise ProtocolError("private message signer does not match link identity")
            if event["type"] != "private.message":
                raise ProtocolError("invalid private message event")
            if not self._known_private_identity(sender_hash):
                raise ProtocolError("sender identity is not part of this team")
            recipient_hash = event["recipient_hash"]
            if recipient_hash == sender_hash:
                raise ProtocolError("choose another operator")
            if not self._known_private_identity(recipient_hash):
                raise ProtocolError("recipient identity is not part of this team")
            self.private_store.insert(
                event,
                sender_hash,
                packet_hash=request_id.hex(),
                interface_name="Private identified Reticulum Link",
            )
            return json.dumps(
                {"accepted": True, "event_id": event["id"]},
                separators=(",", ":"),
            ).encode("utf-8")
        except (ProtocolError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _private_ptt_request(
        self, path: str, data: Any, request_id: bytes, link_id: bytes,
        remote_identity: RNS.Identity | None, requested_at: float,
    ) -> bytes:
        del path, link_id, requested_at
        error = self._identity_error(remote_identity)
        if error is not None:
            return error
        try:
            if self.private_store is None or self.save_ptt is None:
                raise ProtocolError("private voice is unavailable")
            if not isinstance(data, dict):
                raise ProtocolError("invalid private voice request")
            envelope = data.get("envelope")
            audio = data.get("audio")
            if not isinstance(envelope, bytes) or not isinstance(audio, bytes):
                raise ProtocolError("invalid private voice request")
            event, sender_hash = verify_envelope(envelope)
            assert remote_identity is not None
            if sender_hash != remote_identity.hash.hex():
                raise ProtocolError("private voice signer does not match link identity")
            if event["type"] != "private.ptt":
                raise ProtocolError("invalid private voice event")
            if not self._known_private_identity(sender_hash):
                raise ProtocolError("sender identity is not part of this team")
            recipient_hash = event["recipient_hash"]
            if recipient_hash == sender_hash:
                raise ProtocolError("choose another operator")
            if not self._known_private_identity(recipient_hash):
                raise ProtocolError("recipient identity is not part of this team")
            self.save_ptt(event["clip_id"], audio, event["mime_type"])
            self.private_store.insert(
                event,
                sender_hash,
                packet_hash=request_id.hex(),
                interface_name="Private voice · Identified Reticulum Link",
            )
            return json.dumps(
                {"accepted": True, "event_id": event["id"]},
                separators=(",", ":"),
            ).encode("utf-8")
        except (ProtocolError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, separators=(",", ":")).encode("utf-8")

    def _announce_loop(self) -> None:
        while not self._stop.wait(8):
            self._announce()

    def _packet_received(self, raw: bytes, packet: RNS.Packet) -> None:
        try:
            event, sender_hash = verify_envelope(raw)
            if event["type"] in {"private.message", "private.ptt"}:
                raise ProtocolError("private communications require an identified Reticulum link")
            receiving_interface = getattr(packet, "receiving_interface", None)
            packet_hash = getattr(packet, "packet_hash", b"").hex() or None
            interface_name = str(receiving_interface) if receiving_interface else None
            rssi = getattr(packet, "rssi", None)
            snr = getattr(packet, "snr", None)
            inserted = self.store.insert(
                event,
                sender_hash,
                packet_hash=packet_hash,
                interface_name=interface_name,
                rssi=rssi,
                snr=snr,
            )
            if inserted:
                self.last_packet_at = int(time.time())
                self.last_error = None
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            **event,
                            "network": {
                                "verified": True,
                                "sender_hash": sender_hash,
                                "packet_hash": packet_hash,
                                "interface": interface_name,
                                "received_at": self.last_packet_at,
                                "rssi": rssi,
                                "snr": snr,
                            },
                        }
                    )
                RNS.log(f"Reticom accepted verified event {event['id']}", RNS.LOG_NOTICE)
        except (ProtocolError, ValueError, TypeError) as exc:
            self.last_error = str(exc)
            RNS.log(f"Reticom rejected packet: {exc}", RNS.LOG_WARNING)

    def publish_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Sign a Command event and expose it through the Reticulum team feed."""
        if event.get("type") == "private.message":
            raise ProtocolError("use the private Reticulum mailbox")
        envelope = sign_event(event, self.identity, packet_limit=False)
        verified_event, sender_hash = verify_envelope(envelope)
        envelope_hash = RNS.Identity.full_hash(envelope).hex()
        received_at = int(time.time())
        inserted = self.store.insert(
            verified_event,
            sender_hash,
            packet_hash=envelope_hash,
            interface_name="Command origin · Reticulum feed",
        )
        if not inserted:
            raise ProtocolError("command event already exists")
        self.last_packet_at = received_at
        if self.event_callback is not None:
            self.event_callback(
                {
                    **verified_event,
                    "network": {
                        "verified": True,
                        "sender_hash": sender_hash,
                        "packet_hash": envelope_hash,
                        "interface": "Command origin · Reticulum feed",
                        "received_at": received_at,
                        "rssi": None,
                        "snr": None,
                    },
                }
            )
        return {
            "event_id": verified_event["id"],
            "delivered": False,
            "status": "published",
            "rtt_ms": None,
            "packet_bytes": len(envelope),
            "gateway": self.destination.hash.hex(),
            "transport": "identified Reticulum feed",
        }

    def publish_local_field_event(
        self,
        event: dict[str, Any],
        identity: RNS.Identity,
        *,
        admin: bool = False,
    ) -> dict[str, Any]:
        """Accept a signed event from a Field UI hosted in this same process."""
        envelope = sign_event(event, identity, packet_limit=False)
        verified_event, sender_hash = verify_envelope(envelope)
        if verified_event["type"] in {"private.message", "private.ptt"}:
            raise ProtocolError("use the private Reticulum mailbox")
        if verified_event["type"] == "message.deleted" and not self.store.message_owned_by(
            verified_event["message_id"], sender_hash
        ):
            if not admin or not self.store.event_exists(
                verified_event["message_id"], "chat.message"
            ):
                raise ProtocolError("only the original sender can remove this message")
        if verified_event["type"] == "marker.deleted" and not self.store.map_event_owned_by(
            verified_event["marker_id"], "marker.created", sender_hash
        ):
            if not admin or self.store.map_event(
                verified_event["marker_id"], "marker.created"
            ) is None:
                raise ProtocolError("only the original sender can remove this marker")
        if verified_event["type"] == "drawing.deleted" and not self.store.map_event_owned_by(
            verified_event["drawing_id"], "drawing.created", sender_hash
        ):
            if not admin or self.store.map_event(
                verified_event["drawing_id"], "drawing.created"
            ) is None:
                raise ProtocolError("only the original sender can remove this drawing")
        envelope_hash = RNS.Identity.full_hash(envelope).hex()
        received_at = int(time.time())
        inserted = self.store.insert(
            verified_event,
            sender_hash,
            packet_hash=envelope_hash,
            interface_name="Hosted Field origin · Reticulum feed",
        )
        if not inserted:
            raise ProtocolError("field event already exists")
        self.last_packet_at = received_at
        if self.event_callback is not None:
            self.event_callback(
                {
                    **verified_event,
                    "network": {
                        "verified": True,
                        "sender_hash": sender_hash,
                        "packet_hash": envelope_hash,
                        "interface": "Hosted Field origin · Reticulum feed",
                        "received_at": received_at,
                        "rssi": None,
                        "snr": None,
                    },
                }
            )
        return {
            "event_id": verified_event["id"],
            "delivered": True,
            "status": "hosted",
            "rtt_ms": 0,
            "packet_bytes": len(envelope),
            "gateway": self.destination.hash.hex(),
            "transport": "local signed origin · Reticulum feed",
        }

    def publish_local_field_ptt(
        self, event: dict[str, Any], audio: bytes, identity: RNS.Identity
    ) -> dict[str, Any]:
        if self.save_ptt is None:
            raise PTTError("voice storage unavailable")
        self.save_ptt(event["clip_id"], audio, event["mime_type"])
        return self.publish_local_field_event(event, identity, admin=True)

    def publish_local_private(
        self,
        event: dict[str, Any],
        identity: RNS.Identity,
        audio: bytes | None = None,
    ) -> dict[str, Any]:
        if self.private_store is None:
            raise ProtocolError("private messaging is unavailable")
        envelope = sign_event(event, identity, packet_limit=False)
        verified_event, sender_hash = verify_envelope(envelope)
        if verified_event["type"] not in {"private.message", "private.ptt"}:
            raise ProtocolError("invalid private communication event")
        if not self._known_private_identity(verified_event["recipient_hash"]):
            raise ProtocolError("recipient identity is not part of this team")
        if audio is not None:
            if self.save_ptt is None:
                raise PTTError("voice storage unavailable")
            self.save_ptt(
                verified_event["clip_id"], audio, verified_event["mime_type"]
            )
        envelope_hash = RNS.Identity.full_hash(envelope).hex()
        self.private_store.insert(
            verified_event,
            sender_hash,
            packet_hash=envelope_hash,
            interface_name="Hosted Field private Reticulum mailbox",
        )
        return {
            "event_id": verified_event["id"],
            "accepted": True,
            "status": "mailbox",
            "rtt_ms": 0,
            "packet_bytes": len(envelope) + (len(audio) if audio is not None else 0),
            "gateway": self.destination.hash.hex(),
            "transport": "local signed origin · private Reticulum mailbox",
        }

    def publish_ptt(self, event: dict[str, Any], audio: bytes) -> dict[str, Any]:
        if self.save_ptt is None:
            raise PTTError("voice storage unavailable")
        self.save_ptt(event["clip_id"], audio, event["mime_type"])
        return self.publish_event(event)

    def publish_private(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.private_store is None:
            raise ProtocolError("private messaging is unavailable")
        envelope = sign_event(event, self.identity, packet_limit=False)
        verified_event, sender_hash = verify_envelope(envelope)
        if verified_event["type"] not in {"private.message", "private.ptt"}:
            raise ProtocolError("invalid private communication event")
        recipient_hash = verified_event["recipient_hash"]
        if not self._known_private_identity(recipient_hash):
            raise ProtocolError("recipient identity is not part of this team")
        envelope_hash = RNS.Identity.full_hash(envelope).hex()
        self.private_store.insert(
            verified_event,
            sender_hash,
            packet_hash=envelope_hash,
            interface_name="Command private Reticulum mailbox",
        )
        return {
            "event_id": verified_event["id"],
            "accepted": True,
            "status": "mailbox",
            "packet_bytes": len(envelope),
            "transport": "private identified Reticulum mailbox",
        }

    def publish_private_ptt(
        self, event: dict[str, Any], audio: bytes
    ) -> dict[str, Any]:
        if self.save_ptt is None:
            raise PTTError("voice storage unavailable")
        if not self._known_private_identity(event["recipient_hash"]):
            raise ProtocolError("recipient identity is not part of this team")
        self.save_ptt(event["clip_id"], audio, event["mime_type"])
        return self.publish_private(event)

    def state(self) -> dict[str, Any]:
        return {
            "destination": self.destination.hash.hex(),
            "identity": self.identity.hash.hex(),
            "last_packet_at": self.last_packet_at,
            "last_error": self.last_error,
            "interfaces": _json_safe(self.reticulum.get_interface_stats()),
        }

    def stop(self) -> None:
        self._stop.set()
        for link in self._live_links_except():
            try:
                link.teardown()
            except Exception:
                pass
        RNS.Transport.deregister_destination(self.destination)


class TeamAnnounceHandler:
    aspect_filter = f"{APP_NAME}.{'.'.join(ASPECTS)}"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._teams: dict[str, dict[str, Any]] = {}

    def received_announce(
        self,
        destination_hash: bytes,
        announced_identity: RNS.Identity,
        app_data: bytes | None,
        announce_packet_hash: bytes | None = None,
        is_path_response: bool = False,
    ) -> None:
        del announced_identity, announce_packet_hash, is_path_response
        if not app_data:
            return
        try:
            payload = json.loads(app_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        name = payload.get("name")
        modules = payload.get("modules", [])
        everyone_admin = payload.get("everyone_admin", False)
        if (
            payload.get("v") != TEAM_ANNOUNCE_VERSION
            or payload.get("kind") != TEAM_ANNOUNCE_KIND
            or not isinstance(name, str)
            or not isinstance(modules, list)
            or any(item not in AVAILABLE_MODULES for item in modules)
            or not isinstance(everyone_admin, bool)
        ):
            return
        name = name.strip()
        if not name or len(name) > TEAM_NAME_MAX_LENGTH:
            return
        destination = destination_hash.hex()
        with self._lock:
            self._teams[destination] = {
                "name": name,
                "modules": sorted(set(modules)),
                "everyone_admin": everyone_admin,
                "destination": destination,
                "join_code": encode_join_code(destination_hash),
                "last_seen": int(time.time()),
                "hops": RNS.Transport.hops_to(destination_hash),
                "via": "Reticulum announce",
            }

    def nearby(self, current_destination: bytes | None = None) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - TEAM_ANNOUNCE_MAX_AGE
        current = current_destination.hex() if current_destination else None
        with self._lock:
            teams = [
                {**team, "joined": team["destination"] == current}
                for team in self._teams.values()
                if team["last_seen"] >= cutoff
            ]
        return sorted(teams, key=lambda item: (-item["last_seen"], item["name"]))


class FieldSender:
    def __init__(
        self,
        config_dir: Path,
        data_dir: Path,
        gateway_hash: str | None,
        live_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.reticulum = RNS.Reticulum.get_instance() or RNS.Reticulum(
            str(config_dir)
        )
        self.identity = _load_or_create_identity(data_dir / "field.identity")
        self._send_lock = threading.Lock()
        self.gateway_hash = (
            self._parse_gateway_hash(gateway_hash) if gateway_hash is not None else None
        )
        self.last_delivery: dict[str, Any] | None = None
        self.live_callback = live_callback
        self._live_connect_lock = threading.Lock()
        self._live_lock = threading.RLock()
        self._live_link: Any | None = None
        self._live_channel: Any | None = None
        self._live_ready = threading.Event()
        self._live_rate_bps: float | None = None
        self._live_streams: set[str] = set()
        self._live_assembler = LiveVoiceAssembler()
        self.announce_handler = TeamAnnounceHandler()
        RNS.Transport.register_announce_handler(self.announce_handler)

    @staticmethod
    def _parse_gateway_hash(gateway_hash: str) -> bytes:
        try:
            parsed = bytes.fromhex(gateway_hash)
        except ValueError as exc:
            raise RuntimeError("Gateway destination must be hexadecimal") from exc
        if len(parsed) != RNS.Reticulum.TRUNCATED_HASHLENGTH // 8:
            raise RuntimeError("Gateway destination has the wrong length")
        return parsed

    def set_gateway_hash(self, gateway_hash: str) -> str:
        parsed = self._parse_gateway_hash(gateway_hash)
        with self._send_lock:
            self.close_live_link()
            self.gateway_hash = parsed
            self.last_delivery = None
        if not RNS.Transport.has_path(parsed):
            RNS.Transport.request_path(parsed)
        return parsed.hex()

    def _emit_live(self, frame: dict[str, Any]) -> None:
        if self.live_callback is not None:
            self.live_callback(frame)

    def _live_link_closed(self, link: Any) -> None:
        streams: list[str] = []
        with self._live_lock:
            if self._live_link is link:
                streams = list(self._live_streams)
                self._live_link = None
                self._live_channel = None
                self._live_ready.clear()
                self._live_rate_bps = None
                self._live_streams.clear()
        for stream_id in streams:
            self._emit_live({"type": "live.cancel", "stream_id": stream_id})
        self._emit_live({"type": "live.transport", "ready": False, "peers": 0})

    def _receive_live(self, message: Any) -> bool:
        if not isinstance(message, LiveVoiceMessage):
            return False
        try:
            if message.kind == LiveVoiceMessage.HELLO:
                self._live_ready.set()
                self._emit_live({"type": "live.transport", "ready": True, "peers": 1})
                return True
            stream_id = message.stream_uuid
            if message.kind == LiveVoiceMessage.START:
                metadata = validate_live_metadata(message.metadata())
                self._live_streams.add(stream_id)
                self._emit_live(
                    {"type": "live.start", "stream_id": stream_id, **metadata}
                )
                return True
            if stream_id not in self._live_streams:
                raise LiveVoiceError("unknown live voice stream")
            if message.kind == LiveVoiceMessage.CHUNK:
                audio = self._live_assembler.add(message)
                if audio is not None:
                    self._emit_live(
                        {
                            "type": "live.chunk",
                            "stream_id": stream_id,
                            "sequence": message.sequence,
                            "audio": audio,
                        }
                    )
                return True
            if message.kind in {LiveVoiceMessage.END, LiveVoiceMessage.CANCEL}:
                self._live_streams.discard(stream_id)
                self._live_assembler.clear_stream(message.stream_id)
                self._emit_live(
                    {
                        "type": (
                            "live.end"
                            if message.kind == LiveVoiceMessage.END
                            else "live.cancel"
                        ),
                        "stream_id": stream_id,
                    }
                )
                return True
        except LiveVoiceError as exc:
            RNS.log(f"Reticom rejected live voice frame: {exc}", RNS.LOG_WARNING)
        return True

    def ensure_live_link(self, timeout: float = 12.0) -> bool:
        with self._live_connect_lock:
            return self._ensure_live_link(timeout)

    def _ensure_live_link(self, timeout: float) -> bool:
        with self._live_lock:
            if (
                self._live_link is not None
                and self._live_link.status == RNS.Link.ACTIVE
                and self._live_ready.is_set()
            ):
                return True
            destination = self._destination(timeout)
            established = threading.Event()
            link = RNS.Link(
                destination, established_callback=lambda _: established.set()
            )
            self._live_link = link
            self._live_channel = None
            self._live_ready.clear()
            self._live_rate_bps = None
        if not established.wait(timeout) or link.status != RNS.Link.ACTIVE:
            self.close_live_link()
            raise TimeoutError("Could not establish a live Reticulum link")
        rate_bps = link.get_establishment_rate()
        if rate_bps is not None and rate_bps < LIVE_VOICE_MIN_BPS:
            self.close_live_link()
            raise LiveVoiceError(
                f"Reticulum route is too slow for live voice ({round(rate_bps)} bps)"
            )
        link.set_link_closed_callback(self._live_link_closed)
        channel = link.get_channel()
        channel.register_message_type(LiveVoiceMessage)
        channel.add_message_handler(self._receive_live)
        with self._live_lock:
            self._live_channel = channel
            self._live_rate_bps = rate_bps
        link.identify(self.identity)
        time.sleep(max(0.15, min(0.75, (link.rtt or 0.1) * 2)))
        _send_channel_message(channel, LiveVoiceMessage.hello(), timeout=timeout)
        if not self._live_ready.wait(timeout):
            self.close_live_link()
            raise TimeoutError("Command did not acknowledge the live voice channel")
        return True

    def close_live_link(self) -> None:
        with self._live_lock:
            link = self._live_link
            self._live_link = None
            self._live_channel = None
            self._live_ready.clear()
            self._live_rate_bps = None
            self._live_streams.clear()
        if link is not None and link.status not in {RNS.Link.CLOSED, RNS.Link.PENDING}:
            link.teardown()

    def live_ready(self) -> bool:
        with self._live_lock:
            return bool(
                self._live_link is not None
                and self._live_link.status == RNS.Link.ACTIVE
                and self._live_ready.is_set()
            )

    def _live_channel_ready(self) -> Any:
        self.ensure_live_link()
        with self._live_lock:
            channel = self._live_channel
        if channel is None:
            raise TimeoutError("Live Reticulum channel is unavailable")
        return channel

    def send_live_start(self, stream_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        channel = self._live_channel_ready()
        metadata = validate_live_metadata(
            {**metadata, "sender_hash": self.identity.hash.hex()}
        )
        _send_channel_message(
            channel,
            LiveVoiceMessage.control(LiveVoiceMessage.START, stream_id, metadata),
        )
        self._live_streams.add(stream_id)
        return {
            "transport": "authenticated Reticulum channel",
            "peers": 1,
            "rate_bps": round(self._live_rate_bps) if self._live_rate_bps else None,
        }

    def send_live_chunk(self, stream_id: str, sequence: int, audio: bytes) -> int:
        if stream_id not in self._live_streams:
            raise LiveVoiceError("unknown Field live voice stream")
        channel = self._live_channel_ready()
        messages = LiveVoiceMessage.chunk_messages(
            stream_id, sequence, audio, channel.mdu
        )
        for message in messages:
            _send_channel_message(channel, message)
        return 1

    def send_live_end(self, stream_id: str, cancel: bool = False) -> int:
        if stream_id not in self._live_streams:
            raise LiveVoiceError("unknown Field live voice stream")
        self._live_streams.discard(stream_id)
        channel = self._live_channel_ready()
        kind = LiveVoiceMessage.CANCEL if cancel else LiveVoiceMessage.END
        _send_channel_message(channel, LiveVoiceMessage.control(kind, stream_id))
        return 1

    def nearby_teams(self, joined_destination: str | None = None) -> list[dict[str, Any]]:
        current = bytes.fromhex(joined_destination) if joined_destination else None
        return self.announce_handler.nearby(current)

    def team_metadata(
        self, destination_hash: bytes | None = None
    ) -> dict[str, Any] | None:
        target = destination_hash or self.gateway_hash
        if target is None:
            return None
        destination = target.hex()
        for team in self.announce_handler.nearby(self.gateway_hash):
            if team["destination"] == destination:
                return team
        return None

    def team_name(self, destination_hash: bytes | None = None) -> str | None:
        metadata = self.team_metadata(destination_hash)
        return str(metadata["name"]) if metadata else None

    def _destination(self, timeout: float = 12.0) -> RNS.Destination:
        if self.gateway_hash is None:
            raise RuntimeError("Join a team first")
        deadline = time.monotonic() + timeout
        if not RNS.Transport.has_path(self.gateway_hash):
            RNS.Transport.request_path(self.gateway_hash)
        while time.monotonic() < deadline:
            if RNS.Transport.has_path(self.gateway_hash):
                identity = RNS.Identity.recall(self.gateway_hash)
                if identity is not None:
                    return RNS.Destination(
                        identity,
                        RNS.Destination.OUT,
                        RNS.Destination.SINGLE,
                        APP_NAME,
                        *ASPECTS,
                    )
            time.sleep(0.1)
        raise TimeoutError("No Reticulum path to the Command destination")

    def send_event(self, event: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        with self._send_lock:
            destination = self._destination()
            encoded = sign_event(event, self.identity)
            try:
                receipt = RNS.Packet(destination, encoded).send()
            except OSError as exc:
                raise ProtocolError(
                    "signed Reticulum event is too large; shorten the message"
                ) from exc
            receipt.set_timeout(timeout)
            deadline = time.monotonic() + timeout + 1
            while receipt.get_status() == RNS.PacketReceipt.SENT and time.monotonic() < deadline:
                time.sleep(0.05)
            delivered = receipt.get_status() == RNS.PacketReceipt.DELIVERED
            result = {
                "event_id": event["id"],
                "delivered": delivered,
                "status": "delivered" if delivered else "failed",
                "rtt_ms": round(receipt.get_rtt() * 1000, 2) if delivered else None,
                "packet_bytes": len(encoded),
                "gateway": self.gateway_hash.hex(),
            }
            self.last_delivery = result
            if not delivered:
                raise TimeoutError("Reticulum packet was not proven by Command")
            return result

    def request_tasks(self, timeout: float = 12.0) -> dict[str, Any]:
        with self._send_lock:
            destination = self._destination(timeout)
            established = threading.Event()
            link = RNS.Link(
                destination,
                established_callback=lambda _: established.set(),
            )
            try:
                if not established.wait(timeout) or link.status != RNS.Link.ACTIVE:
                    raise TimeoutError("Could not establish an encrypted Reticulum link")
                link.identify(self.identity)
                time.sleep(max(0.15, min(0.75, (link.rtt or 0.1) * 2)))
                receipt = link.request(
                    "/tasks", data=b"", timeout=timeout, max_response_size=64_000
                )
                if receipt is False:
                    raise TimeoutError("Could not send task request over Reticulum")
                deadline = time.monotonic() + timeout + 1
                while not receipt.concluded() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if receipt.get_status() != RNS.RequestReceipt.READY:
                    raise TimeoutError("Task request was not answered over Reticulum")
                response = receipt.get_response()
                if not isinstance(response, bytes):
                    raise ValueError("Invalid task response")
                payload = json.loads(response.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Invalid task response")
                if payload.get("error"):
                    raise PermissionError(str(payload["error"]))
                payload["network"] = {
                    "via": "authenticated Reticulum link",
                    "response_ms": round(receipt.get_response_time() * 1000, 2),
                    "gateway": self.gateway_hash.hex(),
                }
                return payload
            finally:
                if link.status not in {RNS.Link.CLOSED, RNS.Link.PENDING}:
                    link.teardown()

    def _identified_request(
        self,
        path: str,
        data: Any,
        *,
        timeout: float,
        max_response_size: int,
    ) -> tuple[Any, float]:
        destination = self._destination(timeout)
        established = threading.Event()
        link = RNS.Link(destination, established_callback=lambda _: established.set())
        try:
            if not established.wait(timeout) or link.status != RNS.Link.ACTIVE:
                raise TimeoutError("Could not establish an encrypted Reticulum link")
            link.identify(self.identity)
            time.sleep(max(0.15, min(0.75, (link.rtt or 0.1) * 2)))
            receipt = link.request(
                path,
                data=data,
                timeout=timeout,
                max_response_size=max_response_size,
            )
            if receipt is False:
                raise TimeoutError(f"Could not send {path} request over Reticulum")
            deadline = time.monotonic() + timeout + 1
            while not receipt.concluded() and time.monotonic() < deadline:
                time.sleep(0.05)
            if receipt.get_status() != RNS.RequestReceipt.READY:
                raise TimeoutError(f"{path} request was not answered over Reticulum")
            return receipt.get_response(), receipt.get_response_time()
        finally:
            if link.status not in {RNS.Link.CLOSED, RNS.Link.PENDING}:
                link.teardown()

    @staticmethod
    def _json_response(response: Any, label: str) -> dict[str, Any]:
        if not isinstance(response, bytes):
            raise ValueError(f"Invalid {label} response")
        payload = json.loads(response.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid {label} response")
        if payload.get("error"):
            raise PermissionError(str(payload["error"]))
        return payload

    def request_feed(self, timeout: float = 15.0) -> dict[str, Any]:
        with self._send_lock:
            response, response_time = self._identified_request(
                "/feed", b"", timeout=timeout, max_response_size=256_000
            )
            payload = self._json_response(response, "feed")
            payload["network"] = {
                "via": "authenticated Reticulum link",
                "response_ms": round(response_time * 1000, 2),
                "gateway": self.gateway_hash.hex(),
            }
            return payload

    def request_private_messages(self, timeout: float = 15.0) -> dict[str, Any]:
        with self._send_lock:
            response, response_time = self._identified_request(
                "/private/messages", b"", timeout=timeout, max_response_size=192_000
            )
            payload = self._json_response(response, "private messages")
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError("Invalid private message response")
            payload["network"] = {
                "via": "private identified Reticulum link",
                "response_ms": round(response_time * 1000, 2),
                "gateway": self.gateway_hash.hex(),
            }
            return payload

    def request_admin(
        self, action: str, payload: dict[str, Any] | None = None, timeout: float = 20.0
    ) -> dict[str, Any]:
        with self._send_lock:
            response, response_time = self._identified_request(
                "/admin",
                {"action": action, "payload": payload or {}},
                timeout=timeout,
                max_response_size=16_384,
            )
            result = self._json_response(response, "team admin")
            result["network"] = {
                "via": "team admin · identified Reticulum link",
                "response_ms": round(response_time * 1000, 2),
                "gateway": self.gateway_hash.hex(),
            }
            return result

    def send_private_message(
        self, event: dict[str, Any], timeout: float = 20.0
    ) -> dict[str, Any]:
        with self._send_lock:
            envelope = sign_event(event, self.identity, packet_limit=False)
            response, response_time = self._identified_request(
                "/private/send",
                {"envelope": envelope},
                timeout=timeout,
                max_response_size=4096,
            )
            payload = self._json_response(response, "private message")
            accepted = bool(payload.get("accepted"))
            result = {
                "event_id": event["id"],
                "accepted": accepted,
                "status": "mailbox" if accepted else "failed",
                "rtt_ms": round(response_time * 1000, 2),
                "packet_bytes": len(envelope),
                "gateway": self.gateway_hash.hex(),
                "transport": "private identified Reticulum link",
            }
            if not accepted:
                raise TimeoutError("Command did not accept the private message")
            return result

    def send_private_ptt(
        self, event: dict[str, Any], audio: bytes, timeout: float = 45.0
    ) -> dict[str, Any]:
        with self._send_lock:
            envelope = sign_event(event, self.identity, packet_limit=False)
            response, response_time = self._identified_request(
                "/private/ptt",
                {"envelope": envelope, "audio": audio},
                timeout=timeout,
                max_response_size=4096,
            )
            payload = self._json_response(response, "private voice")
            accepted = bool(payload.get("accepted"))
            result = {
                "event_id": event["id"],
                "accepted": accepted,
                "status": "mailbox" if accepted else "failed",
                "rtt_ms": round(response_time * 1000, 2),
                "packet_bytes": len(envelope) + len(audio),
                "gateway": self.gateway_hash.hex(),
                "transport": "private voice · identified Reticulum link",
            }
            if not accepted:
                raise TimeoutError("Command did not accept the private voice clip")
            return result

    def send_ptt(
        self, event: dict[str, Any], audio: bytes, timeout: float = 45.0
    ) -> dict[str, Any]:
        with self._send_lock:
            envelope = sign_event(event, self.identity)
            response, response_time = self._identified_request(
                "/ptt",
                {"envelope": envelope, "audio": audio},
                timeout=timeout,
                max_response_size=4096,
            )
            payload = self._json_response(response, "voice")
            result = {
                "event_id": event["id"],
                "delivered": bool(payload.get("accepted")),
                "status": "delivered" if payload.get("accepted") else "failed",
                "rtt_ms": round(response_time * 1000, 2),
                "packet_bytes": len(envelope) + len(audio),
                "gateway": self.gateway_hash.hex(),
            }
            self.last_delivery = result
            return result

    def send_link_event(
        self, event: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        with self._send_lock:
            envelope = sign_event(event, self.identity, packet_limit=False)
            response, response_time = self._identified_request(
                "/event",
                {"envelope": envelope},
                timeout=timeout,
                max_response_size=4096,
            )
            payload = self._json_response(response, "event")
            accepted = bool(payload.get("accepted"))
            result = {
                "event_id": event["id"],
                "delivered": accepted,
                "status": "delivered" if accepted else "failed",
                "rtt_ms": round(response_time * 1000, 2),
                "packet_bytes": len(envelope),
                "gateway": self.gateway_hash.hex(),
                "transport": "authenticated Reticulum link",
            }
            self.last_delivery = result
            return result

    def request_audio(
        self, clip_id: str, timeout: float = 30.0
    ) -> tuple[bytes, str]:
        with self._send_lock:
            response, _ = self._identified_request(
                "/audio", clip_id, timeout=timeout, max_response_size=600_000
            )
            if isinstance(response, bytes):
                payload = self._json_response(response, "voice clip")
                raise ValueError(str(payload))
            if not isinstance(response, dict):
                raise ValueError("Invalid voice clip response")
            audio = response.get("audio")
            mime_type = response.get("mime_type")
            if not isinstance(audio, bytes) or not isinstance(mime_type, str):
                raise ValueError("Invalid voice clip response")
            return audio, mime_type

    def request_transcription(
        self, clip_id: str, start: bool = False, timeout: float = 30.0
    ) -> dict[str, Any]:
        with self._send_lock:
            response, response_time = self._identified_request(
                "/transcription",
                {"clip_id": clip_id, "start": start},
                timeout=timeout,
                max_response_size=16_384,
            )
            if isinstance(response, bytes):
                payload = self._json_response(response, "transcription")
            elif isinstance(response, dict):
                payload = response
            else:
                raise ValueError("Invalid transcription response")
            payload["network"] = {
                "via": "authenticated Reticulum link",
                "response_ms": round(response_time * 1000, 2),
            }
            return payload

    def state(self) -> dict[str, Any]:
        path_known = bool(
            self.gateway_hash is not None
            and RNS.Transport.has_path(self.gateway_hash)
        )
        return {
            "identity": self.identity.hash.hex(),
            "gateway": self.gateway_hash.hex() if self.gateway_hash is not None else None,
            "path_known": path_known,
            "hops": RNS.Transport.hops_to(self.gateway_hash) if path_known else None,
            "last_delivery": self.last_delivery,
            "live_voice": {
                "ready": self.live_ready(),
                "rate_bps": round(self._live_rate_bps) if self._live_rate_bps else None,
            },
            "interfaces": _json_safe(self.reticulum.get_interface_stats()),
        }

    def stop(self) -> None:
        self.close_live_link()
        RNS.Transport.deregister_announce_handler(self.announce_handler)
