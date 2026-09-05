"""Create/join a local team if needed and send one real Reticulum test message."""

import argparse
import json
import time
import urllib.request


def request(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="http://localhost:8780")
    parser.add_argument("--field", default="http://localhost:8781")
    args = parser.parse_args()
    command = request(args.command, "/api/state")
    field = request(args.field, "/api/state")
    if command["role"] != "gateway" or field["role"] != "field":
        raise SystemExit("Expected a Command endpoint and a Field endpoint")
    if field["team"]["joined"] and (
        field["team"]["destination"] != command["team"]["destination"]
    ):
        raise SystemExit("Field is already in another team; leaving it unchanged")
    team = command["team"]
    if not team["created"]:
        team = request(args.command, "/api/team/create", {"name": "Local team"})
    if not field["team"]["joined"]:
        request(args.field, "/api/team/join", {"join_code": team["join_code"]})
    sent = request(args.field, "/api/send", {
        "type": "chat.message",
        "message": "Docker connectivity check: real Reticulum transmission.",
    })
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        request(args.field, "/api/feed")
        state = request(args.command, "/api/state")
        received = next((e for e in state["events"] if e["id"] == sent["event"]["id"]), None)
        if received is not None:
            evidence = received["network"]
            if not evidence["verified"] or evidence["sender_hash"] != field["network"]["identity"]:
                raise SystemExit("Received event did not match Field's verified identity")
            print(json.dumps({"event_id": received["id"], "network": evidence}, indent=2))
            return
        time.sleep(1)
    raise SystemExit("Command did not receive the Field event within 60 seconds")


if __name__ == "__main__":
    main()
