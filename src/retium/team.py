from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from pathlib import Path
from typing import Any

JOIN_CODE_PREFIX = "RTM1"
TEAM_NAME_MAX_LENGTH = 40
AVAILABLE_MODULES = {"tasks"}


class TeamCodeError(ValueError):
    pass


def _validated_modules(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TeamCodeError("modules must be a list")
    modules = {item.strip().lower() for item in value}
    unsupported = modules - AVAILABLE_MODULES
    if unsupported:
        raise TeamCodeError(f"unsupported module: {sorted(unsupported)[0]}")
    return sorted(modules)


def _b32encode(value: bytes) -> str:
    return base64.b32encode(value).decode("ascii").rstrip("=")


def encode_join_code(destination_hash: bytes | str) -> str:
    if isinstance(destination_hash, str):
        try:
            destination_hash = bytes.fromhex(destination_hash)
        except ValueError as exc:
            raise TeamCodeError("destination must be hexadecimal") from exc
    if len(destination_hash) != 16:
        raise TeamCodeError("destination must be 16 bytes")
    payload = _b32encode(destination_hash)
    checksum = _b32encode(hashlib.blake2s(destination_hash, digest_size=2).digest())
    grouped = "-".join(
        (payload + checksum)[index : index + 4]
        for index in range(0, len(payload + checksum), 4)
    )
    return f"{JOIN_CODE_PREFIX}-{grouped}"


def decode_join_code(value: str) -> bytes:
    compact = "".join(character for character in value.upper() if character.isalnum())
    if not compact.startswith(JOIN_CODE_PREFIX):
        raise TeamCodeError("join code must start with RTM1")
    encoded = compact[len(JOIN_CODE_PREFIX) :]
    if len(encoded) != 30:
        raise TeamCodeError("join code has the wrong length")
    payload, checksum = encoded[:26], encoded[26:]
    try:
        padded = payload + "=" * ((8 - len(payload) % 8) % 8)
        destination_hash = base64.b32decode(padded, casefold=True)
    except (ValueError, binascii.Error) as exc:
        raise TeamCodeError("join code contains invalid characters") from exc
    if len(destination_hash) != 16:
        raise TeamCodeError("join code contains an invalid destination")
    expected = _b32encode(hashlib.blake2s(destination_hash, digest_size=2).digest())
    if checksum != expected:
        raise TeamCodeError("join code checksum does not match")
    return destination_hash


class TeamProfile:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.name: str | None = None
        self.created_at: int | None = None
        self.modules: list[str] = []
        self.everyone_admin = False
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stored_name = str(data.get("name", "")).strip()
            self.name = stored_name or None
            stored_created_at = data.get("created_at")
            self.created_at = (
                stored_created_at if isinstance(stored_created_at, int) else int(time.time())
            )
            self.modules = _validated_modules(data.get("modules", []))
            self.everyone_admin = data.get("everyone_admin") is True

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "created_at": self.created_at,
                    "modules": self.modules,
                    "everyone_admin": self.everyone_admin,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def set_name(self, value: Any, modules: Any = None) -> str:
        name = value.strip() if isinstance(value, str) else ""
        if not name or len(name) > TEAM_NAME_MAX_LENGTH:
            raise TeamCodeError(f"team name must be 1-{TEAM_NAME_MAX_LENGTH} characters")
        self.name = name
        if self.created_at is None:
            self.created_at = int(time.time())
        if modules is not None:
            self.modules = _validated_modules(modules)
        self._save()
        return name

    def set_modules(self, modules: Any) -> list[str]:
        if self.name is None:
            raise TeamCodeError("create a team before enabling modules")
        self.modules = _validated_modules(modules)
        self._save()
        return self.modules

    def set_everyone_admin(self, value: Any) -> bool:
        if self.name is None:
            raise TeamCodeError("create a team before changing permissions")
        if not isinstance(value, bool):
            raise TeamCodeError("everyone_admin must be true or false")
        self.everyone_admin = value
        self._save()
        return self.everyone_admin

    def state(self, destination_hash: bytes | str) -> dict[str, Any]:
        if isinstance(destination_hash, bytes):
            destination = destination_hash.hex()
        else:
            destination = destination_hash
        created = self.name is not None
        return {
            "created": created,
            "name": self.name,
            "destination": destination if created else None,
            "join_code": encode_join_code(destination) if created else None,
            "created_at": self.created_at,
            "modules": self.modules,
            "everyone_admin": self.everyone_admin,
            "available_modules": sorted(AVAILABLE_MODULES),
            "access_control": "pairing-only",
        }


class TeamMembership:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.destination: str | None = None
        self.name: str | None = None
        self.joined_at: int | None = None
        self.modules: list[str] = []
        self.everyone_admin = False
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            destination = str(data.get("destination", ""))
            try:
                decoded = bytes.fromhex(destination)
            except ValueError:
                decoded = b""
            if len(decoded) == 16:
                self.destination = destination.lower()
                name = data.get("name")
                self.name = name.strip() if isinstance(name, str) and name.strip() else None
                joined_at = data.get("joined_at")
                self.joined_at = joined_at if isinstance(joined_at, int) else None
                self.modules = _validated_modules(data.get("modules", []))
                self.everyone_admin = data.get("everyone_admin") is True

    @property
    def joined(self) -> bool:
        return self.destination is not None

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "destination": self.destination,
                    "name": self.name,
                    "joined_at": self.joined_at,
                    "modules": self.modules,
                    "everyone_admin": self.everyone_admin,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def join(
        self,
        destination: bytes | str,
        name: str | None = None,
        modules: Any = None,
        everyone_admin: Any = False,
    ) -> None:
        destination_hash = destination.hex() if isinstance(destination, bytes) else destination
        encode_join_code(destination_hash)
        self.destination = destination_hash.lower()
        self.name = name.strip() if isinstance(name, str) and name.strip() else None
        self.joined_at = int(time.time())
        self.modules = _validated_modules(modules)
        if not isinstance(everyone_admin, bool):
            raise TeamCodeError("everyone_admin must be true or false")
        self.everyone_admin = everyone_admin
        self._save()

    def update_metadata(
        self, name: str | None, modules: Any, everyone_admin: Any = False
    ) -> None:
        if not self.joined:
            return
        clean_name = name.strip() if isinstance(name, str) and name.strip() else self.name
        clean_modules = _validated_modules(modules)
        if not isinstance(everyone_admin, bool):
            raise TeamCodeError("everyone_admin must be true or false")
        if (
            clean_name != self.name
            or clean_modules != self.modules
            or everyone_admin != self.everyone_admin
        ):
            self.name = clean_name
            self.modules = clean_modules
            self.everyone_admin = everyone_admin
            self._save()

    def leave(self) -> None:
        self.destination = None
        self.name = None
        self.joined_at = None
        self.modules = []
        self.everyone_admin = False
        self._save()

    def state(
        self,
        discovered_name: str | None = None,
        discovered_modules: Any = None,
        discovered_everyone_admin: Any = None,
    ) -> dict[str, Any]:
        name = discovered_name or self.name
        modules = (
            _validated_modules(discovered_modules)
            if discovered_modules is not None
            else self.modules
        )
        everyone_admin = (
            discovered_everyone_admin
            if isinstance(discovered_everyone_admin, bool)
            else self.everyone_admin
        )
        return {
            "joined": self.joined,
            "name": name,
            "destination": self.destination,
            "join_code": encode_join_code(self.destination) if self.destination else None,
            "joined_at": self.joined_at,
            "modules": modules,
            "everyone_admin": everyone_admin,
            "available_modules": sorted(AVAILABLE_MODULES),
            "access_control": "pairing-only",
        }
