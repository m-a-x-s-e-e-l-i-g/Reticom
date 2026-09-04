from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .bootstrap import network_settings, write_custom_node
from .live_voice import LIVE_VOICE_MAX_CHUNK, LiveVoiceError
from .offline_maps import OfflineMapError, OfflineMapStore
from .protocol import ProtocolError, new_event
from .ptt import PTT_MAX_BYTES, PTTError, PTTStore
from .qr import decode_join_code_image, join_code_svg
from .store import EventStore, PrivateMessageStore
from .team import TeamCodeError, TeamMembership, TeamProfile, decode_join_code
from .tasks import TaskError, TaskStore
from .transport import FieldSender, GatewayReceiver
from .transcription import LocalTranscriber, TranscriptionError
from .user import UserProfile
from .waypoints import WaypointArrivalDetector


def create_app(
    role: str,
    config_dir: Path,
    data_dir: Path,
    gateway_hash: str | None = None,
) -> FastAPI:
    service: GatewayReceiver | FieldSender | None = None
    hosted_service: GatewayReceiver | None = None
    store: EventStore | None = None
    private_store: PrivateMessageStore | None = None
    team_profile: TeamProfile | None = None
    team_membership: TeamMembership | None = None
    task_store: TaskStore | None = None
    user_profile: UserProfile | None = None
    ptt_store: PTTStore | None = None
    transcriber: LocalTranscriber | None = None
    subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
    voice_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
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
            and team_membership.destination == hosted_service.destination.hash.hex()
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
        state["admin"] = field_hosts_team() or state["everyone_admin"]
        return state

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
        stack_data_dir: Path, profile: TeamProfile
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

        def feed_response() -> bytes:
            assert store is not None and team_profile is not None
            events = store.recent(60, since=team_profile.created_at)
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
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        def save_and_transcribe(clip_id: str, audio: bytes, mime_type: str) -> None:
            assert ptt_store is not None and transcriber is not None
            ptt_store.save(clip_id, audio, mime_type)
            transcriber.schedule(clip_id, audio, mime_type)

        def transcription_response(clip_id: str, start: bool) -> dict[str, Any]:
            assert ptt_store is not None and transcriber is not None
            result = transcriber.status(clip_id)
            if result["status"] == "unavailable" and start:
                audio, mime_type = ptt_store.read(clip_id)
                transcriber.schedule(clip_id, audio, mime_type)
                return {"status": "processing", "clip_id": clip_id}
            return result

        return GatewayReceiver(
            config_dir,
            stack_data_dir,
            store,
            team_name=team_profile.name,
            team_modules=team_profile.modules,
            task_response=task_response,
            feed_response=feed_response,
            save_ptt=save_and_transcribe,
            audio_response=ptt_store.read,
            transcription_response=transcription_response,
            admin_response=apply_admin_action,
            event_callback=publish_from_reticulum,
            live_callback=publish_live_from_reticulum,
            private_store=private_store,
            team_created_at=team_profile.created_at,
            everyone_admin=team_profile.everyone_admin,
        )

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
        return service.send_link_event(event) if linked else service.send_event(event)

    def publish_field_ptt_sync(
        event: dict[str, Any], audio: bytes
    ) -> dict[str, Any]:
        if not isinstance(service, FieldSender):
            raise RuntimeError("Field transport is unavailable")
        if field_hosts_team():
            assert hosted_service is not None
            return hosted_service.publish_local_field_ptt(event, audio, service.identity)
        return service.send_ptt(event, audio)

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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal service, hosted_service, team_membership, user_profile, event_loop
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
        yield
        if isinstance(service, (GatewayReceiver, FieldSender)):
            service.stop()
        if hosted_service is not None:
            hosted_service.stop()
        if transcriber is not None:
            transcriber.close()
        if task_store is not None:
            task_store.close()
        if private_store is not None:
            private_store.close()
        if store is not None:
            store.close()

    app = FastAPI(title="Reticom", version="0.1.0", lifespan=lifespan)

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
                    store.recent(limit, since=team_profile.created_at)
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
        return {
            "role": role,
            "network": network_state,
            "events": [],
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
                {"events": store.recent(60, since=team_profile.created_at)}
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
                    "events": store.recent(60, since=team_profile.created_at),
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
                payload = await asyncio.to_thread(service.request_feed)
            metadata = payload.get("team")
            if isinstance(metadata, dict):
                team_membership.update_metadata(
                    metadata.get("name"),
                    metadata.get("modules", []),
                    metadata.get("everyone_admin", False),
                )
                payload["team"] = field_team_state()
            return JSONResponse(payload)
        except (TimeoutError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

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
    async def audio(clip_id: str) -> Response:
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
                if result["status"] == "unavailable" and start:
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
                    if result["status"] == "unavailable" and start:
                        audio, mime_type = ptt_store.read(clip_id)
                        transcriber.schedule(clip_id, audio, mime_type)
                        result = {"status": "processing", "clip_id": clip_id}
                else:
                    result = await asyncio.to_thread(
                        service.request_transcription, clip_id, start
                    )
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

    @app.post("/api/team/join")
    async def join_team(request: Request) -> JSONResponse:
        if (
            role != "field"
            or not isinstance(service, FieldSender)
            or team_membership is None
        ):
            raise HTTPException(status_code=405, detail="Only a Field node can join")
        try:
            body = await request.json()
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
            delivery = await asyncio.to_thread(service.send_event, event)
            metadata = service.team_metadata(destination)
            name = str(metadata["name"]) if metadata else None
            modules = metadata.get("modules", []) if metadata else []
            everyone_admin = metadata.get("everyone_admin", False) if metadata else False
            team_membership.join(destination, name, modules, everyone_admin)
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
            role != "gateway"
            or not isinstance(service, GatewayReceiver)
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
            delivery = await asyncio.to_thread(service.publish_event, event)
            return JSONResponse(
                {"task": task, "event": event, "delivery": delivery},
                status_code=201,
            )
        except (ProtocolError, TaskError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str) -> JSONResponse:
        if (
            role != "gateway"
            or not isinstance(service, GatewayReceiver)
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
            body.pop("callsign", None)
            body.pop("icon", None)
            body.pop("color", None)
            if isinstance(service, GatewayReceiver) and event_type not in {
                "chat.message",
                "drawing.created",
                "marker.created",
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
        if kind not in {"marker", "drawing"}:
            raise HTTPException(status_code=404, detail="Unknown map item")
        created_type = f"{kind}.created"
        deleted_type = f"{kind}.deleted"
        reference_key = f"{kind}_id"
        try:
            if isinstance(service, GatewayReceiver):
                assert store is not None
                if store.map_event(map_event_id, created_type) is None:
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
