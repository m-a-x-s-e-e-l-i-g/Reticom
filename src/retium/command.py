"""Independent team hosts behind one Command UI and one Reticulum instance.

The original data directory stays in place as `default`; new teams get isolated
identities, stores, callbacks and subscriber queues. Selection is request-local,
never a global mutable 'current team', so separate browser tabs cannot cross-post.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .team import TeamCodeError, TeamProfile, TeamMembership, decode_join_code
from .user import UserProfile
from .command_snapshot import read_team_snapshot
from .offline_maps import OfflineMapStore
from .offline_routing import offline_routing_router
from .intel_packs import intel_router


class CommandTeams:
    def __init__(self, config_dir: Path, data_dir: Path, factory: Callable):
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.factory = factory
        self.path = data_dir / "command-teams.json"
        self.entries = {"default": {"hosting": True}}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))
        # Validate before resolving any stored directory.
        for team_id, entry in self.entries.items():
            if team_id != "default" and (len(team_id) != 32 or uuid.UUID(hex=team_id).hex != team_id):
                raise ValueError("Invalid saved Command team identifier")
            if not isinstance(entry, dict) or not isinstance(entry.get("hosting"), bool):
                raise ValueError("Invalid saved Command team status")
            if entry.get("mode", "host") not in {"host", "member"}:
                raise ValueError("Invalid saved Command team mode")
        self.apps: dict[str, FastAPI] = {}
        self.contexts = {}
        self.sync_workers = {}
        self.sync_stops = {}
        self.requests: dict[str, set] = {}
        self.sockets: dict[str, dict] = {}
        self.stopping: set[str] = set()
        self.lock = asyncio.Lock()
        self.static = StaticFiles(directory=Path(__file__).with_name("static"), html=True)

    def directory(self, team_id: str) -> Path:
        if team_id not in self.entries:
            raise HTTPException(404, "Unknown team")
        return self.data_dir if team_id == "default" else self.data_dir / "teams" / team_id

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    async def start(self, team_id: str):
        if self.entries[team_id].get("removed_at"):
            raise HTTPException(409, "Restore this team before opening it")
        if team_id in self.apps:
            return
        role = "field" if self.entries[team_id].get("mode") == "member" else "gateway"
        app = self.factory(role, self.config_dir, self.directory(team_id))
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        self.apps[team_id] = app
        self.contexts[team_id] = context
        self.requests[team_id] = set()
        self.sockets[team_id] = {}
        if role == "field":
            stop = asyncio.Event()
            self.sync_stops[team_id] = stop
            async def synchronize():
                while not stop.is_set():
                    try:
                        await app.state.sync_feed()
                    except (HTTPException, OSError, ValueError):
                        pass  # Retain cached mission data while the remote host is offline.
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        pass
            self.sync_workers[team_id] = asyncio.create_task(synchronize())

    async def stop(self, team_id: str):
        if team_id not in self.apps:
            return
        self.stopping.add(team_id)
        try:
            if team_id in self.sync_workers:
                self.sync_stops[team_id].set()
                _, remaining = await asyncio.wait({self.sync_workers[team_id]}, timeout=30)
                if remaining:
                    raise HTTPException(409, "Team sync is finishing. Try again shortly.")
                self.sync_workers.pop(team_id)
                self.sync_stops.pop(team_id)
            # Drain HTTP work before closing its SQLite stores; never cancel a
            # to_thread write underneath it. New requests are rejected by dispatch.
            pending = set(self.requests[team_id])
            if pending:
                _, remaining = await asyncio.wait(pending, timeout=30)
                if remaining:
                    raise HTTPException(409, "Team is busy. Finish sending and try again.")
            for task, send in list(self.sockets[team_id].items()):
                try:
                    await send({"type": "websocket.close", "code": 1001})
                except (RuntimeError, OSError):
                    pass
                task.cancel()
            if self.sockets[team_id]:
                await asyncio.gather(*list(self.sockets[team_id]), return_exceptions=True)
            await self.contexts.pop(team_id).__aexit__(None, None, None)
            self.apps.pop(team_id)
        finally:
            self.stopping.discard(team_id)

    def listing(self, removed=False):
        teams = []
        for team_id in self.entries:
            entry = self.entries[team_id]
            if bool(entry.get("removed_at")) != removed:
                continue
            directory = self.directory(team_id)
            member = entry.get("mode") == "member"
            profile = (TeamMembership if member else TeamProfile)(directory / "team.json")
            if not member and profile.name is None:
                continue
            metadata_path = directory / "gateway.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            destination = profile.destination if member else metadata.get("destination")
            teams.append({
                "id": team_id, "name": profile.name or entry.get("label") or "Joined team",
                "hosting": team_id in self.apps and team_id not in self.stopping,
                "destination": destination, "modules": profile.modules,
                "mode": "member" if member else "host", "removed_at": entry.get("removed_at"),
            })
        return {"teams": teams}

    def overview_snapshot(self, listed):
        snapshots = []
        for team in listed:
            directory = self.directory(team["id"])
            try:
                if team.get("mode") == "member":
                    profile = TeamMembership(directory / "team.json")
                    directory = directory / "field-cache" / profile.destination
                    snapshot = read_team_snapshot(directory, None, profile.modules)
                else:
                    profile = TeamProfile(directory / "team.json")
                    snapshot = read_team_snapshot(directory, profile.created_at, profile.modules)
                snapshots.append({**team, **snapshot, "snapshot_available": True})
            except (OSError, ValueError, sqlite3.Error):
                snapshots.append({**team, "snapshot_available": False, "events": [], "open_tasks": None, "last_event_at": None})
        return {"teams": snapshots, "snapshot_at": int(time.time())}

    async def __call__(self, scope, receive, send):
        if not scope["path"].startswith("/api/"):
            await self.static(scope, receive, send)
            return
        values = parse_qs(scope.get("query_string", b"").decode("ascii", errors="replace"))
        team_id = values.get("team", ["default"])[0]
        app = self.apps.get(team_id)
        if app is None or team_id in self.stopping:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Team is not hosted. Open Teams to start it.", "team_unavailable": True}, status_code=409)(scope, receive, send)
            return
        websocket = scope["type"] == "websocket"
        # Own the websocket handler task, not the ASGI server's enclosing task
        # (which can stay alive after the handler returns).
        task = asyncio.create_task(app(scope, receive, send)) if websocket else asyncio.current_task()
        if websocket:
            self.sockets[team_id][task] = send
        else:
            self.requests[team_id].add(task)
        try:
            if websocket:
                await task
            else:
                await app(scope, receive, send)
        except asyncio.CancelledError:
            if not websocket or team_id not in self.stopping:
                raise
        finally:
            if websocket:
                self.sockets[team_id].pop(task, None)
            else:
                self.requests[team_id].discard(task)


def create_command_app(config_dir: Path, data_dir: Path, factory: Callable) -> FastAPI:
    teams = CommandTeams(config_dir, data_dir, factory)
    overview_maps = OfflineMapStore(data_dir / "offline-maps")

    @asynccontextmanager
    async def lifespan(app):
        discovery = teams.factory("field", config_dir, data_dir / "discovery")
        discovery_context = discovery.router.lifespan_context(discovery)
        await discovery_context.__aenter__()
        app.state.discovery = discovery
        try:
            for team_id, entry in teams.entries.items():
                if entry["hosting"] and not entry.get("removed_at"):
                    await teams.start(team_id)
            yield
        finally:
            await public_intel.traffic.close()
            await offline_routes.downloads.close()
            await asyncio.to_thread(offline_routes.store.close)
            for team_id in list(teams.apps):
                await teams.stop(team_id)
            await discovery_context.__aexit__(None, None, None)

    app = FastAPI(title="Reticom Command", lifespan=lifespan)
    # Public layers belong to this device, not to a selected team's signed feed.
    public_intel = intel_router(data_dir)
    app.include_router(public_intel)
    from .landmarks import landmark_router
    app.include_router(landmark_router())
    offline_routes = offline_routing_router(data_dir)
    app.include_router(offline_routes)
    app.state.command_teams = teams

    @app.get("/api/health")
    async def health():
        return {"ready": True}

    async def body_object(request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Enter a valid JSON object") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "Enter a valid JSON object")
        return body

    @app.get("/api/command/teams")
    async def list_teams():
        return {**teams.listing(), "removed": teams.listing(removed=True)["teams"]}

    @app.get("/api/command/nearby")
    async def nearby():
        # Discovery knows what is nearby; the Command workspace knows every
        # locally saved team. Merge both so a repeated announce cannot offer a
        # second join for a team that is already present (or merely paused).
        saved = {team["destination"]: team for team in teams.listing()["teams"] if team.get("destination")}
        announced = []
        for team in app.state.discovery.state.nearby_teams():
            local = saved.get(team.get("destination"))
            announced.append({**team,
                "in_workspace": bool(local),
                "workspace_team_id": local.get("id") if local else None,
                "workspace_hosting": bool(local and local.get("hosting")),
            })
        return {"teams": announced}

    @app.post("/api/command/join")
    async def join_team(request: Request):
        body = await body_object(request)
        try:
            destination = decode_join_code(str(body.get("join_code", ""))).hex()
        except TeamCodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        async with teams.lock:
            for existing in [*teams.listing()["teams"], *teams.listing(removed=True)["teams"]]:
                if existing["destination"] == destination:
                    teams.entries[existing["id"]].pop("removed_at", None)
                    await teams.start(existing["id"])
                    teams.entries[existing["id"]]["hosting"] = True
                    teams.save()
                    return {"id": existing["id"], "existing": True}
            team_id = uuid.uuid4().hex
            directory = data_dir / "teams" / team_id
            membership = TeamMembership(directory / "team.json")
            membership.join(bytes.fromhex(destination))
            UserProfile(directory / "user.json").update("COMMAND", "beacon", "amber")
            teams.entries[team_id] = {"hosting": True, "mode": "member"}
            # Persist the independent membership before any network operation.
            teams.save()
            try:
                await teams.start(team_id)
            except Exception:
                teams.entries[team_id]["hosting"] = False
                teams.save()
                raise
            try:
                await teams.apps[team_id].state.join_team({"join_code": body["join_code"], "callsign": "COMMAND"})
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                return {"id": team_id, "warning": "Team saved. Its host is offline; synchronization will retry."}
            return {"id": team_id}

    @app.post("/api/command/teams/{team_id}/remove")
    async def remove_team(team_id: str, request: Request):
        body = await body_object(request)
        if body.get("confirm") is not True:
            raise HTTPException(422, "Confirm removal from this Command workspace")
        async with teams.lock:
            teams.directory(team_id)
            await teams.stop(team_id)
            teams.entries[team_id].update(hosting=False, removed_at=int(time.time()))
            teams.save()
            return {"removed": True, "recoverable": True, **teams.listing()}

    @app.post("/api/command/teams/{team_id}/restore")
    async def restore_team(team_id: str):
        async with teams.lock:
            teams.directory(team_id)
            if not teams.entries[team_id].get("removed_at"):
                raise HTTPException(409, "This team is already in the workspace")
            teams.entries[team_id].pop("removed_at", None)
            teams.entries[team_id]["hosting"] = False
            teams.save()
            return teams.listing()

    @app.get("/api/command/overview")
    async def overview():
        async with teams.lock:
            listed = teams.listing()["teams"]
        snapshot = await asyncio.to_thread(teams.overview_snapshot, listed)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    async def map_response(operation, *args):
        try:
            payload, _ = await asyncio.to_thread(operation, *args)
            return Response(payload, media_type="application/x-protobuf", headers={"Cache-Control": "public, max-age=604800"})
        except FileNotFoundError as exc:
            raise HTTPException(404, "Map data not available") from exc
        except Exception as exc:
            raise HTTPException(503, "Map data is not saved and the provider could not be reached") from exc

    @app.get("/api/command/map/tiles/{zoom}/{x}/{y}.pbf")
    async def overview_tile(zoom: int, x: int, y: int):
        return await map_response(overview_maps.tile, zoom, x, y)

    @app.get("/api/command/map/fonts/{fontstack}/{range_name}.pbf")
    async def overview_font(fontstack: str, range_name: str):
        return await map_response(overview_maps.font, fontstack, range_name)

    @app.post("/api/command/teams")
    async def create_team(request: Request):
        body = await body_object(request)
        async with teams.lock:
            team_id = uuid.uuid4().hex
            profile = TeamProfile(data_dir / "teams" / team_id / "team.json")
            try:
                profile.set_name(body.get("name"), body.get("modules", []))
            except TeamCodeError as exc:
                raise HTTPException(422, str(exc)) from exc
            teams.entries[team_id] = {"hosting": True}
            try:
                await teams.start(team_id)
            except Exception:
                # Keep the profile recoverable if transport startup fails.
                teams.entries[team_id]["hosting"] = False
                teams.save()
                raise
            teams.save()
            return {"id": team_id, **teams.listing()}

    @app.post("/api/command/teams/{team_id}/rename")
    async def rename_team(team_id: str, request: Request):
        body = await body_object(request)
        async with teams.lock:
            directory = teams.directory(team_id)
            if teams.entries[team_id].get("removed_at") or teams.entries[team_id].get("mode") == "member":
                raise HTTPException(409, "Only a locally hosted, saved team can be renamed here")
            try:
                if team_id in teams.apps:
                    teams.apps[team_id].state.rename_team(body.get("name"))
                else:
                    profile = TeamProfile(directory / "team.json")
                    if profile.name is None:
                        raise HTTPException(409, "Create a team before renaming it")
                    profile.set_name(body.get("name"))
            except TeamCodeError as exc:
                raise HTTPException(422, str(exc)) from exc
            return teams.listing()

    @app.post("/api/command/teams/{team_id}/hosting")
    async def set_hosting(team_id: str, request: Request):
        body = await body_object(request)
        if not isinstance(body.get("hosting"), bool):
            raise HTTPException(422, "hosting must be true or false")
        async with teams.lock:
            teams.directory(team_id)
            if body["hosting"]:
                await teams.start(team_id)
            else:
                await teams.stop(team_id)
            teams.entries[team_id]["hosting"] = body["hosting"]
            teams.save()
            return teams.listing()

    app.mount("/", teams)
    return app
