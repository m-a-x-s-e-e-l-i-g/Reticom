"""Root-authorized independent Reticulum hosts, durable sync and failover.

This is trusted-host replication, not a consensus system or a private mailbox
E2EE upgrade. Approved backup operators can read the same data as the owner.
No original host private key is ever exported.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from pathlib import Path

import RNS

from .replication import ReplicatedTable
from .team import TeamProfile
from .membership import Membership

ASPECTS = ("retium", "event", "ingress")
MAX_HOSTS = 4


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical(value))
    temporary.replace(path)


def public_identity(public_key):
    if not isinstance(public_key, str):
        raise ValueError("Enter the backup device public key")
    key = bytes.fromhex(public_key)
    if len(key) != 64:
        raise ValueError("invalid host public key")
    identity = RNS.Identity(create_keys=False)
    identity.load_public_key(key)
    return identity


def endpoint(identity):
    return RNS.Destination.hash(identity, *ASPECTS).hex()


def verify_policy(envelope, root, minimum=0):
    if not isinstance(envelope, dict) or set(envelope) != {"policy", "signature"}:
        raise ValueError("invalid continuity policy")
    policy = envelope["policy"]
    if not isinstance(policy, dict) or policy.get("v") != 1 or policy.get("team") != root:
        raise ValueError("wrong continuity team")
    identity = public_identity(policy["owner"])
    if endpoint(identity) != root or not identity.validate(bytes.fromhex(envelope["signature"]), canonical(policy)):
        raise ValueError("continuity policy signature does not match the join code")
    if type(policy.get("revision")) is not int or policy["revision"] < minimum:
        raise ValueError("continuity policy rollback")
    hosts = policy.get("hosts")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= MAX_HOSTS:
        raise ValueError("invalid host list")
    destinations = set()
    for host in hosts:
        if (not isinstance(host, dict) or type(host.get("ready")) is not bool
            or not isinstance(host.get("label"), str) or len(host["label"]) > 40):
            raise ValueError("invalid host")
        dest = endpoint(public_identity(host["public_key"]))
        if dest != host.get("destination") or dest in destinations:
            raise ValueError("invalid host destination")
        destinations.add(dest)
    if not any(h["destination"] == root and h["public_key"] == policy["owner"] and h["ready"] for h in hosts):
        raise ValueError("owner missing from host list")
    if not any(h["destination"] == policy.get("preferred") and h["ready"] for h in hosts):
        raise ValueError("preferred host is not ready")
    return policy


def rpc(identity, destination_hash, path, data, timeout=4.0, max_response_size=1_000_000, public_key=None):
    """An identified encrypted Link; data never traverses the local HTTP API."""
    dest_hash = bytes.fromhex(destination_hash)
    deadline = time.monotonic() + timeout
    if not RNS.Transport.has_path(dest_hash):
        RNS.Transport.request_path(dest_hash)
    while not RNS.Transport.has_path(dest_hash):
        if time.monotonic() >= deadline:
            raise TimeoutError("No path to team host")
        time.sleep(.05)
    remote = public_identity(public_key) if public_key else RNS.Identity.recall(dest_hash)
    if remote is None or endpoint(remote) != destination_hash:
        raise ValueError("host identity mismatch")
    ready = threading.Event()
    link = RNS.Link(RNS.Destination(remote, RNS.Destination.OUT, RNS.Destination.SINGLE, *ASPECTS), established_callback=lambda _: ready.set())
    try:
        if not ready.wait(max(.01, deadline-time.monotonic())) or link.status != RNS.Link.ACTIVE:
            raise TimeoutError("Team host did not answer")
        link.identify(identity)
        time.sleep(max(.1, min(.5, (link.rtt or .05)*2)))
        receipt = link.request(path, data=data, timeout=max(.1, deadline-time.monotonic()), max_response_size=max_response_size)
        if receipt is False:
            raise TimeoutError("Team host request failed")
        while not receipt.concluded() and time.monotonic() < deadline:
            time.sleep(.025)
        if receipt.get_status() != RNS.RequestReceipt.READY:
            raise TimeoutError("Team host request timed out")
        return receipt.get_response(), receipt.get_response_time()
    finally:
        link.teardown()


def json_rpc(*args, **kwargs):
    data, _ = rpc(*args, **kwargs)
    if not isinstance(data, bytes):
        raise ValueError("invalid host response")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid host response")
    if value.get("error"):
        raise PermissionError(value["error"])
    return value


class HostDirectory:
    """Pinned signed policies persist across restarts and host outages."""
    def __init__(self, path: Path, root: str):
        self.path, self.root = path, root
        self.lock = threading.RLock()
        self.envelope = None
        self.failed_until = {}
        self.active = root
        if path.exists():
            self.accept(json.loads(path.read_text()), save=False)

    def accept(self, envelope, save=True):
        with self.lock:
            current = self.envelope["policy"] if self.envelope else None
            policy = verify_policy(envelope, self.root, current["revision"] if current else 0)
            if current and policy["revision"] == current["revision"] and policy != current:
                raise ValueError("conflicting policy revision")
            if current is None or current["preferred"] != policy["preferred"]:
                self.active = policy["preferred"]
            self.envelope = envelope
            if save:
                atomic_json(self.path, envelope)

    def candidates(self):
        with self.lock:
            if not self.envelope:
                return [self.root]
            policy = self.envelope["policy"]
            ready = [h["destination"] for h in policy["hosts"] if h["ready"]]
            ordered = list(dict.fromkeys([self.active, policy["preferred"], *ready]))
            return sorted((h for h in ordered if h in ready), key=lambda h: self.failed_until.get(h, 0) > time.monotonic())

    def failed(self, destination):
        with self.lock:
            self.failed_until[destination] = time.monotonic()+20

    def refresh(self, identity):
        for destination in self.candidates():
            try:
                envelope = json_rpc(identity, destination, "/continuity/policy", {})
                self.accept(envelope)
                return True
            except (ValueError, KeyError, TypeError, OSError, TimeoutError):
                self.failed(destination)
        return False


class SettingsStore:
    def __init__(self, path):
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.execute("CREATE TABLE IF NOT EXISTS team_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._connection.commit()


class TeamContinuity:
    def __init__(self, gateway, profile, tasks, private, audio, root=None):
        self.gateway, self.profile, self.audio = gateway, profile, audio
        self.path = gateway.data_dir / "continuity"
        self.path.mkdir(parents=True, exist_ok=True)
        self.local = gateway.destination.hash.hex()
        self.root = root or self.local
        self.owner = self.root == self.local
        if gateway.membership.root != self.root:
            gateway.membership = Membership(gateway.data_dir, gateway.identity, self.root)
        self.directory = HostDirectory(self.path / "policy.json", self.root)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.status = {}
        self.last_error = None
        self.settings = SettingsStore(self.path / "settings.sqlite3")
        self.tables = {
            "events": ReplicatedTable(gateway.store, "events", "event_id", self.local),
            "tasks": ReplicatedTable(tasks, "tasks", "task_id", self.local),
            "private": ReplicatedTable(private, "private_messages", "event_id", self.local),
            "settings": ReplicatedTable(self.settings, "team_settings", "key", self.local),
        }
        if self.owner and not self.directory.envelope:
            self.sign_policy({"v": 1, "team": self.root, "owner": gateway.identity.get_public_key().hex(), "revision": 1,
                "preferred": self.local, "hosts": [self.host_entry(gateway.identity, "Original host", True)]})
        if self.owner:
            self.save_settings()
        self.apply_settings()
        profile.on_change = self.save_settings
        for path in ("policy", "page", "audio", "ready", "membership"):
            gateway.destination.register_request_handler("/continuity/"+path, response_generator=self.request, allow=RNS.Destination.ALLOW_ALL)
        self.thread = threading.Thread(target=self.run, name="reticom-replication", daemon=True)
        self.thread.start()

    @staticmethod
    def host_entry(identity, label, ready=False):
        return {"destination": endpoint(identity), "public_key": identity.get_public_key().hex(), "label": label, "ready": ready}

    def sign_policy(self, policy):
        envelope = {"policy": policy, "signature": self.gateway.identity.sign(canonical(policy)).hex()}
        self.directory.accept(envelope)

    def approve(self, public_key, label):
        if not self.owner:
            raise PermissionError("Only the original team owner can approve backup hosts")
        identity = public_identity(public_key)
        with self.lock:
            policy = json.loads(json.dumps(self.directory.envelope["policy"]))
            if any(h["destination"] == endpoint(identity) for h in policy["hosts"]):
                return
            if len(policy["hosts"]) >= MAX_HOSTS:
                raise ValueError("A team supports up to four trusted hosts")
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
                raise ValueError("Enter a host name of 1-40 characters")
            policy["hosts"].append(self.host_entry(identity, label.strip()))
            policy["revision"] += 1
            self.sign_policy(policy)

    def prefer(self, destination):
        if not self.owner:
            raise PermissionError("Only the original team owner can hand over preferred hosting")
        with self.lock:
            policy = json.loads(json.dumps(self.directory.envelope["policy"]))
            if not any(h["destination"] == destination and h["ready"] for h in policy["hosts"]):
                raise ValueError("Backup has not completed its first sync")
            if destination != self.local:
                status = self.status.get(destination, {})
                if time.time()-status.get("acknowledged_at", 0) > 15 or status.get("heads") != self.heads():
                    raise ValueError("Wait for the backup to confirm all current data before handover")
            policy["preferred"] = destination
            policy["revision"] += 1
            self.sign_policy(policy)

    def heads(self):
        return {name: table.head() for name, table in self.tables.items()}

    def ready(self):
        return bool(self.gateway.membership.envelope and self.directory.envelope and any(h["destination"] == self.local and h["ready"] for h in self.directory.envelope["policy"]["hosts"]))

    def save_settings(self):
        value = canonical({"name": self.profile.name, "created_at": self.profile.created_at, "modules": self.profile.modules, "everyone_admin": self.profile.everyone_admin}).decode()
        with self.settings._lock, self.settings._connection:
            self.settings._connection.execute("INSERT INTO team_settings VALUES('profile',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value!=excluded.value", (value,))

    def apply_settings(self):
        with self.settings._lock:
            row = self.settings._connection.execute("SELECT value FROM team_settings WHERE key='profile'").fetchone()
        if not row:
            return
        value = json.loads(row[0])
        if not isinstance(value, dict) or set(value) != {"name", "created_at", "modules", "everyone_admin"}:
            raise ValueError("invalid replicated team metadata")
        if value.get("name") is None:
            return
        # Use the same validators as ordinary profile updates before persisting.
        from .team import _validated_modules, TEAM_NAME_MAX_LENGTH
        if not isinstance(value.get("name"), str) or not 1 <= len(value["name"].strip()) <= TEAM_NAME_MAX_LENGTH or type(value.get("created_at")) is not int or type(value.get("everyone_admin")) is not bool:
            raise ValueError("invalid replicated team metadata")
        value["modules"] = _validated_modules(value.get("modules"))
        changed = any(getattr(self.profile, key) != val for key, val in value.items())
        if changed:
            for key, val in value.items():
                setattr(self.profile, key, val)
            atomic_json(self.profile.path, value)
            self.gateway.set_team(self.profile.name, self.profile.modules, self.profile.created_at, self.profile.everyone_admin)

    def request(self, path, data, request_id, link_id, remote_identity, requested_at):
        try:
            if remote_identity is None:
                raise PermissionError("Identified link required")
            if path == "/continuity/policy":
                return canonical(self.directory.envelope)
            if not isinstance(data, dict) or data.get("team") != self.root:
                raise ValueError("Wrong replication team")
            if data.get("policy"):
                self.directory.accept(data["policy"])
            policy = self.directory.envelope["policy"]
            peer = next((h for h in policy["hosts"] if h["public_key"] == remote_identity.get_public_key().hex()), None)
            if peer is None:
                raise PermissionError("Host not approved by team owner")
            if path == "/continuity/membership":
                incoming = data.get("membership")
                if incoming and self.gateway.membership.accept(incoming):
                    self.gateway.enforce_membership()
                # Pending requests are untrusted display labels, never approvals.
                for item in data.get("requests", [])[:128]:
                    self.gateway.membership.request(item["identity"], item["callsign"])
                return canonical({"membership": self.gateway.membership.envelope})
            if path == "/continuity/page":
                return canonical(self.tables[data["table"]].page(data.get("after", 0)))
            if path == "/continuity/audio":
                audio, mime = self.audio.read(data["clip_id"])
                return canonical({"audio": base64.b64encode(audio).decode(), "mime_type": mime})
            if path == "/continuity/ready":
                with self.lock:
                    self.status[peer["destination"]] = {"acknowledged_at": time.time(), "heads": data.get("heads")}
                    if self.owner and not peer["ready"] and data.get("heads") == self.heads():
                        updated = json.loads(json.dumps(policy))
                        next(h for h in updated["hosts"] if h["destination"] == peer["destination"])["ready"] = True
                        updated["revision"] += 1
                        self.sign_policy(updated)
                return canonical(self.directory.envelope)
            raise ValueError("Unknown replication request")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return canonical({"error": str(exc)})

    def sync_peer(self, peer):
        policy = self.directory.envelope
        common = {"team": self.root, "policy": policy}
        access = json_rpc(self.gateway.identity, peer["destination"], "/continuity/membership", {
            **common, "membership": self.gateway.membership.envelope,
            "requests": self.gateway.membership.listing()["requests"]}, public_key=peer["public_key"], max_response_size=150000)
        if access.get("membership") and self.gateway.membership.accept(access["membership"]):
            self.gateway.enforce_membership()
        heads = {}
        for name, table in self.tables.items():
            if self.stop_event.is_set():
                return
            # Bound work per tick without truncating the persistent history.
            for _ in range(8):
                page = json_rpc(self.gateway.identity, peer["destination"], "/continuity/page", {**common, "table": name, "after": table.cursor(peer["destination"])}, public_key=peer["public_key"])
                table.merge(peer["destination"], page, {h["destination"] for h in policy["policy"]["hosts"]})
                heads[name] = page["cursor"]
                if page["cursor"] == page["head"] or self.stop_event.is_set():
                    break
            if page["cursor"] != page["head"]:
                return
        self.apply_settings()
        # Do not advertise a replica as ready while its recorded audio is missing.
        for name in ("events", "private"):
            table = self.tables[name]
            with table.lock:
                records = table.db.execute(f"SELECT payload_json FROM {table.table}").fetchall()
            for record in records:
                event = json.loads(record[0])
                if event.get("type") not in {"ptt.broadcast", "private.ptt"}:
                    continue
                if self.stop_event.is_set():
                    return
                try:
                    self.audio.read(event["clip_id"])
                    continue
                except FileNotFoundError:
                    pass
                clip = json_rpc(self.gateway.identity, peer["destination"], "/continuity/audio", {**common, "clip_id": event["clip_id"]}, public_key=peer["public_key"], timeout=10)
                self.audio.save(event["clip_id"], base64.b64decode(clip["audio"], validate=True), clip["mime_type"])
        reply = json_rpc(self.gateway.identity, peer["destination"], "/continuity/ready", {**common, "heads": heads}, public_key=peer["public_key"])
        self.directory.accept(reply)
        self.status.setdefault(peer["destination"], {}).update({"synced_at": time.time(), "error": None})

    def run(self):
        while not self.stop_event.wait(3):
            envelope = self.directory.envelope
            if not envelope:
                continue
            for peer in envelope["policy"]["hosts"]:
                if peer["destination"] == self.local or self.stop_event.is_set():
                    continue
                try:
                    self.sync_peer(peer)
                    self.last_error = None
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, TimeoutError) as exc:
                    self.last_error = str(exc)
                    self.status.setdefault(peer["destination"], {}).update({"error": str(exc)})

    def state(self):
        return {"team": self.root, "owner": self.owner, "ready": self.ready(), "local_host": self.local,
            "policy": self.directory.envelope["policy"] if self.directory.envelope else None,
            "peers": dict(self.status), "error": self.last_error,
            "replication": "asynchronous trusted-host replication"}

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        self.profile.on_change = None
        self.settings._connection.close()
