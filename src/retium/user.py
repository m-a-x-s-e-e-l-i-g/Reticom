from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .protocol import (
    MAX_CALLSIGN_LENGTH,
    OPERATOR_COLORS,
    OPERATOR_ICONS,
    ProtocolError,
)

DEFAULT_CALLSIGN = "FIELD-1"
DEFAULT_ICON = "dot"
DEFAULT_COLOR = "moss"


class UserProfile:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.callsign = DEFAULT_CALLSIGN
        self.icon = DEFAULT_ICON
        self.color = DEFAULT_COLOR
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            callsign = data.get("callsign")
            icon = data.get("icon")
            color = data.get("color")
            if isinstance(callsign, str) and 1 <= len(callsign.strip()) <= MAX_CALLSIGN_LENGTH:
                self.callsign = callsign.strip()
            if icon in OPERATOR_ICONS:
                self.icon = str(icon)
            if color in OPERATOR_COLORS:
                self.color = str(color)

    def update(self, callsign: Any, icon: Any, color: Any = None) -> dict[str, str]:
        clean_callsign = callsign.strip() if isinstance(callsign, str) else ""
        if not clean_callsign or len(clean_callsign) > MAX_CALLSIGN_LENGTH:
            raise ProtocolError(f"callsign must be 1-{MAX_CALLSIGN_LENGTH} characters")
        if icon not in OPERATOR_ICONS:
            raise ProtocolError("unsupported operator icon")
        if color is None:
            color = self.color
        if color not in OPERATOR_COLORS:
            raise ProtocolError("unsupported operator color")
        self.callsign = clean_callsign
        self.icon = str(icon)
        self.color = str(color)
        self.path.write_text(
            json.dumps(self.state(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.state()

    def state(self) -> dict[str, str]:
        return {"callsign": self.callsign, "icon": self.icon, "color": self.color}
