from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
import RNS
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .bootstrap import network_settings, write_custom_node
from .continuity import TeamContinuity, atomic_json, endpoint
from .live_voice import LIVE_VOICE_MAX_CHUNK, LiveVoiceError
from .live_heading import HeadingClient, clock_ms, interval as heading_interval, TTL_MS
from .offline_maps import OfflineMapError, OfflineMapStore
from .offline_routing import offline_routing_router
from .intel_packs import intel_router
from .landmarks import landmark_router
from .outbox import FieldOutbox
from .protocol import ProtocolError, new_event
from .ptt import PTT_MAX_BYTES, PTTError, PTTStore
from .qr import decode_join_code_image, join_code_svg
from .store import EventStore, PrivateMessageStore
from .team import TeamCodeError, TeamMembership, TeamProfile, decode_join_code
from .tasks import TaskError, TaskStore
from .transport import FieldSender, GatewayReceiver, _load_or_create_identity
from .transcription import LocalTranscriber, TranscriptionError
from .user import UserProfile
from .waypoints import WaypointArrivalDetector


def local_membership_request(request: Request) -> bool:
    """Recognize the explicitly configured localhost-only Docker port forward."""
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    if origin and urlsplit(origin).netloc != request.url.netloc:
        return False
    if not request.client:
        return False
    if request.client.host in {"127.0.0.1", "::1"}:
        return True
    return (os.environ.get("RETICOM_LOCAL_PORT_FORWARD") == "1"
            and request.url.hostname in {"localhost", "127.0.0.1", "::1"})


def create_app(
    role: str,
    config_dir: Path,
    data_dir: Path,
    gateway_hash: str | None = None,
) -> FastAPI:
    if role == "gateway":
        from .command import create_command_app

        from functools import partial
        return create_command_app(config_dir, data_dir, partial(create_node_app, command_ai=True))
    return create_node_app(role, config_dir, data_dir, gateway_hash)


def create_node_app(
    role: str,
    config_dir: Path,
    data_dir: Path,
    gateway_hash: str | None = None,
    *, command_ai: bool = False,
) -> FastAPI:
    from .speech_markers import SpeechMarkerProcessor, MAX_AGE
    speech_ai = SpeechMarkerProcessor(data_dir / "speech-markers") if command_ai and os.getenv("RETICOM_AI_ENABLED", "1") != "0" else None
    speech_ai_task = None
    speech_ai_stopping = False
    speech_ai_clips = {}
    speech_ai_sources = {}
    private_ai_dir = data_dir / "private-ai-markers"
    private_ai_dir.mkdir(parents=True, exist_ok=True)
    def private_ai_markers():
        return [item for path in private_ai_dir.glob("*.json")
                if (item := json.loads(path.read_text())).get("expires_at", 0) > time.time()]
    command_position_path = data_dir / "command-position.json"
    def command_position():
        return json.loads(command_position_path.read_text()) if command_position_path.exists() else None
    service: GatewayReceiver | FieldSender | None = None
    hosted_service: GatewayReceiver | None = None
    store: EventStore | None = None
    private_store: PrivateMessageStore | None = None
    team_profile: TeamProfile | None = None
    team_membership: TeamMembership | None = None
    task_store: TaskStore | None = None
    user_profile: UserProfile | None = None
    ptt_store: PTTStore | None = None
    field_ptt_store: PTTStore | None = None
    field_transcriber: LocalTranscriber | None = None
    transcriber: LocalTranscriber | None = None
    field_event_stores: dict[str, EventStore] = {}
    field_mission_lock = asyncio.Lock()
    navigation_lock = asyncio.Lock()
    field_outbox: FieldOutbox | None = None
    field_flush_task: asyncio.Task[None] | None = None
    continuity_task: asyncio.Task[None] | None = None
    continuity_stopping = False
    subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
    voice_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
    heading_subscribers: dict[asyncio.Event, dict[str, Any]] = {}
    event_loop: asyncio.AbstractEventLoop | None = None
    waypoint_arrivals = WaypointArrivalDetector()
    offline_maps = OfflineMapStore(data_dir / "offline-maps")
    offline_download_tasks: dict[str, asyncio.Task[None]] = {}

    def active_gateway() -> GatewayReceiver | None:
        if isinstance(service, GatewayReceiver):
            return service
        return hosted_service

    def field_hosts_team() -> bool:
        return bool(
            role == "field"
            and hosted_service is not None
            and team_membership is not None
            and team_membership.destination == (
                hosted_service.continuity.root if hosted_service.continuity else hosted_service.destination.hash.hex()
            )
            and (hosted_service.continuity is None or hosted_service.continuity.ready())
        )

    def field_team_state(
        discovered_name: str | None = None,
        discovered_modules: Any = None,
        discovered_everyone_admin: Any = None,
    ) -> dict[str, Any]:
        assert team_membership is not None
        state = team_membership.state(
            discovered_name, discovered_modules, discovered_everyone_admin
        )
        state["hosted"] = field_hosts_team()
        state["membership_status"] = "approved" if field_hosts_team() else getattr(service, "membership_status", "unknown")
        state["admin"] = field_hosts_team() or (state["everyone_admin"] and state["membership_status"] == "approved")
        return state

    def field_event_store(destination: str | None = None) -> EventStore | None:
        if role != "field" or team_membership is None:
            return None
        team_destination = destination or team_membership.destination
        if not team_destination:
            return None
        if team_destination not in field_event_stores:
            field_event_stores[team_destination] = EventStore(
                data_dir / "field-cache" / team_destination / "events.sqlite3"
            )
        return field_event_stores[team_destination]

    def mission_inbox(destination=None):
        from .mission import MissionInbox
        cache = field_event_store(destination)
        return MissionInbox(cache) if cache is not None else None

    def mission_status(destination=None):
        inbox = mission_inbox(destination)
        status = inbox.status() if inbox else {"state": "pending"}
        return {k: v for k, v in status.items() if k not in {"team", "tasks", "private_events"}}

    def cache_field_feed(events: Any) -> None:
        cache = field_event_store()
        if cache is None or not isinstance(events, list):
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            network = event.get("network")
            network = network if isinstance(network, dict) else {}
            sender_hash = str(network.get("sender_hash", "")).strip()
            if not sender_hash:
                continue
            clean_event = {key: value for key, value in event.items() if key != "network"}
            try:
                inserted = cache.insert(
                    clean_event,
                    sender_hash,
                    packet_hash=network.get("packet_hash"),
                    interface_name=network.get("interface") or "Authenticated Reticulum feed",
                )
                if not inserted:
                    cache.mark_verified(
                        clean_event["id"],
                        packet_hash=network.get("packet_hash"),
                        interface_name=network.get("interface")
                        or "Authenticated Reticulum feed",
                    )
                if field_outbox is not None:
                    field_outbox.remove(clean_event["id"])
                if isinstance(event.get("report_view"), dict):
                    cache.cache_report_view(clean_event["id"], event["report_view"])
            except (KeyError, TypeError, ValueError):
                continue

    def queued_delivery(event: dict[str, Any], destination: str) -> dict[str, Any]:
        assert field_outbox is not None
        return {
            "event_id": event["id"],
            "delivered": False,
            "status": "queued",
            "rtt_ms": None,
            "packet_bytes": None,
            "gateway": destination,
            "queued": field_outbox.count(destination),
            "transport": "local outbox · encrypted Reticulum when reachable",
        }

    def publish_from_reticulum(event: dict[str, Any]) -> None:
        if event.get("type") == "task.completed" and task_store is not None:
            task_store.complete(
                event.get("task_id"),
                str(event.get("callsign", "")),
                str(event.get("network", {}).get("sender_hash", "")),
            )
        if (
            event.get("type") == "position.updated"
            and store is not None
            and team_profile is not None
            and team_profile.name is not None
            and user_profile is not None
            and active_gateway() is not None
        ):
            for arrival in waypoint_arrivals.update(
                event,
                store.recent(500, since=team_profile.created_at),
            ):
                arrival_event = new_event(
                    "waypoint.arrived",
                    user_profile.callsign,
                    icon=user_profile.icon,
                    color=user_profile.color,
                    waypoint_id=arrival["waypoint_id"],
                    waypoint_label=arrival["waypoint_label"],
                    operator_callsign=arrival["operator_callsign"],
                    distance_m=arrival["distance_m"],
                )
                gateway = active_gateway()
                assert gateway is not None
                gateway.publish_event(arrival_event)
        if event_loop is None:
            return

        def publish() -> None:
            for queue in tuple(subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(event)

        event_loop.call_soon_threadsafe(publish)

    def publish_live_from_reticulum(frame: dict[str, Any]) -> None:
        if event_loop is None:
            return

        if frame.get("type") == "heading.sample":
            def latest_heading() -> None:
                for signal, pending in tuple(heading_subscribers.items()):
                    key = frame["sender_hash"]
                    if key in pending or len(pending) < 128:
                        pending[key] = frame
                        signal.set()
            event_loop.call_soon_threadsafe(latest_heading)
            return

        def publish() -> None:
            for queue in tuple(voice_subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(frame)

        event_loop.call_soon_threadsafe(publish)

    def clear_communications_sync() -> dict[str, int]:
        if store is None or team_profile is None or ptt_store is None or transcriber is None:
            raise RuntimeError("communication cleanup is unavailable")
        cleared = store.clear_communications(team_profile.created_at)
        audio_removed = 0
        transcripts_removed = 0
        for clip_id in cleared["clip_ids"]:
            if ptt_store.delete(clip_id):
                audio_removed += 1
            if transcriber.delete(clip_id):
                transcripts_removed += 1
        return {
            "events": cleared["events"],
            "audio": audio_removed,
            "transcripts": transcripts_removed,
        }

    def apply_admin_action(
        action: str, payload: dict[str, Any], sender_hash: str = "local-owner"
    ) -> dict[str, Any]:
        del sender_hash
        gateway = active_gateway()
        if gateway is None or team_profile is None or team_profile.name is None:
            raise RuntimeError("create or join a team before using admin settings")
        if action == "modules.set":
            team_profile.set_modules(payload.get("modules"))
        elif action == "permissions.set":
            team_profile.set_everyone_admin(payload.get("everyone_admin"))
        elif action == "communications.clear":
            return {"cleared": clear_communications_sync()}
        else:
            raise ValueError("unsupported team admin action")
        gateway.set_team(
            team_profile.name,
            team_profile.modules,
            team_profile.created_at,
            team_profile.everyone_admin,
        )
        return {"team": team_profile.state(gateway.destination.hash)}

    def start_gateway_stack(
        stack_data_dir: Path, profile: TeamProfile, replication_root: str | None = None
    ) -> GatewayReceiver:
        nonlocal store, private_store, team_profile, task_store, ptt_store, transcriber
        store = EventStore(stack_data_dir / "events.sqlite3")
        private_store = PrivateMessageStore(stack_data_dir / "private-messages.sqlite3")
        task_store = TaskStore(stack_data_dir / "tasks.sqlite3")
        ptt_store = PTTStore(stack_data_dir / "ptt")
        transcriber = LocalTranscriber(stack_data_dir / "transcripts")
        team_profile = profile

        def task_response() -> bytes:
            assert task_store is not None
            return json.dumps(
                {"module": "tasks", "enabled": True, "tasks": task_store.list()},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        def feed_response(mission=False) -> bytes:
            assert store is not None and team_profile is not None
            events = store.mission_events(since=team_profile.created_at) if mission else store.recent(60, since=team_profile.created_at)
            if not mission:
                event_ids = {item["id"] for item in events}
                events.extend(item for item in store.navigation_events(since=team_profile.created_at) if item["id"] not in event_ids)
            compact = []
            for event in events:
                network = event.get("network", {})
                compact.append(
                    {
                        **{key: value for key, value in event.items() if key != "network"},
                        "network": {
                            "verified": True,
                            "sender_hash": network.get("sender_hash"),
                            "received_at": network.get("received_at"),
                            "became_active": network.get("became_active", False),
                            **(
                                {"position_silence_seconds": network["position_silence_seconds"]}
                                if network.get("became_active")
                                else {}
                            ),
                        },
                    }
                )
            return json.dumps(
                {
                    "events": compact,
                    "team": {
                        "name": team_profile.name,
                        "modules": team_profile.modules,
                        "everyone_admin": team_profile.everyone_admin,
                    },
                    **({"tasks": task_store.list() if task_store and "tasks" in team_profile.modules else []} if mission else {}),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        def remove_operator_media(clip_id: str) -> None:
            ptt_store.delete(clip_id)
            transcriber.delete(clip_id)

        def save_and_transcribe(clip_id: str, audio: bytes, mime_type: str) -> None:
            assert ptt_store is not None and transcriber is not None
            ptt_store.save(clip_id, audio, mime_type)
            transcriber.schedule(clip_id, audio, mime_type)

        def transcription_response(clip_id: str, start: bool) -> dict[str, Any]:
            assert ptt_store is not None and transcriber is not None
            result = transcriber.status(clip_id)
            if result["status"] in {"unavailable", "error"} and start:
                audio, mime_type = ptt_store.read(clip_id)
                transcriber.schedule(clip_id, audio, mime_type)
                return {"status": "processing", "clip_id": clip_id}
            return result

        gateway = GatewayReceiver(
            config_dir,
            stack_data_dir,
            store,
            team_name=team_profile.name,
            team_modules=team_profile.modules,
            task_response=task_response,
            feed_response=feed_response,
            mission_response=lambda: feed_response(True),
            save_ptt=save_and_transcribe,
            audio_response=ptt_store.read,
            transcription_response=transcription_response,
            admin_response=apply_admin_action,
            event_callback=publish_from_reticulum,
            live_callback=publish_live_from_reticulum,
            private_store=private_store,
            remove_operator_media=remove_operator_media,
            team_created_at=team_profile.created_at,
            everyone_admin=team_profile.everyone_admin,
        )
        gateway.continuity = TeamContinuity(gateway, profile, task_store, private_store, ptt_store, replication_root)
        if role == "field" and not replication_root:
            local_identity = _load_or_create_identity(data_dir / "field.identity")
            if not gateway.membership.approved(local_identity.hash.hex()):
                gateway.membership.decide(local_identity.hash.hex(), "approved", user_profile.callsign if user_profile else "Owner")
        gateway.enforce_membership()
        # A newly approved replica must finish its initial data/audio sync before
        # it serves application requests. Replication endpoints remain available.
        if replication_root:
            for route, handler in {
                "/feed": gateway._feed_request, "/tasks": gateway._tasks_request,
                "/event": gateway._event_request, "/ptt": gateway._ptt_request,
                "/audio": gateway._audio_request, "/transcription": gateway._transcription_request,
                "/admin": gateway._admin_request, "/private/messages": gateway._private_messages_request,
                "/private/send": gateway._private_send_request, "/private/ptt": gateway._private_ptt_request,
            }.items():
                def guard(handler):
                    def gated(path, data, request_id, link_id, remote_identity, requested_at):
                        if not gateway.continuity.ready():
                            return b'{"error":"Backup is still synchronizing"}'
                        return handler(path, data, request_id, link_id, remote_identity, requested_at)
                    return gated
                gateway.destination.register_request_handler(route, response_generator=guard(handler), allow=RNS.Destination.ALLOW_ALL)
        return gateway

    def publish_field_event_sync(
        event: dict[str, Any], *, linked: bool = False
    ) -> dict[str, Any]:
        if not isinstance(service, FieldSender):
            raise RuntimeError("Field transport is unavailable")
        if field_hosts_team():
            assert hosted_service is not None
            return hosted_service.publish_local_field_event(
                event, service.identity, admin=True
            )
        if team_membership is None or not team_membership.destination or field_outbox is None:
            raise RuntimeError("Join a team first")
        destination = team_membership.destination
        cache = field_event_store(destination)
        assert cache is not None
        report_admin = False
        if event["type"] == "report.dismissed":
            target = cache.automatic_report_source(event["message_id"])
            if target is None:
                raise ProtocolError("automatic report source not found in the local team feed")
            report_admin = target["sender_hash"] != service.identity.hash.hex()
            if report_admin and not team_membership.everyone_admin:
                raise ProtocolError("only the reporter or a team admin can remove this automatic report")
        if event["type"] == "marker.status":
            target = cache.map_event(event["marker_id"], "marker.created")
            if target is None:
                raise ProtocolError("marker not found in the local team feed")
            if target["sender_hash"] != service.identity.hash.hex() and not team_membership.everyone_admin:
                raise ProtocolError("only the reporter or a team admin can change this marker")
        cache.insert(
            event,
            service.identity.hash.hex(),
            interface_name="Local team admin · Reticulum outbox" if report_admin else "Local device · Reticulum outbox",
            delivery_status="queued",
        )
        field_outbox.add(destination, event, linked=linked)
        return queued_delivery(event, destination)

    def flush_field_outbox_sync(destination: str) -> None:
        if (
            not isinstance(service, FieldSender)
            or field_outbox is None
            or service.gateway_hash is None
            or service.gateway_hash.hex() != destination
        ):
            return
        cache = field_event_store(destination)
        for item in field_outbox.pending(destination):
            event = item["event"]
            try:
                if item["transport"] == "ptt":
                    if field_ptt_store is None:
                        raise RuntimeError("local voice storage is unavailable")
                    audio, _ = field_ptt_store.read(event["clip_id"])
                    delivery = service.send_ptt(event, audio, timeout=8.0)
                elif item["linked"]:
                    delivery = service.send_link_event(event, timeout=6.0)
                else:
                    delivery = service.send_event(event, timeout=6.0)
                if not delivery.get("delivered"):
                    raise TimeoutError("Reticulum delivery was not accepted")
            except (PermissionError, ProtocolError, RuntimeError, TimeoutError, ValueError) as exc:
                field_outbox.failed(event["id"], str(exc))
                break
            field_outbox.remove(event["id"])
            if cache is not None:
                cache.mark_verified(
                    event["id"],
                    packet_hash=delivery.get("packet_hash"),
                    interface_name="Team admin · Authenticated Reticulum Link" if event["type"] == "report.dismissed" else "Authenticated Reticulum delivery",
                )

    def schedule_field_outbox_flush() -> None:
        nonlocal field_flush_task
        if (
            role != "field"
            or field_flush_task is not None
            or team_membership is None
            or not team_membership.destination
            or field_outbox is None
            or field_outbox.count(team_membership.destination) == 0
            or (not field_hosts_team() and getattr(service, "membership_status", "unknown") != "approved")
        ):
            return
        destination = team_membership.destination

        async def flush() -> None:
            nonlocal field_flush_task
            try:
                await asyncio.to_thread(flush_field_outbox_sync, destination)
            finally:
                field_flush_task = None

        field_flush_task = asyncio.create_task(flush())

    def publish_field_ptt_sync(
        event: dict[str, Any], audio: bytes
    ) -> dict[str, Any]:
        if not isinstance(service, FieldSender):
            raise RuntimeError("Field transport is unavailable")
        if field_hosts_team():
            assert hosted_service is not None
            return hosted_service.publish_local_field_ptt(event, audio, service.identity)
        if (
            team_membership is None
            or not team_membership.destination
            or field_outbox is None
            or field_ptt_store is None
        ):
            raise RuntimeError("Join a team first")
        destination = team_membership.destination
        cache = field_event_store(destination)
        assert cache is not None
        field_ptt_store.save(event["clip_id"], audio, event["mime_type"])
        cache.insert(
            event,
            service.identity.hash.hex(),
            interface_name="Local device · Reticulum voice outbox",
            delivery_status="queued",
        )
        field_outbox.add(
            destination,
            event,
            linked=True,
            transport="ptt",
        )
        return queued_delivery(event, destination)

    def publish_field_private_sync(
        event: dict[str, Any], audio: bytes | None = None
    ) -> dict[str, Any]:
        if not isinstance(service, FieldSender):
            raise RuntimeError("Field transport is unavailable")
        if field_hosts_team():
            assert hosted_service is not None
            return hosted_service.publish_local_private(event, service.identity, audio)
        if audio is None:
            return service.send_private_message(event)
        return service.send_private_ptt(event, audio)

    async def maintain_field_continuity():
        nonlocal hosted_service
        checked_team, checked_at = None, 0
        while not continuity_stopping:
            try:
                if isinstance(service, FieldSender) and team_membership is not None and team_membership.joined:
                    if not field_hosts_team() and (checked_team != team_membership.destination or time.monotonic() - checked_at > 15):
                        checked_team, checked_at = team_membership.destination, time.monotonic()
                        await asyncio.to_thread(service.request_membership, user_profile.callsign)
                    directory = service.host_directory
                    if directory is not None:
                        gateway = active_gateway()
                        if gateway and gateway.continuity and gateway.continuity.root == team_membership.destination:
                            directory.accept(gateway.continuity.directory.envelope)
                        else:
                            await asyncio.to_thread(directory.refresh, service.identity)
                        choice = data_dir / "backup-choice.json"
                        if hosted_service is None and choice.exists() and directory.envelope:
                            requested = json.loads(choice.read_text())
                            if requested.get("team") == team_membership.destination:
                                replica_dir = data_dir / "backups" / team_membership.destination
                                identity = _load_or_create_identity(replica_dir / "gateway.identity")
                                if any(h["destination"] == endpoint(identity) for h in directory.envelope["policy"]["hosts"]):
                                    atomic_json(replica_dir / "continuity" / "policy.json", directory.envelope)
                                    hosted_service = start_gateway_stack(replica_dir, TeamProfile(replica_dir / "team.json"), team_membership.destination)
                        if field_hosts_team() and hosted_service is not None:
                            team_membership.update_metadata(team_profile.name, team_profile.modules, team_profile.everyone_admin)
                    # Retry queued work without depending on the UI polling /feed.
                    if field_hosts_team() and field_outbox is not None:
                        for item in field_outbox.pending(team_membership.destination):
                            event = item["event"]
                            if item["transport"] == "ptt":
                                audio, _ = field_ptt_store.read(event["clip_id"])
                                hosted_service.publish_local_field_ptt(event, audio, service.identity)
                            else:
                                hosted_service.publish_local_field_event(event, service.identity, admin=True)
                            field_outbox.remove(event["id"])
                    else:
                        schedule_field_outbox_flush()
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                # Connectivity failure is expected; keep local work and retry.
                if active_gateway() and active_gateway().continuity:
                    active_gateway().continuity.last_error = str(exc)
            await asyncio.sleep(3)

    async def process_incoming_speech():
        while not speech_ai_stopping:
            try:
                if role == "gateway":
                    events = store.mission_events(since=team_profile.created_at) if store and team_profile and team_profile.name else []
                elif team_membership and team_membership.joined:
                    response = await feed()
                    events = json.loads(response.body).get("events", [])
                else:
                    events = []
                public_events = events
                private_events = []
                if events:
                    try:
                        private_events = json.loads((await private_messages()).body).get("messages", [])
                    except (HTTPException, RuntimeError):
                        pass
                events = [*events, *private_events]
                for source in sorted(events, key=lambda event: event["created_at"]):
                    if speech_ai_stopping:
                        return
                    if source["type"] not in {"ptt.broadcast", "chat.message", "private.message", "private.ptt"} or source.get("automatic_report_dismissed"):
                        continue
                    if not 0 <= time.time() - source["created_at"] <= MAX_AGE:
                        continue
                    speech_ai_sources[source["id"]] = True
                    if len(speech_ai_sources) > 200:
                        speech_ai_sources.pop(next(iter(speech_ai_sources)))
                    if source["type"] in {"ptt.broadcast", "private.ptt"}:
                        speech_ai_clips[source["clip_id"]] = source["id"]
                    saved = speech_ai.read(source["id"])
                    if saved and (saved.get("state") in {"created", "cancelled", "no_marker", "needs_clarification"}
                        or saved.get("state") == "error" and time.time() - saved["updated_at"] < 60):
                        continue
                    if source["type"] in {"chat.message", "private.message"}:
                        transcript = {"status": "ready", "text": source["message"]}
                    else:
                        current_transcriber = transcriber if role == "gateway" or field_hosts_team() else field_transcriber
                        if current_transcriber is None:
                            continue
                        transcript = current_transcriber.status(source["clip_id"])
                        if transcript["status"] == "error" and saved and saved.get("state") == "transcribing":
                            speech_ai.write(source, {"state": "error", "reason": transcript.get("error", "Transcription failed")})
                            continue
                        if transcript["status"] in {"unavailable", "error"}:
                            recording = await read_audio(source["clip_id"])
                            current_transcriber.schedule(source["clip_id"], recording.body, recording.media_type)
                            speech_ai.write(source, {"state": "transcribing"})
                            continue
                        if transcript["status"] == "processing":
                            continue
                        if transcript["status"] == "no_speech":
                            speech_ai.write(source, {"state": "no_marker", "reason": "No speech detected"})
                            continue
                        if transcript["status"] != "ready":
                            continue
                    is_private = source["type"].startswith("private.")
                    participants = {source.get("recipient_hash"), source.get("network", {}).get("sender_hash")}
                    peer = next((identity for identity in participants if identity != service.identity.hash.hex()), None)
                    context_events = [*public_events]
                    if is_private:
                        context_events = [e for e in public_events if e["type"] == "position.updated"]
                        context_events += [e for e in private_events if {e.get("recipient_hash"), e.get("network", {}).get("sender_hash")} == participants]
                        context_events += [e for e in private_ai_markers() if e.get("private_peer") == peer]
                    def publish_ai(event):
                        if speech_ai_stopping:
                            raise RuntimeError("Command is stopping")
                        if is_private:
                            if event["type"] == "marker.deleted":
                                (private_ai_dir / (str(uuid.UUID(event["marker_id"])) + ".json")).unlink(missing_ok=True)
                            else:
                                atomic_json(private_ai_dir / (event["id"] + ".json"), {**event, "private_ai": True, "private_peer": peer,
                                    "network": {"sender_hash": service.identity.hash.hex(), "verified": False, "received_at": int(time.time())}})
                            return
                        current_store = store if role == "gateway" or field_hosts_team() else field_event_store()
                        current_events = current_store.mission_events() if current_store else []
                        if not any(e["id"] == source["id"] and not e.get("automatic_report_dismissed") for e in current_events):
                            raise RuntimeError("The source report was removed while AI was processing")
                        if time.time() - source["created_at"] > MAX_AGE:
                            raise RuntimeError("The source report is too old to map automatically")
                        if role == "gateway":
                            return service.publish_event(event)
                        return publish_field_event_sync(event, linked=True)
                    await asyncio.to_thread(speech_ai.process, source, transcript["text"], context_events,
                        service.identity.hash.hex(), user_profile.callsign, publish_ai, command_position=command_position())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if speech_ai:
                    speech_ai.status = {"state": "error", "model": speech_ai.interpreter.model, "reason": str(exc)}
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal service, hosted_service, team_membership, user_profile, event_loop
        nonlocal field_outbox, field_flush_task, field_ptt_store, field_transcriber
        nonlocal continuity_task, continuity_stopping, speech_ai_task, speech_ai_stopping
        event_loop = asyncio.get_running_loop()
        if role == "gateway":
            user_path = data_dir / "user.json"
            first_command_start = not user_path.exists()
            user_profile = UserProfile(user_path)
            if first_command_start:
                user_profile.update("COMMAND", "beacon", "amber")
            service = start_gateway_stack(data_dir, TeamProfile(data_dir / "team.json"))
        else:
            team_membership = TeamMembership(data_dir / "team.json")
            user_profile = UserProfile(data_dir / "user.json")
            field_outbox = FieldOutbox(data_dir / "field-outbox.sqlite3")
            field_ptt_store = PTTStore(data_dir / "field-ptt")
            field_transcriber = LocalTranscriber(data_dir / "field-transcripts")
            hosted_dir = data_dir / "hosted"
            hosted_meta_path = hosted_dir / "gateway.json"
            if team_membership.joined and hosted_meta_path.exists():
                try:
                    hosted_destination = str(
                        json.loads(hosted_meta_path.read_text(encoding="utf-8")).get(
                            "destination", ""
                        )
                    )
                except (OSError, TypeError, json.JSONDecodeError):
                    hosted_destination = ""
                hosted_profile = TeamProfile(hosted_dir / "team.json")
                if (
                    hosted_profile.name is not None
                    and hosted_destination == team_membership.destination
                ):
                    hosted_service = start_gateway_stack(hosted_dir, hosted_profile)
            service = FieldSender(
                config_dir,
                data_dir,
                gateway_hash or team_membership.destination,
                live_callback=publish_live_from_reticulum,
            )
            continuity_task = asyncio.create_task(maintain_field_continuity())
        if speech_ai:
            speech_ai_task = asyncio.create_task(process_incoming_speech())
        yield
        speech_ai_stopping = True
        if speech_ai_task:
            speech_ai_task.cancel()
            try:
                await speech_ai_task
            except asyncio.CancelledError:
                pass
        await public_intel.traffic.close()
        await offline_routes.downloads.close()
        await asyncio.to_thread(offline_routes.store.close)
        continuity_stopping = True
        if continuity_task is not None:
            await continuity_task
        if field_flush_task is not None:
            field_flush_task.cancel()
            try:
                await field_flush_task
            except asyncio.CancelledError:
                pass
        if isinstance(service, (GatewayReceiver, FieldSender)):
            service.stop()
        if hosted_service is not None:
            hosted_service.stop()
        if field_transcriber is not None:
            field_transcriber.close()
        if transcriber is not None:
            transcriber.close()
        if task_store is not None:
            task_store.close()
        if private_store is not None:
            private_store.close()
        if store is not None:
            store.close()
        for cached_store in field_event_stores.values():
            cached_store.close()
        if field_outbox is not None:
            field_outbox.close()

    app = FastAPI(title="Reticom", version="0.1.0", lifespan=lifespan)
    public_intel = intel_router(data_dir)
    app.include_router(public_intel)
    app.include_router(landmark_router())
    offline_routes = offline_routing_router(data_dir)
    app.include_router(offline_routes)

    def membership_owner(request: Request):
        # These are privileged local-device controls, not remote team-admin RPCs.
        if not local_membership_request(request):
            raise HTTPException(403, "Manage membership on the team owner's device")
        gateway = active_gateway()
        if gateway is None or not gateway.membership.owner or not team_profile or not team_profile.name:
            raise HTTPException(403, "Only the original team owner can manage membership")
        return gateway

    def field_membership_admin(request: Request) -> FieldSender:
        """Authorize a local Field UI to delegate a roster decision to its host."""
        if not local_membership_request(request):
            raise HTTPException(403, "Manage membership from the local Reticom app")
        if (
            role != "field" or not isinstance(service, FieldSender)
            or team_membership is None or not team_membership.joined
            or not team_membership.everyone_admin
            or service.membership_status != "approved"
        ):
            raise HTTPException(403, "Team admin rights are required")
        return service

    def membership_listing(gateway: GatewayReceiver) -> dict[str, Any]:
        """Include known senders as reviewable requests without granting access."""
        candidates = [{"identity": item["sender_hash"], "callsign": item["callsign"]}
            for item in gateway.store.operators(since=gateway.team_created_at)
            if item["sender_hash"] != gateway.identity.hash.hex() and gateway.membership.status(item["sender_hash"]) == "pending"]
        state = gateway.membership.listing()
        known = {item["identity"] for item in state["requests"]}
        state["requests"].extend({**item, "previously_seen": True} for item in candidates if item["identity"] not in known)
        return state

    @app.get("/api/team/members")
    async def members(request: Request):
        try:
            gateway = membership_owner(request)
            return JSONResponse(membership_listing(gateway), headers={"Cache-Control": "no-store"})
        except HTTPException as owner_error:
            if owner_error.status_code != 403:
                raise
        sender = field_membership_admin(request)
        try:
            return JSONResponse(
                await asyncio.to_thread(sender.request_membership_admin),
                headers={"Cache-Control": "no-store"},
            )
        except (RuntimeError, TimeoutError, ValueError, PermissionError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/team/members")
    async def decide_member(request: Request):
        try:
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError("Use a JSON membership decision")
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 2048:
                    raise ValueError("Membership decision is too large")
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) - {"identity", "status", "callsign"}:
                raise ValueError("Invalid membership decision")
            try:
                gateway = membership_owner(request)
            except HTTPException as owner_error:
                if owner_error.status_code != 403:
                    raise
                sender = field_membership_admin(request)
                try:
                    state = await asyncio.to_thread(sender.request_membership_admin, body)
                except PermissionError as exc:
                    detail = str(exc)
                    if body.get("status") == "removed" and "Choose approve, reject or revoke" in detail:
                        raise HTTPException(409, "The team host is running an older version that cannot remove operators. Update Reticom on the team owner's device, then retry removal.") from exc
                    raise HTTPException(403, detail) from exc
                except (RuntimeError, TimeoutError) as exc:
                    raise HTTPException(503, str(exc)) from exc
                return JSONResponse(state, headers={"Cache-Control": "no-store"})
            if isinstance(service, FieldSender) and body.get("identity") == service.identity.hash.hex():
                raise ValueError("The hosting device cannot revoke itself")
            await asyncio.to_thread(gateway.membership.decide, body.get("identity"), body.get("status"), body.get("callsign"))
            gateway.enforce_membership()
            return JSONResponse(membership_listing(gateway), headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def rename_hosted_team(name: str) -> None:
        """Local Command workspace operation; retain the running host and identity."""
        gateway = active_gateway()
        if gateway is None or team_profile is None or team_profile.name is None:
            raise HTTPException(409, "Create a team before renaming it")
        team_profile.set_name(name)
        gateway.set_team(
            team_profile.name, team_profile.modules,
            team_profile.created_at, team_profile.everyone_admin,
        )

    app.state.rename_team = rename_hosted_team

    @app.get("/api/team/continuity")
    async def continuity_state():
        gateway = active_gateway()
        if gateway and gateway.continuity:
            return gateway.continuity.state()
        directory = service.host_directory if isinstance(service, FieldSender) else None
        choice = data_dir / "backup-choice.json"
        requested = json.loads(choice.read_text()) if choice.exists() else None
        current = team_membership.destination if team_membership else None
        offer = None
        if current and requested and requested.get("team") == current:
            identity = _load_or_create_identity(data_dir / "backups" / current / "gateway.identity")
            offer = {"public_key": identity.get_public_key().hex(), "destination": endpoint(identity)}
        return {"owner": False, "ready": False, "offer": offer,
            "policy": directory.envelope["policy"] if directory and directory.envelope else None}

    @app.post("/api/team/continuity/offer")
    async def offer_backup():
        if role != "field" or not team_membership or not team_membership.joined:
            raise HTTPException(409, "Join a team on Field before offering to host a backup")
        if field_hosts_team():
            raise HTTPException(409, "This device already hosts the team")
        identity = _load_or_create_identity(data_dir / "backups" / team_membership.destination / "gateway.identity")
        atomic_json(data_dir / "backup-choice.json", {"team": team_membership.destination})
        return {"public_key": identity.get_public_key().hex(), "destination": endpoint(identity)}

    @app.post("/api/team/continuity/approve")
    async def approve_backup(request: Request):
        gateway = active_gateway()
        if gateway is None or not gateway.continuity or not gateway.continuity.owner:
            raise HTTPException(403, "Only the original team owner can approve backup hosts")
        try:
            body = await request.json()
            if not isinstance(body, dict) or body.get("trust_host") is not True:
                raise ValueError("Confirm that this backup is trusted with team data and private mailboxes")
            gateway.continuity.approve(body.get("public_key"), body.get("label"))
            return gateway.continuity.state()
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/team/continuity/handover")
    async def handover(request: Request):
        gateway = active_gateway()
        if gateway is None or not gateway.continuity or not gateway.continuity.owner:
            raise HTTPException(403, "Only the original team owner can hand over preferred hosting")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Invalid handover request")
            gateway.continuity.prefer(body.get("destination"))
            return gateway.continuity.state()
        except (ValueError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/health")
    async def health():
        return {"ready": service is not None}

    @app.get("/api/state")
    async def state(limit: int = Query(default=200, ge=1, le=500)) -> dict[str, Any]:
        if service is None:
            raise HTTPException(status_code=503, detail="Reticulum is starting")
        if role == "gateway":
            assert (
                isinstance(service, GatewayReceiver)
                and store is not None
                and team_profile is not None
            )
            team_created = team_profile.name is not None
            return {
                "role": role,
                "network": service.state(),
                "event_count": (
                    store.count(since=team_profile.created_at) if team_created else 0
                ),
                "events": (
                    store.mission_events(since=team_profile.created_at, intel_limit=limit)
                    if team_created
                    else []
                ),
                "operators": (
                    store.operators(since=team_profile.created_at)
                    if team_created
                    else []
                ),
                "team": team_profile.state(service.destination.hash),
                "user": user_profile.state() if user_profile is not None else None,
            }
        assert isinstance(service, FieldSender) and team_membership is not None
        network_state = service.state()
        if field_hosts_team() and hosted_service is not None:
            network_state.update(
                {
                    "gateway": hosted_service.destination.hash.hex(),
                    "path_known": True,
                    "hops": 0,
                    "hosted": True,
                }
            )
        destination = team_membership.destination
        cache = field_event_store(destination)
        network_state["queued_events"] = (
            field_outbox.count(destination)
            if field_outbox is not None and destination is not None
            else 0
        )
        return {
            "role": role,
            "network": network_state,
            "events": cache.mission_events() if cache is not None else [],
            "team": field_team_state(),
            "user": user_profile.state() if user_profile is not None else None,
            "nearby_teams": service.nearby_teams(team_membership.destination),
        }

    @app.post("/api/user/settings")
    async def update_user_settings(request: Request) -> JSONResponse:
        if role != "field" or not isinstance(service, FieldSender) or user_profile is None:
            raise HTTPException(status_code=405, detail="User settings belong to Field")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ProtocolError("request body must be an object")
            user = user_profile.update(
                body.get("callsign"), body.get("icon"), body.get("color")
            )
            delivery = None
            warning = None
            if team_membership is not None and team_membership.joined:
                event = new_event("profile.updated", **user)
                try:
                    delivery = await asyncio.to_thread(publish_field_event_sync, event)
                except TimeoutError as exc:
                    warning = str(exc)
            return JSONResponse(
                {"user": user, "delivery": delivery, "warning": warning}
            )
        except (ProtocolError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/network/settings")
    async def get_network_settings() -> JSONResponse:
        if role != "field":
            raise HTTPException(status_code=405, detail="Network settings belong to Field")
        try:
            return JSONResponse(network_settings(config_dir / "config"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.put("/api/network/settings")
    async def update_network_settings(request: Request) -> JSONResponse:
        if role != "field":
            raise HTTPException(status_code=405, detail="Network settings belong to Field")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            custom_node = write_custom_node(
                config_dir / "config",
                body.get("host", ""),
                body.get("port", 4242),
            )
            return JSONResponse(
                {
                    **network_settings(config_dir / "config"),
                    "custom_node": custom_node,
                }
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/team/leave")
    async def leave_team() -> JSONResponse:
        if role != "field" or team_membership is None:
            raise HTTPException(status_code=405, detail="Only Field can leave a team")
        nonlocal hosted_service, transcriber
        was_hosting = field_hosts_team()
        team_membership.leave()
        if isinstance(service, FieldSender):
            await asyncio.to_thread(service.clear_gateway)
        if was_hosting and hosted_service is not None:
            hosted_service.stop()
            hosted_service = None
            if transcriber is not None:
                transcriber.close()
                transcriber = None
        return JSONResponse({"team": field_team_state()})

    @app.get("/api/feed")
    async def feed() -> JSONResponse:
        if role == "gateway":
            if store is None or team_profile is None or team_profile.name is None:
                raise HTTPException(status_code=409, detail="Create a team first")
            return JSONResponse(
                {"events": store.mission_events(since=team_profile.created_at)}
            )
        if (
            not isinstance(service, FieldSender)
            or team_membership is None
            or not team_membership.joined
        ):
            raise HTTPException(status_code=409, detail="Join a team first")
        try:
            if field_hosts_team():
                assert store is not None and team_profile is not None
                assert private_store is not None
                payload = {
                    "events": store.mission_events(since=team_profile.created_at),
                    "private_events": private_store.recent(
                        service.identity.hash.hex(), since=team_profile.created_at
                    ),
                    "team": {
                        "name": team_profile.name,
                        "modules": team_profile.modules,
                        "everyone_admin": team_profile.everyone_admin,
                    },
                    "network": {
                        "via": "local hosted Reticulum feed",
                        "response_ms": 0,
                        "gateway": hosted_service.destination.hash.hex()
                        if hosted_service is not None else None,
                    },
                }
            else:
                destination = team_membership.destination
                cache = field_event_store(destination)
                if not service.state().get("path_known"):
                    return JSONResponse(
                        {
                            "events": cache.mission_events() if cache is not None else [],
                            "mission_sync": mission_status(destination),
                            "private_events": [],
                            "team": field_team_state(),
                            "network": {
                                "online": False,
                                "via": "local field cache",
                                "queued": field_outbox.count(destination)
                                if field_outbox is not None and destination is not None
                                else 0,
                            },
                        }
                    )
                async with field_mission_lock:
                    inbox = mission_inbox(destination)
                    payload = await asyncio.to_thread(service.request_feed, 20.0, inbox.request())
                    if team_membership.destination != destination:
                        raise ValueError("Team changed during mission sync")
                    if "mission" in payload or payload.get("mission_restart"):
                        inbox.accept(payload)
                        saved = inbox.status()
                        payload["team"] = saved["team"]
                        payload["private_events"] = saved["private_events"]
                        payload["mission_sync"] = mission_status(destination)
                        payload.pop("records", None)
                        payload.pop("mission", None)
                    else:
                        cache_field_feed(payload.get("events"))
                        payload["mission_sync"] = {"state": "legacy", "reason": "Update the team host for full mission sync"}
                payload["events"] = cache.mission_events() if cache is not None else []
                network = payload.setdefault("network", {})
                network["online"] = True
                network["queued"] = (
                    field_outbox.count(destination)
                    if field_outbox is not None and destination is not None
                    else 0
                )
                schedule_field_outbox_flush()
            metadata = payload.get("team")
            if isinstance(metadata, dict) and metadata.get("name"):
                team_membership.update_metadata(
                    metadata.get("name"),
                    metadata.get("modules", []),
                    metadata.get("everyone_admin", False),
                )
                payload["team"] = field_team_state()
            else:
                # During the first partial snapshot there is no saved metadata
                # yet. Keep the joined state; do not send an empty team to UI.
                payload["team"] = field_team_state()
            return JSONResponse(payload)
        except (TimeoutError, PermissionError, ValueError) as exc:
            destination = team_membership.destination
            cache = field_event_store(destination)
            return JSONResponse(
                {
                    "events": cache.mission_events() if cache is not None else [],
                    "mission_sync": {**mission_status(destination), "state": "retrying", "reason": str(exc)},
                    "private_events": [],
                    "team": field_team_state(),
                    "network": {
                        "online": False,
                        "via": "local field cache",
                        "queued": field_outbox.count(destination)
                        if field_outbox is not None and destination is not None
                        else 0,
                        "reason": str(exc),
                    },
                }
            )

    @app.post("/api/mission/sync")
    async def resync_mission() -> JSONResponse:
        if role != "field" or not team_membership or not team_membership.joined:
            raise HTTPException(status_code=409, detail="Join a team first")
        async with field_mission_lock:
            mission_inbox().request(force=True)
        return await feed()

    @app.post("/api/ptt")
    async def send_ptt(
        request: Request,
        duration_ms: int = Query(ge=100, le=10_000),
    ) -> JSONResponse:
        command_ready = (
            role == "gateway"
            and isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
        )
        field_ready = (
            role == "field"
            and isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if (not command_ready and not field_ready) or user_profile is None:
            raise HTTPException(status_code=409, detail="Join a team before broadcasting")
        audio = await request.body()
        if not audio or len(audio) > PTT_MAX_BYTES:
            raise HTTPException(
                status_code=413, detail=f"Voice clip must be 1-{PTT_MAX_BYTES} bytes"
            )
        mime_type = request.headers.get("content-type", "").split(";", 1)[0]
        try:
            event = new_event(
                "ptt.broadcast",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                clip_id=str(uuid.uuid4()),
                duration_ms=duration_ms,
                mime_type=mime_type,
            )
            if isinstance(service, GatewayReceiver):
                delivery = await asyncio.to_thread(service.publish_ptt, event, audio)
            else:
                delivery = await asyncio.to_thread(publish_field_ptt_sync, event, audio)
            return JSONResponse({"event": event, "delivery": delivery})
        except (ProtocolError, PTTError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/messages/history")
    async def clear_message_history() -> JSONResponse:
        try:
            if role == "gateway" or field_hosts_team():
                return JSONResponse(
                    await asyncio.to_thread(
                        apply_admin_action, "communications.clear", {}
                    )
                )
            if (
                role != "field"
                or not isinstance(service, FieldSender)
                or team_membership is None
                or not team_membership.joined
                or not team_membership.everyone_admin
            ):
                raise PermissionError("team admin rights are required")
            result = await asyncio.to_thread(
                service.request_admin, "communications.clear", {}
            )
            return JSONResponse({"cleared": result.get("cleared", {})})
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/messages/{message_id}")
    async def delete_message(message_id: str) -> JSONResponse:
        command_ready = (
            role == "gateway"
            and isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
            and store is not None
        )
        field_ready = (
            role == "field"
            and isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if (not command_ready and not field_ready) or user_profile is None:
            raise HTTPException(status_code=409, detail="Join a team before removing messages")
        try:
            event = new_event(
                "message.deleted",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                message_id=message_id,
            )
            if isinstance(service, GatewayReceiver):
                assert store is not None
                if not store.event_exists(message_id, "chat.message"):
                    raise FileNotFoundError("Message not found")
                delivery = await asyncio.to_thread(service.publish_event, event)
            else:
                delivery = await asyncio.to_thread(
                    publish_field_event_sync, event, linked=True
                )
            return JSONResponse({"event": event, "delivery": delivery})
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ProtocolError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/audio/{clip_id}")
    async def read_audio(clip_id: str) -> Response:
        try:
            if role == "gateway":
                if ptt_store is None:
                    raise FileNotFoundError("voice storage unavailable")
                content, mime_type = ptt_store.read(clip_id)
            else:
                if (
                    not isinstance(service, FieldSender)
                    or team_membership is None
                    or not team_membership.joined
                ):
                    raise PermissionError("join a team first")
                if field_hosts_team():
                    if ptt_store is None:
                        raise FileNotFoundError("voice storage unavailable")
                    content, mime_type = await asyncio.to_thread(ptt_store.read, clip_id)
                else:
                    try:
                        if field_ptt_store is None:
                            raise FileNotFoundError
                        content, mime_type = await asyncio.to_thread(
                            field_ptt_store.read, clip_id
                        )
                    except (FileNotFoundError, PTTError):
                        content, mime_type = await asyncio.to_thread(
                            service.request_audio, clip_id
                        )
            return Response(
                content=content,
                media_type=mime_type,
                headers={"Cache-Control": "private, max-age=3600"},
            )
        except (FileNotFoundError, PTTError):
            raise HTTPException(status_code=404, detail="Voice clip not found")
        except (TimeoutError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/ai/markers")
    async def ai_marker_status():
        return {"enabled": speech_ai is not None, **(speech_ai.status if speech_ai else {"state": "disabled"}),
                "private_markers": private_ai_markers() if speech_ai else [],
                "reports": {source_id: speech_ai.read(source_id) for source_id in speech_ai_sources} if speech_ai else {}}

    @app.post("/api/command/position")
    async def set_command_position(request: Request):
        if not command_ai or not local_membership_request(request):
            raise HTTPException(403, "Command position must be set locally")
        if service is None or user_profile is None:
            raise HTTPException(503, "Command is not ready")
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"lat", "lon"}:
                raise ProtocolError("Provide latitude and longitude")
            marker = new_event("marker.created", user_profile.callsign, marker_type="command-post",
                               label="Command position", **body)
            previous = command_position()
            publish = service.publish_event if role == "gateway" else publish_field_event_sync
            await asyncio.to_thread(publish, marker)
            position = {"lat": marker["lat"], "lon": marker["lon"], "marker_id": marker["id"]}
            atomic_json(command_position_path, position)
            if previous and previous.get("marker_id"):
                await asyncio.to_thread(publish, new_event("marker.deleted", user_profile.callsign, marker_id=previous["marker_id"]))
            if speech_ai:
                for source_id in speech_ai_sources:
                    saved = speech_ai.read(source_id)
                    if saved and saved.get("state") == "needs_clarification" and saved.get("reason") in {"No recent shared position for the referenced operator", "Set Command position on the map first"}:
                        speech_ai.write({"id": source_id}, {**saved, "state": "pending"})
            return position
        except (ProtocolError, ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/transcriptions/{clip_id}")
    async def transcription(
        clip_id: str,
        start: bool = Query(default=False),
    ) -> JSONResponse:
        try:
            if role == "gateway":
                if ptt_store is None or transcriber is None:
                    raise FileNotFoundError("transcription unavailable")
                result = transcriber.status(clip_id)
                if result["status"] in {"unavailable", "error"} and start:
                    audio, mime_type = ptt_store.read(clip_id)
                    transcriber.schedule(clip_id, audio, mime_type)
                    result = {"status": "processing", "clip_id": clip_id}
            else:
                if (
                    not isinstance(service, FieldSender)
                    or team_membership is None
                    or not team_membership.joined
                ):
                    raise PermissionError("join a team first")
                if field_hosts_team():
                    if ptt_store is None or transcriber is None:
                        raise FileNotFoundError("transcription unavailable")
                    result = transcriber.status(clip_id)
                    if result["status"] in {"unavailable", "error"} and start:
                        audio, mime_type = ptt_store.read(clip_id)
                        transcriber.schedule(clip_id, audio, mime_type)
                        result = {"status": "processing", "clip_id": clip_id}
                else:
                    if field_transcriber is None:
                        raise FileNotFoundError("transcription unavailable")
                    result = field_transcriber.status(clip_id)
                    if result["status"] in {"unavailable", "error"} and start:
                        # Use this device's engine even when the team owner is a phone.
                        # The audio endpoint already implements local/outbound and
                        # authenticated team-host audio retrieval.
                        recording = await read_audio(clip_id)
                        field_transcriber.schedule(clip_id, recording.body, recording.media_type)
                        result = {"status": "processing", "clip_id": clip_id}
            if speech_ai and clip_id in speech_ai_clips:
                result = {**result, "interpretation": speech_ai.read(speech_ai_clips[clip_id])}
            return JSONResponse(result)
        except (FileNotFoundError, PTTError):
            raise HTTPException(status_code=404, detail="Voice clip not found")
        except TranscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (TimeoutError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/team/create")
    async def create_team(request: Request) -> JSONResponse:
        nonlocal hosted_service
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TeamCodeError("request body must be an object")
            if role == "gateway":
                if not isinstance(service, GatewayReceiver) or team_profile is None:
                    raise RuntimeError("Command is not ready")
                if team_profile.name is not None:
                    raise HTTPException(status_code=409, detail="Use Teams to create another team. The current team is unchanged.")
                name = team_profile.set_name(body.get("name"), body.get("modules", []))
                service.set_team(
                    name,
                    team_profile.modules,
                    team_profile.created_at,
                    team_profile.everyone_admin,
                )
                return JSONResponse(team_profile.state(service.destination.hash))
            if (
                not isinstance(service, FieldSender)
                or team_membership is None
                or user_profile is None
            ):
                raise RuntimeError("Field is not ready")
            if team_membership.joined:
                raise TeamCodeError("leave the current team before creating another")
            user_profile.update(
                body.get("callsign", user_profile.callsign),
                user_profile.icon,
                user_profile.color,
            )
            hosted_dir = data_dir / "hosted"
            hosted_profile = TeamProfile(hosted_dir / "team.json")
            name = hosted_profile.set_name(body.get("name"), body.get("modules", []))
            if hosted_service is not None:
                hosted_service.stop()
            hosted_service = start_gateway_stack(hosted_dir, hosted_profile)
            destination = hosted_service.destination.hash
            service.set_gateway_hash(destination.hex())
            team_membership.join(
                destination,
                name,
                hosted_profile.modules,
                hosted_profile.everyone_admin,
            )
            event = new_event(
                "team.joined",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
            )
            delivery = await asyncio.to_thread(publish_field_event_sync, event)
            state = field_team_state()
            return JSONResponse({**state, "event": event, "delivery": delivery})
        except (TeamCodeError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            if hosted_service is not None and not field_hosts_team():
                hosted_service.stop()
                hosted_service = None
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/team/modules")
    async def set_team_modules(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TeamCodeError("request body must be an object")
            payload = {"modules": body.get("modules")}
            if role == "gateway" or field_hosts_team():
                result = await asyncio.to_thread(
                    apply_admin_action, "modules.set", payload
                )
                team = result["team"]
                if role == "field" and team_membership is not None:
                    team_membership.update_metadata(
                        team.get("name"),
                        team.get("modules", []),
                        team.get("everyone_admin", False),
                    )
                    team = field_team_state()
            else:
                if (
                    not isinstance(service, FieldSender)
                    or team_membership is None
                    or not team_membership.joined
                    or not team_membership.everyone_admin
                ):
                    raise PermissionError("team admin rights are required")
                result = await asyncio.to_thread(
                    service.request_admin, "modules.set", payload
                )
                team = result["team"]
                team_membership.update_metadata(
                    team.get("name"),
                    team.get("modules", []),
                    team.get("everyone_admin", False),
                )
                team = field_team_state()
            return JSONResponse(team)
        except (TeamCodeError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/team/permissions")
    async def set_team_permissions(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TeamCodeError("request body must be an object")
            payload = {"everyone_admin": body.get("everyone_admin")}
            if role == "gateway" or field_hosts_team():
                result = await asyncio.to_thread(
                    apply_admin_action, "permissions.set", payload
                )
                team = result["team"]
                if role == "field" and team_membership is not None:
                    team_membership.update_metadata(
                        team.get("name"),
                        team.get("modules", []),
                        team.get("everyone_admin", False),
                    )
                    team = field_team_state()
            else:
                if (
                    not isinstance(service, FieldSender)
                    or team_membership is None
                    or not team_membership.joined
                    or not team_membership.everyone_admin
                ):
                    raise PermissionError("team admin rights are required")
                result = await asyncio.to_thread(
                    service.request_admin, "permissions.set", payload
                )
                remote_team = result["team"]
                team_membership.update_metadata(
                    remote_team.get("name"),
                    remote_team.get("modules", []),
                    remote_team.get("everyone_admin", False),
                )
                team = field_team_state()
            return JSONResponse(team)
        except (TeamCodeError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/team/qr")
    async def team_qr() -> Response:
        gateway = active_gateway()
        if gateway is None or team_profile is None or team_profile.name is None:
            raise HTTPException(status_code=404, detail="Create a team first")
        team = team_profile.state(gateway.destination.hash)
        return Response(
            content=join_code_svg(str(team["join_code"])),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/team/qr/decode")
    async def decode_team_qr(request: Request) -> JSONResponse:
        if role != "field" or not isinstance(service, FieldSender):
            raise HTTPException(status_code=405, detail="Only a Field node scans team QR codes")
        try:
            join_code = await asyncio.to_thread(
                decode_join_code_image, await request.body()
            )
            return JSONResponse({"join_code": join_code})
        except TeamCodeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def join_team_data(body) -> JSONResponse:
        if (
            role != "field"
            or not isinstance(service, FieldSender)
            or team_membership is None
        ):
            raise HTTPException(status_code=405, detail="Only a Field node can join")
        try:
            if not isinstance(body, dict):
                raise TeamCodeError("request body must be an object")
            if user_profile is None:
                raise RuntimeError("User profile is unavailable")
            destination = decode_join_code(str(body.get("join_code", "")))
            user_profile.update(
                str(body.get("callsign", user_profile.callsign)),
                user_profile.icon,
                user_profile.color,
            )
            service.set_gateway_hash(destination.hex())
            event = new_event(
                "team.joined",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
            )
            metadata = service.team_metadata(destination)
            name = str(metadata["name"]) if metadata else None
            modules = metadata.get("modules", []) if metadata else []
            everyone_admin = metadata.get("everyone_admin", False) if metadata else False
            team_membership.join(destination, name, modules, everyone_admin)
            delivery = await asyncio.to_thread(publish_field_event_sync, event)
            schedule_field_outbox_flush()
            return JSONResponse(
                {
                    "team": field_team_state(name, modules, everyone_admin),
                    "event": event,
                    "delivery": delivery,
                }
            )
        except (TeamCodeError, ProtocolError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    app.state.join_team = join_team_data
    app.state.nearby_teams = lambda: service.nearby_teams(None) if isinstance(service, FieldSender) else []

    app.state.sync_feed = feed

    @app.post("/api/team/join")
    async def join_team(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Enter a valid JSON object") from exc
        return await join_team_data(body)

    @app.get("/api/team/nearby")
    async def nearby_teams() -> JSONResponse:
        if (
            role != "field"
            or not isinstance(service, FieldSender)
            or team_membership is None
        ):
            raise HTTPException(status_code=405, detail="Only a Field node discovers teams")
        return JSONResponse(
            {"teams": service.nearby_teams(team_membership.destination)}
        )

    @app.get("/api/tasks")
    async def tasks() -> JSONResponse:
        if role == "gateway":
            if team_profile is None or task_store is None:
                raise HTTPException(status_code=503, detail="Tasks are starting")
            if "tasks" not in team_profile.modules:
                raise HTTPException(status_code=409, detail="Tasks module is not enabled")
            return JSONResponse(
                {"module": "tasks", "enabled": True, "tasks": task_store.list()}
            )
        if (
            not isinstance(service, FieldSender)
            or team_membership is None
            or not team_membership.joined
        ):
            raise HTTPException(status_code=409, detail="Join a team first")
        if "tasks" not in team_membership.modules:
            raise HTTPException(status_code=409, detail="Tasks module is not enabled")
        try:
            if field_hosts_team():
                assert task_store is not None
                return JSONResponse(
                    {"module": "tasks", "enabled": True, "tasks": task_store.list()}
                )
            return JSONResponse(await asyncio.to_thread(service.request_tasks))
        except (TimeoutError, PermissionError, ValueError) as exc:
            saved = mission_inbox().status()
            if saved.get("last_synced_at"):
                return JSONResponse({"module": "tasks", "enabled": True, "tasks": saved["tasks"], "cached": True})
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/private/messages")
    async def private_messages() -> JSONResponse:
        if isinstance(service, GatewayReceiver):
            if (
                team_profile is None
                or team_profile.name is None
                or private_store is None
            ):
                raise HTTPException(status_code=409, detail="Create a team first")
            return JSONResponse(
                {
                    "messages": private_store.recent(
                        service.identity.hash.hex(), since=team_profile.created_at
                    ),
                    "network": {"via": "local Command private mailbox", "response_ms": 0},
                }
            )
        if (
            not isinstance(service, FieldSender)
            or team_membership is None
            or not team_membership.joined
        ):
            raise HTTPException(status_code=409, detail="Join a team first")
        try:
            if field_hosts_team():
                assert private_store is not None and team_profile is not None
                return JSONResponse(
                    {
                        "messages": private_store.recent(
                            service.identity.hash.hex(), since=team_profile.created_at
                        ),
                        "network": {
                            "via": "local hosted private Reticulum mailbox",
                            "response_ms": 0,
                        },
                    }
                )
            return JSONResponse(await asyncio.to_thread(service.request_private_messages))
        except (TimeoutError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/private/messages")
    async def send_private_message(request: Request) -> JSONResponse:
        command_ready = (
            isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
        )
        field_ready = (
            isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if (not command_ready and not field_ready) or user_profile is None:
            raise HTTPException(status_code=409, detail="Create or join a team first")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ProtocolError("request body must be an object")
            event = new_event(
                "private.message",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                recipient_hash=body.get("recipient_hash"),
                message=body.get("message"),
            )
            if isinstance(service, GatewayReceiver):
                delivery = await asyncio.to_thread(service.publish_private, event)
            else:
                delivery = await asyncio.to_thread(publish_field_private_sync, event)
            return JSONResponse(
                {"event": event, "delivery": delivery}, status_code=201
            )
        except (ProtocolError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (TimeoutError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/private/ptt")
    async def send_private_ptt(
        request: Request,
        recipient_hash: str = Query(min_length=32, max_length=32),
        duration_ms: int = Query(ge=100, le=10_000),
    ) -> JSONResponse:
        command_ready = (
            isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
        )
        field_ready = (
            isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if (not command_ready and not field_ready) or user_profile is None:
            raise HTTPException(status_code=409, detail="Create or join a team first")
        audio = await request.body()
        if not audio or len(audio) > PTT_MAX_BYTES:
            raise HTTPException(
                status_code=413, detail=f"Voice clip must be 1-{PTT_MAX_BYTES} bytes"
            )
        mime_type = request.headers.get("content-type", "").split(";", 1)[0]
        try:
            event = new_event(
                "private.ptt",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                recipient_hash=recipient_hash,
                clip_id=str(uuid.uuid4()),
                duration_ms=duration_ms,
                mime_type=mime_type,
            )
            if isinstance(service, GatewayReceiver):
                delivery = await asyncio.to_thread(
                    service.publish_private_ptt, event, audio
                )
            else:
                delivery = await asyncio.to_thread(
                    publish_field_private_sync, event, audio
                )
            return JSONResponse(
                {"event": event, "delivery": delivery}, status_code=201
            )
        except (ProtocolError, PTTError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (TimeoutError, PermissionError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/tasks")
    async def create_task(request: Request) -> JSONResponse:
        if (
            (role != "gateway" and not field_hosts_team())
            or active_gateway() is None
            or team_profile is None
            or task_store is None
            or user_profile is None
        ):
            raise HTTPException(status_code=405, detail="Only Command can create tasks")
        if "tasks" not in team_profile.modules:
            raise HTTPException(status_code=409, detail="Tasks module is not enabled")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TaskError("request body must be an object")
            task = task_store.create(body.get("title"), body.get("assignee"))
            event = new_event(
                "task.created",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                task_id=task["id"],
                title=task["title"],
                assignee=task["assignee"],
            )
            delivery = await asyncio.to_thread(
                service.publish_event if isinstance(service, GatewayReceiver) else publish_field_event_sync, event
            )
            return JSONResponse(
                {"task": task, "event": event, "delivery": delivery},
                status_code=201,
            )
        except (ProtocolError, TaskError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str) -> JSONResponse:
        if (
            (role != "gateway" and not field_hosts_team())
            or active_gateway() is None
            or team_profile is None
            or task_store is None
        ):
            raise HTTPException(
                status_code=405, detail="Only Command can delete tasks"
            )
        if "tasks" not in team_profile.modules:
            raise HTTPException(status_code=409, detail="Tasks module is not enabled")
        try:
            task = task_store.delete(task_id)
        except TaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return JSONResponse({"deleted": task})

    @app.post("/api/tasks/{task_id}/complete")
    async def complete_task(task_id: str, request: Request) -> JSONResponse:
        if (
            role != "field"
            or not isinstance(service, FieldSender)
            or team_membership is None
            or not team_membership.joined
            or user_profile is None
        ):
            raise HTTPException(status_code=405, detail="Only a joined Field node completes tasks")
        if "tasks" not in team_membership.modules:
            raise HTTPException(status_code=409, detail="Tasks module is not enabled")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ProtocolError("request body must be an object")
            event = new_event(
                "task.completed",
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                task_id=task_id,
            )
            delivery = await asyncio.to_thread(
                publish_field_event_sync, event, linked=True
            )
            return JSONResponse({"event": event, "delivery": delivery})
        except (ProtocolError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/send")
    async def send(request: Request) -> JSONResponse:
        command_ready = (
            role == "gateway"
            and isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
        )
        field_ready = (
            role == "field"
            and isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if not command_ready and not field_ready:
            raise HTTPException(status_code=409, detail="Create or join a team before sending")
        if user_profile is None:
            raise HTTPException(status_code=503, detail="User profile is unavailable")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ProtocolError("request body must be an object")
            event_type = str(body.pop("type", ""))
            if event_type in {"navigation.updated", "navigation.stopped"}:
                raise ProtocolError("Use the navigation endpoint to share a route")
            body.pop("callsign", None)
            body.pop("icon", None)
            body.pop("color", None)
            if isinstance(service, GatewayReceiver) and event_type not in {
                "chat.message",
                "drawing.created",
                "marker.created",
                "marker.status",
            }:
                raise ProtocolError("Command can send messages and map updates")
            event = new_event(
                event_type,
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                **body,
            )
            if isinstance(service, GatewayReceiver):
                if event_type == "marker.status" and store.map_event(event["marker_id"], "marker.created") is None:
                    raise ProtocolError("marker not found")
                delivery = await asyncio.to_thread(service.publish_event, event)
            else:
                delivery = await asyncio.to_thread(
                    publish_field_event_sync, event, linked=True
                )
            return JSONResponse({"event": event, "delivery": delivery})
        except (ProtocolError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def navigation_context():
        command_ready = (isinstance(service, GatewayReceiver) and team_profile is not None
                         and team_profile.name is not None and store is not None)
        field_ready = (isinstance(service, FieldSender) and team_membership is not None
                       and team_membership.joined)
        if not command_ready and not field_ready:
            raise HTTPException(status_code=409, detail="Create or join a team before sharing navigation")
        selected = store if command_ready or field_hosts_team() else field_event_store()
        if selected is None or user_profile is None:
            raise HTTPException(status_code=503, detail="Navigation storage is unavailable")
        since = team_profile.created_at if (command_ready or field_hosts_team()) else None
        return selected, service.identity.hash.hex(), since

    @app.get("/api/navigation")
    async def navigation_plans() -> dict[str, Any]:
        from .navigation import as_plan
        selected, sender, since = navigation_context()
        return {"plans": [as_plan(event) for event in selected.navigation_events(since)], "sender_hash": sender}

    @app.put("/api/navigation")
    async def share_navigation(request: Request) -> JSONResponse:
        from .navigation import as_plan, fields_from_request
        selected, sender, since = navigation_context()
        raw = await request.body()
        if len(raw) > 250_000:
            raise HTTPException(status_code=413, detail="Route request is too large; route was not shared")
        try:
            body = json.loads(raw)
            async with navigation_lock:
                current = next((event for event in selected.navigation_events(since)
                                if event["network"]["sender_hash"] == sender), None)
                route_id = current["route_id"] if current and current["type"] == "navigation.updated" else str(uuid.uuid4())
                fields = fields_from_request(body, route_id, selected.next_navigation_revision(sender))
                event = new_event("navigation.updated", user_profile.callsign,
                                  icon=user_profile.icon, color=user_profile.color, **fields)
                if isinstance(service, GatewayReceiver):
                    delivery = await asyncio.to_thread(service.publish_event, event)
                else:
                    delivery = await asyncio.to_thread(publish_field_event_sync, event, linked=True)
                projected = next(item for item in selected.navigation_events(since) if item["id"] == event["id"])
            schedule_field_outbox_flush()
            return JSONResponse({"event": event, "delivery": delivery, "plan": as_plan(projected)}, status_code=201)
        except (ProtocolError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/navigation")
    async def stop_shared_navigation() -> JSONResponse:
        from .navigation import as_plan
        selected, sender, since = navigation_context()
        try:
            async with navigation_lock:
                current = next((event for event in selected.navigation_events(since)
                                if event["network"]["sender_hash"] == sender), None)
                if current is None or current["type"] == "navigation.stopped":
                    return JSONResponse({"stopped": False})
                event = new_event("navigation.stopped", user_profile.callsign,
                                  icon=user_profile.icon, color=user_profile.color,
                                  route_id=current["route_id"], revision=selected.next_navigation_revision(sender))
                if isinstance(service, GatewayReceiver):
                    delivery = await asyncio.to_thread(service.publish_event, event)
                else:
                    delivery = await asyncio.to_thread(publish_field_event_sync, event, linked=True)
                projected = next(item for item in selected.navigation_events(since) if item["id"] == event["id"])
            schedule_field_outbox_flush()
            return JSONResponse({"stopped": True, "event": event, "delivery": delivery, "plan": as_plan(projected)})
        except (ProtocolError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/map/{kind}/{map_event_id}")
    async def delete_map_event(kind: str, map_event_id: str) -> JSONResponse:
        command_ready = (
            role == "gateway"
            and isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
            and store is not None
        )
        field_ready = (
            role == "field"
            and isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if (not command_ready and not field_ready) or user_profile is None:
            raise HTTPException(status_code=409, detail="Join a team before editing the map")
        if kind not in {"marker", "drawing", "automatic-report"}:
            raise HTTPException(status_code=404, detail="Unknown map item")
        created_type = f"{kind}.created"
        deleted_type = "report.dismissed" if kind == "automatic-report" else f"{kind}.deleted"
        reference_key = "message_id" if kind == "automatic-report" else f"{kind}_id"
        try:
            if isinstance(service, GatewayReceiver):
                assert store is not None
                target = store.automatic_report_source(map_event_id) if kind == "automatic-report" else store.map_event(map_event_id, created_type)
                if target is None:
                    raise FileNotFoundError("Map item not found")
            event = new_event(
                deleted_type,
                user_profile.callsign,
                icon=user_profile.icon,
                color=user_profile.color,
                **{reference_key: map_event_id},
            )
            if isinstance(service, GatewayReceiver):
                delivery = await asyncio.to_thread(service.publish_event, event)
            else:
                delivery = await asyncio.to_thread(
                    publish_field_event_sync, event, linked=True
                )
            fixed = command_position() if command_ai else None
            if kind == "marker" and fixed and fixed.get("marker_id") == map_event_id:
                command_position_path.unlink(missing_ok=True)
            return JSONResponse({"event": event, "delivery": delivery})
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ProtocolError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/offline-maps")
    async def list_offline_maps() -> dict[str, Any]:
        packs = offline_maps.list()
        return {
            "packs": packs,
            "bytes": sum(int(pack.get("bytes", 0)) for pack in packs),
            "max_tiles": 2500,
            "max_zoom": 14,
        }

    @app.post("/api/offline-maps")
    async def create_offline_map(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("bounds"), dict):
                raise OfflineMapError("Choose a visible map area before downloading")
            pack = offline_maps.create(
                body["bounds"],
                int(body.get("max_zoom", 14)),
                body.get("name"),
            )

            async def download() -> None:
                try:
                    await asyncio.to_thread(offline_maps.download, pack["id"])
                except Exception:
                    # The store records a concise, user-facing error in the manifest.
                    pass
                finally:
                    offline_download_tasks.pop(pack["id"], None)

            offline_download_tasks[pack["id"]] = asyncio.create_task(download())
            return JSONResponse({"pack": pack}, status_code=202)
        except (OfflineMapError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/offline-maps/{pack_id}")
    async def delete_offline_map(pack_id: str) -> dict[str, Any]:
        try:
            deleted = await asyncio.to_thread(offline_maps.delete, pack_id)
            return {"deleted": deleted}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OfflineMapError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/map/tiles/{zoom}/{x}/{y}.pbf")
    async def map_tile(zoom: int, x: int, y: int) -> Response:
        try:
            payload, cached = await asyncio.to_thread(offline_maps.tile, zoom, x, y)
            return Response(
                payload,
                media_type="application/x-protobuf",
                headers={
                    "Cache-Control": "public, max-age=604800",
                    "X-Reticom-Offline": "hit" if cached else "network",
                },
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Map tile is not saved and the online map provider could not be reached",
            ) from exc

    @app.get("/api/map/fonts/{fontstack}/{range_name}.pbf")
    async def map_font(fontstack: str, range_name: str) -> Response:
        try:
            payload, cached = await asyncio.to_thread(offline_maps.font, fontstack, range_name)
            return Response(
                payload,
                media_type="application/x-protobuf",
                headers={
                    "Cache-Control": "public, max-age=2592000",
                    "X-Reticom-Offline": "hit" if cached else "network",
                },
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Map labels are not saved and the online map provider could not be reached",
            ) from exc

    @app.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        if role != "gateway":
            await websocket.close(code=1008, reason="Command stream only")
            return
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        subscribers.add(queue)
        try:
            await websocket.send_json({"type": "stream.ready"})
            while True:
                event = await queue.get()
                await websocket.send_json({"type": "event.received", "event": event})
        except WebSocketDisconnect:
            pass
        finally:
            subscribers.discard(queue)

    @app.websocket("/api/voice/live")
    async def live_voice(websocket: WebSocket) -> None:
        if service is None or user_profile is None:
            await websocket.close(code=1013, reason="Reticulum is starting")
            return
        command_ready = (
            role == "gateway"
            and isinstance(service, GatewayReceiver)
            and team_profile is not None
            and team_profile.name is not None
        )
        field_ready = (
            role == "field"
            and isinstance(service, FieldSender)
            and team_membership is not None
            and team_membership.joined
        )
        if not command_ready and not field_ready:
            await websocket.close(code=1008, reason="Join a team before live voice")
            return
        voice_transport: GatewayReceiver | FieldSender = (
            hosted_service if field_hosts_team() and hosted_service is not None else service
        )

        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=96)
        voice_subscribers.add(queue)
        active_stream: str | None = None
        chunk_sequence = 0

        async def send_frames() -> None:
            while True:
                frame = await queue.get()
                if frame.get("type") == "live.chunk":
                    stream_id = uuid.UUID(str(frame["stream_id"])).bytes
                    sequence = int(frame.get("sequence", 0)).to_bytes(4, "big")
                    audio = frame.get("audio")
                    if isinstance(audio, bytes):
                        await websocket.send_bytes(stream_id + sequence + audio)
                else:
                    await websocket.send_json(frame)

        sender = asyncio.create_task(send_frames())
        try:
            if isinstance(voice_transport, FieldSender):
                try:
                    await asyncio.to_thread(voice_transport.ensure_live_link)
                except (TimeoutError, OSError, RuntimeError, LiveVoiceError) as exc:
                    await websocket.send_json(
                        {"type": "live.transport", "ready": False, "error": str(exc)}
                    )
                else:
                    await websocket.send_json(
                        {"type": "live.transport", "ready": True, "peers": 1}
                    )
            else:
                await websocket.send_json(
                    {
                        "type": "live.transport",
                        "ready": voice_transport.live_ready(),
                        "peers": 1 if voice_transport.live_ready() else 0,
                    }
                )

            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                try:
                    if message.get("text") is not None:
                        body = json.loads(str(message["text"]))
                        if not isinstance(body, dict):
                            raise LiveVoiceError("invalid live voice command")
                        action = str(body.get("action", ""))
                        if action == "start":
                            if active_stream is not None:
                                raise LiveVoiceError("a live transmission is already active")
                            stream_id = str(uuid.UUID(str(body.get("stream_id", ""))))
                            metadata = {
                                "mime_type": body.get("mime_type"),
                                "callsign": user_profile.callsign,
                                "icon": user_profile.icon,
                                "color": user_profile.color,
                            }
                            delivery = await asyncio.to_thread(
                                voice_transport.send_live_start, stream_id, metadata
                            )
                            active_stream = stream_id
                            chunk_sequence = 0
                            await websocket.send_json(
                                {
                                    "type": "live.started",
                                    "stream_id": stream_id,
                                    **delivery,
                                }
                            )
                        elif action in {"end", "cancel"}:
                            if active_stream is None:
                                continue
                            stream_id = active_stream
                            await asyncio.to_thread(
                                voice_transport.send_live_end,
                                stream_id,
                                action == "cancel",
                            )
                            active_stream = None
                            await websocket.send_json(
                                {
                                    "type": "live.ended",
                                    "stream_id": stream_id,
                                    "canceled": action == "cancel",
                                }
                            )
                        else:
                            raise LiveVoiceError("unknown live voice command")
                    elif message.get("bytes") is not None:
                        audio = message["bytes"]
                        if active_stream is None:
                            raise LiveVoiceError("start live voice before sending audio")
                        if not isinstance(audio, bytes) or not audio or len(audio) > LIVE_VOICE_MAX_CHUNK:
                            raise LiveVoiceError("invalid live voice audio chunk")
                        await asyncio.to_thread(
                            voice_transport.send_live_chunk,
                            active_stream,
                            chunk_sequence,
                            audio,
                        )
                        chunk_sequence += 1
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    LiveVoiceError,
                    TimeoutError,
                    OSError,
                    RuntimeError,
                ) as exc:
                    await websocket.send_json(
                        {
                            "type": "live.error",
                            "stream_id": active_stream,
                            "error": str(exc),
                        }
                    )
        except WebSocketDisconnect:
            pass
        finally:
            if active_stream is not None:
                try:
                    await asyncio.to_thread(
                        voice_transport.send_live_end, active_stream, True
                    )
                except (LiveVoiceError, TimeoutError, OSError, RuntimeError):
                    pass
            sender.cancel()
            voice_subscribers.discard(queue)
            if isinstance(voice_transport, FieldSender) and not voice_subscribers:
                await asyncio.to_thread(voice_transport.close_live_link)

    @app.websocket("/api/heading/live")
    async def live_heading(websocket: WebSocket) -> None:
        if service is None or user_profile is None:
            await websocket.close(code=1013)
            return
        if not ((role == "gateway" and team_profile and team_profile.name) or
                (role == "field" and team_membership and team_membership.joined)):
            await websocket.close(code=1008)
            return
        transport = active_gateway() or service
        headings = transport.headings
        destination = team_membership.destination if team_membership else None
        await websocket.accept()
        signal = asyncio.Event()
        pending: dict[str, Any] = {}
        heading_subscribers[signal] = pending
        sent_heading = False

        async def stream() -> None:
            while True:
                await signal.wait()
                signal.clear()
                frames = list(pending.values())
                pending.clear()
                for frame in frames:
                    if frame.get("at") and clock_ms() - frame["at"] > TTL_MS:
                        continue
                    await websocket.send_json({**frame, "server_at": clock_ms()} if frame.get("type") == "heading.clock" else frame)

        async def maintain() -> None:
            while True:
                if (active_gateway() or service) is not transport or (team_membership and team_membership.destination != destination):
                    await websocket.close(code=1000, reason="Team changed")
                    return
                try:
                    ready = await asyncio.to_thread(headings.ensure) if isinstance(headings, HeadingClient) else headings.ready()
                    period = heading_interval(headings.link) if isinstance(headings, HeadingClient) and headings.ready() else .1
                except (TimeoutError, OSError, RuntimeError, ValueError):
                    ready, period = False, 1
                pending["state"] = {"type": "heading.ready", "ready": ready, "interval_ms": round(period * 1000)}
                signal.set()
                await asyncio.sleep(2)

        sender, keeper = asyncio.create_task(stream()), asyncio.create_task(maintain())
        try:
            while True:
                body = await websocket.receive_json()
                if isinstance(body, dict) and body.get("type") == "heading.clock" and type(body.get("request_id")) is int:
                    pending["clock"] = {"type": "heading.clock", "request_id": body["request_id"]}
                    signal.set()
                    continue
                if not isinstance(body, dict) or body.get("type") != "heading.sample":
                    continue
                # Never turn delayed browser frames into fresh network samples.
                if type(body.get("at")) not in (int, float) or not -1000 <= clock_ms() - body["at"] <= 750:
                    continue
                value = body.get("heading")
                if value is not None and (type(value) not in (int, float) or not 0 <= value < 360):
                    continue
                sent_heading = value is not None
                if isinstance(headings, HeadingClient):
                    await asyncio.to_thread(headings.send, value)
                else:
                    await asyncio.to_thread(headings.publish, value, service.identity.hash)
        except (WebSocketDisconnect, RuntimeError, ValueError, OSError):
            pass
        finally:
            heading_subscribers.pop(signal, None)
            sender.cancel()
            keeper.cancel()
            await asyncio.gather(sender, keeper, return_exceptions=True)
            if sent_heading:
                if isinstance(headings, HeadingClient):
                    await asyncio.to_thread(headings.send, None)
                else:
                    await asyncio.to_thread(headings.publish, None, service.identity.hash)
            if isinstance(headings, HeadingClient) and not heading_subscribers:
                headings.close()

    static_dir = Path(__file__).with_name("static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def _gateway_from_file(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data["destination"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Reticom node")
    parser.add_argument("--role", choices=("gateway", "field"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--gateway")
    parser.add_argument("--gateway-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    gateway_hash = args.gateway
    if args.role == "field" and gateway_hash is None and args.gateway_file:
        gateway_hash = _gateway_from_file(args.gateway_file)

    app = create_app(args.role, args.config.resolve(), args.data.resolve(), gateway_hash)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
