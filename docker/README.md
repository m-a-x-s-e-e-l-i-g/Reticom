# Reticom in Docker

Run from the repository root with Docker Engine and the Compose plugin, or Docker Desktop with Linux containers:

```sh
docker compose up -d --build --wait
```

- Command: http://localhost:8780
- Field: http://localhost:8781

Create a team in Command, then paste its join code into Field. These are two independent Reticulum identities communicating through an actual TCP interface inside Docker. No sample operators, positions or messages are seeded. Community Internet TCP nodes are enabled on both services.

The containers run as a non-root user. Each has a separate named volume containing its identity, network configuration, team data, audio, offline maps and model cache. An image rebuild or `docker compose down` preserves those volumes. `docker compose down -v` deletes them, including the identities; do not use it to update the application.

```sh
docker compose logs -f
docker compose stop
docker compose up -d --build --wait
```

To run Command alone, use `docker compose up -d --build --wait command`.

With Python installed on the host, `python docker/smoke_test.py` verifies the complete Reticulum path. It creates a `Local team` if Command has no team, joins Field if needed, and sends one explicitly labeled test message. It checks the received event's verified sender identity and prints its network evidence. It refuses to switch Field away from an existing different team. Use `--command http://localhost:8790 --field http://localhost:8791` for alternate ports.

## Ports and phones

The web UIs and Reticulum TCP port bind to localhost by default. If another local installation uses these ports, set `RETICOM_COMMAND_PORT`, `RETICOM_FIELD_PORT` and `RETICOM_TCP_PORT` in a local `.env` file, for example:

```dotenv
RETICOM_COMMAND_PORT=8790
RETICOM_FIELD_PORT=8791
RETICOM_TCP_PORT=4243
```

For an Android APK on the same LAN, set `RETICOM_TCP_BIND` to the laptop's LAN IP in `.env`, then run `docker compose up -d` again. Allow that TCP port in the host firewall if needed. In Android Advanced Network, enter the laptop LAN IP and TCP port as the custom node, restart the app and join the Docker Command team's code. The phone keeps its own local app and GPS; it doesn't need the Docker web UI exposed.

Docker Desktop's bridge network does not expose nearby Wi-Fi multicast discovery to the host LAN. This setup therefore uses TCP. Internet community routes can carry traffic when a path to the team destination exists. A LAN IP is only reachable on that LAN; exposing this node over cellular requires a reachable Internet endpoint or an available community route.

Keep the web API local: it is an administrative API without Internet-facing authentication. Browser GPS and microphone access work on localhost; a remotely exposed plain HTTP UI does not provide the secure context those browser features require.

## Speech and development

Recorded PTT and local speech transcription are included. The first transcription downloads the selected Whisper model and needs Internet access; later runs use the persistent cache. Set `RETIUM_TRANSCRIPTION_MODEL` (default `base`) and `RETIUM_TRANSCRIPTION_LANGUAGE` (default `en`) in `.env` to change them. Browser TTS uses voices available on the browser's device. The container has no direct access to the host microphone or GPS.

For source editing without rebuilding, temporarily mount `./src:/app/src:ro` on the services using a local Compose override. Static UI edits become available on refresh; restart the affected service after Python edits. Restart is deliberate so development reloads do not initialize duplicate Reticulum instances.

Only application source, dependency requirements, the container entrypoint and license enter the Docker build context. Existing laptop and phone data is neither copied into the image nor automatically migrated.

Compose health checks validate each local HTTP backend, not Internet or team reachability. Startup waits for Command to become healthy before Field starts, following [Docker's startup-order mechanism](https://docs.docker.com/compose/how-tos/startup-order/).
