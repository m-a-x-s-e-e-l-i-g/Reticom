"""Owner-signed admission policy. A join code or signed event is not approval."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import RNS

MAX_MEMBERS = 512
MAX_PENDING = 128


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def endpoint(identity):
    return RNS.Destination.hash(identity, "retium", "event", "ingress").hex()


def identity_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid member identity")
    return value


def label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise ValueError("Enter a callsign of 1-40 characters")
    return value.strip()


class Membership:
    def __init__(self, directory: Path, identity, root=None):
        self.identity = identity
        self.root = root or endpoint(identity)
        self.owner = endpoint(identity) == self.root
        self.directory = Path(directory) / "membership" / self.root
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "policy.json"
        self.roles_path = self.directory / "admin-roles.json"
        self.pending_path = self.directory / "requests.json"
        self.lock = threading.RLock()
        self.envelope = None
        self.roles = None
        self.owner_hash = identity.hash.hex() if self.owner else None
        self.pending = {}
        if self.path.exists():
            self.accept(json.loads(self.path.read_text()), save=False)
        if self.roles_path.exists() and self.envelope is not None:
            self.accept_roles(json.loads(self.roles_path.read_text()), save=False)
        if self.pending_path.exists():
            value = json.loads(self.pending_path.read_text())
            if not isinstance(value, dict) or len(value) > MAX_PENDING:
                raise ValueError("Invalid membership requests")
            for key, item in value.items():
                identity_hash(key); label(item["callsign"])
            self.pending = value
        if self.owner and self.envelope is None:
            self._sign({"v": 1, "team": self.root, "owner": identity.get_public_key().hex(), "revision": 1, "members": {}})

    @staticmethod
    def write(path, value):
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(canonical(value))
        temporary.chmod(0o600)
        temporary.replace(path)

    def _sign(self, policy):
        self.accept({"policy": policy, "signature": self.identity.sign(canonical(policy)).hex()})

    def accept(self, envelope, save=True):
        with self.lock:
            if not isinstance(envelope, dict) or set(envelope) != {"policy", "signature"} or len(canonical(envelope)) > 150000:
                raise ValueError("Invalid membership policy")
            policy = envelope["policy"]
            if not isinstance(policy, dict) or set(policy) != {"v", "team", "owner", "revision", "members"} or policy["v"] != 1 or policy["team"] != self.root:
                raise ValueError("Wrong membership team")
            public = RNS.Identity(create_keys=False)
            key = bytes.fromhex(policy["owner"])
            if len(key) != 64:
                raise ValueError("Invalid membership signer")
            public.load_public_key(key)
            if endpoint(public) != self.root or not public.validate(bytes.fromhex(envelope["signature"]), canonical(policy)):
                raise ValueError("Membership approval is not signed by the team owner")
            if type(policy["revision"]) is not int or policy["revision"] < 1 or not isinstance(policy["members"], dict) or len(policy["members"]) > MAX_MEMBERS:
                raise ValueError("Invalid membership revision or roster")
            for key, member in policy["members"].items():
                identity_hash(key)
                if not isinstance(member, dict) or set(member) != {"status", "callsign"} or member["status"] not in {"approved", "revoked", "rejected", "removed"}:
                    raise ValueError("Invalid member decision")
                label(member["callsign"])
            if self.envelope:
                current = self.envelope["policy"]
                if policy["revision"] < current["revision"]:
                    return False  # Stale peers must not undo a revocation.
                if policy["revision"] == current["revision"]:
                    if policy != current:
                        raise ValueError("Conflicting membership revision")
                    return False
            if save:
                self.write(self.path, envelope)
            self.envelope = json.loads(json.dumps(envelope))
            self.owner_hash = public.hash.hex()
            return True

    def status(self, sender):
        with self.lock:
            if self.owner_hash is not None and sender == self.owner_hash:
                return "approved"
            if self.envelope:
                return self.envelope["policy"]["members"].get(sender, {}).get("status", "pending")
            return "pending"

    def approved(self, sender):
        return self.status(sender) == "approved"

    def accept_roles(self, envelope, save=True):
        # Keep the existing signed roster format readable by older backup hosts.
        with self.lock:
            if not self.envelope or not isinstance(envelope, dict) or set(envelope) != {"policy", "signature"}:
                raise ValueError("Invalid admin roles")
            policy = envelope["policy"]
            if (not isinstance(policy, dict) or set(policy) != {"team", "revision", "admins"}
                    or policy["team"] != self.root or type(policy["revision"]) is not int or policy["revision"] < 1
                    or not isinstance(policy["admins"], list) or len(policy["admins"]) > MAX_MEMBERS):
                raise ValueError("Invalid admin role policy")
            for key in policy["admins"]:
                identity_hash(key)
            public = RNS.Identity(create_keys=False)
            public.load_public_key(bytes.fromhex(self.envelope["policy"]["owner"]))
            if not public.validate(bytes.fromhex(envelope["signature"]), canonical(policy)):
                raise ValueError("Admin roles must be signed by the team owner")
            if self.roles:
                old = self.roles["policy"]
                if policy["revision"] < old["revision"]:
                    return False
                if policy["revision"] == old["revision"]:
                    if policy != old:
                        raise ValueError("Conflicting admin roles")
                    return False
            if save:
                self.write(self.roles_path, envelope)
            self.roles = json.loads(json.dumps(envelope))
            return True

    def is_admin(self, sender):
        with self.lock:
            return bool(self.approved(sender) and self.roles and self.envelope
                        and self.roles["policy"]["revision"] == self.envelope["policy"]["revision"]
                        and sender in self.roles["policy"]["admins"])

    def request(self, sender, callsign, *, command=False):
        identity_hash(sender); callsign = label(callsign)
        if type(command) is not bool:
            raise ValueError("Invalid Command request")
        with self.lock:
            status = self.status(sender)
            if status == "pending" and (sender not in self.pending or command and not self.pending[sender].get("command")):
                if sender not in self.pending and len(self.pending) >= MAX_PENDING:
                    raise ValueError("Membership request list is full; contact the team owner")
                self.pending[sender] = {"callsign": callsign, "requested_at": int(time.time())}
                if command:
                    self.pending[sender]["command"] = True
                self.write(self.pending_path, self.pending)
            return {"status": status, "identity": sender, "approval_required": True}

    def decide(self, sender, status, callsign=None, *, admin=None):
        identity_hash(sender)
        if not self.owner:
            raise PermissionError("Only the original team owner can change membership")
        if status not in {"approved", "rejected", "revoked", "removed"}:
            raise ValueError("Choose approve, reject, revoke or remove")
        if admin is not None and (type(admin) is not bool or status != "approved"):
            raise ValueError("Only an approved member can receive admin rights")
        if sender == self.identity.hash.hex():
            raise ValueError("The team owner cannot revoke itself")
        with self.lock:
            policy = json.loads(json.dumps(self.envelope["policy"]))
            previous = policy["members"].get(sender, {})
            name = label(callsign or previous.get("callsign") or self.pending.get(sender, {}).get("callsign") or "Operator")
            if sender not in policy["members"] and len(policy["members"]) >= MAX_MEMBERS:
                raise ValueError("Membership roster is full")
            policy["members"][sender] = {"status": status, "callsign": "Removed operator" if status == "removed" else name}
            policy["revision"] += 1
            self._sign(policy)
            if self.roles or admin is True:
                admins = set(self.roles["policy"]["admins"]) if self.roles else set()
                if status != "approved" or admin is False or previous.get("status") != "approved":
                    admins.discard(sender)
                if admin is True:
                    admins.add(sender)
                roles = {"team": self.root, "revision": policy["revision"],
                         "admins": sorted(key for key in admins if policy["members"].get(key, {}).get("status") == "approved")}
                self.accept_roles({"policy": roles, "signature": self.identity.sign(canonical(roles)).hex()})
            self.pending.pop(sender, None)
            self.write(self.pending_path, self.pending)

    def removed_identities(self):
        with self.lock:
            members = self.envelope["policy"]["members"] if self.envelope else {}
            return {key for key, value in members.items() if value["status"] == "removed"}

    def listing(self):
        with self.lock:
            members = self.envelope["policy"]["members"] if self.envelope else {}
            return {"owner": self.owner, "approval_required": True,
                "members": [{"identity": key, **value, **({"admin": True} if self.is_admin(key) else {})}
                            for key, value in members.items() if value["status"] != "removed"],
                "requests": [{"identity": key, **value} for key, value in self.pending.items() if key not in members]}
