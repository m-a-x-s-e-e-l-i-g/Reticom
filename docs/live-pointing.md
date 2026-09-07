# Live phone pointing

Reticom shares the direction of a phone, not the user's eyes or a verified line
of sight. Field, Command and All Teams show a short translucent arrow from a
teammate's recent position. Your own long bearing line stays local.

## Use

- Updated Android builds enable **User Settings → Share facing direction** by
  default. Turn it off to remain receive-only.
- Keep Reticom visible and the screen unlocked. Sharing stops when the app
  pauses, the screen locks, the compass becomes stale, or the switch is off.
- Hold the phone flat to point its top edge, or upright to point its rear camera.
  The transition uses tilt hysteresis. Nearby magnets and compass calibration
  can affect accuracy; this is approximate direction, not precision surveying.
- A location fix is needed to correct magnetic heading to geographic north.
  An operator arrow additionally requires a shared position no older than two
  minutes with reported accuracy of 100 metres or better. Live heading does not
  imply live GPS. Enable regular position sharing separately.
- Headings disappear after four seconds without a fresh sample. Turning sharing
  off sends a stop immediately if connected; expiry handles lost stop packets.
- Both the team host and receiving/sending clients need this update. Older hosts
  simply never acknowledge the heading handshake; ordinary messages still work.
  The clock-aware handshake requires updated hosts and clients. It measures
  clock offset over the encrypted link using an echoed probe (at most 2 seconds
  round trip). Samples still expire after four seconds; clock differences do
  not require changing the phone or laptop clock.

## Transport

The browser uses `/api/heading/live`, a dedicated WebSocket to its local node.
Field opens a separate identified, encrypted Reticulum Link to the team's active
host. The host derives the sender identity from that Link, then relays samples.
It follows the application's existing team access/trust model; the host can read
the telemetry. This is not a new member-admission or host-blind encryption layer.

Frames are small binary packets: 13 bytes uplink and 29 bytes on relay, excluding
Reticulum and carrier overhead. They bypass the reliable voice Channel and the
event store. Samples are never retried or written to the outbox. Receivers keep
the latest sample per sender, reject reordered/expired samples, and interpolate
the shortest angular turn. No prediction beyond the last measured heading.

Native sensor delivery is capped at 20 Hz. Network changes are capped at 10 Hz,
with a one-degree threshold and a stationary heartbeat once per second. A link's
establishment-rate estimate lowers the rate on slower routes (up to two seconds
between samples). This is an estimate, not measured radio signal strength or a
guarantee of available bandwidth. Multiple operators share the route's capacity.

The browser declines sends when its WebSocket buffer grows; server fan-out
coalesces unsent headings. Underlying TCP/Reticulum interfaces may still buffer
traffic, so this is best-effort low-latency telemetry, not guaranteed real-time.
Expired packets are discarded. Clients calibrate against the host with a
correlated clock probe before sending or receiving headings, translating relay
timestamps back into local node time. The browser separately calibrates against its local node
using a WebSocket round trip every two seconds, accepting only replies within
one second. This handles Windows/Docker clock skew without relaxing expiry.
Calibration is never inferred from heading samples, and reconnecting clears it.

An available team host or approved continuity host is still required for sharing.
A community transport node is a route, not an application host. Local compass
continues independently; heading delivery is not recovered from offline history.

## Validation

```powershell
.venv\Scripts\python.exe -m pytest tests/test_live_heading.py -q
node --test tests/live-heading.test.mjs
$env:RETICOM_NETWORK_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_heading_network.py -q -s
```

The opt-in integration test runs an isolated TCP carrier, a real Reticulum host
and two real Field nodes with separate identities. It checks authenticated sender
attribution, sample delivery, stop delivery, and absence of history/outbox entries.
Local-loopback latency measurements are not cellular performance measurements.

`tests/CompassBearingCheck.java` can be compiled together with
`android/app/src/main/java/com/retium/field/CompassBearing.java` and run as
`com.retium.field.CompassBearingCheck` using Java 17. It checks flat and upright
cardinal bearings without requiring an Android emulator.

Physical-device checks still matter: rotate the phone through north, hold it
upright, lock the screen, switch apps, toggle sharing, and disconnect/reconnect
cellular data while watching a second updated client. Browser fixtures do not
validate a handset's magnetometer or Android power management.
