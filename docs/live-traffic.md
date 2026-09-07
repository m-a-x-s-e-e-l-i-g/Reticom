# Live aircraft and vessels

Open **Map layers → Intel packs** and enable **Live aircraft** or **Live boats & ships**. Both are optional and off on new installations. Zoom to a regional view (level 7+, at most a 250-nautical-mile covering radius). Each pack has a compact **Filters · classification & type** section:

- Classification: all, military, commercial, or other/unknown ownership.
- Aircraft types: light/small/medium/heavy, helicopters, gliders, balloons, reported UAVs and others.
- Vessel types: cargo, tanker, passenger, fishing, sailing, pleasure craft, tug, service and others.
- Search: callsign, registration/model, or vessel name/MMSI. Press **Apply filters** to save.

Filters combine, persist on this device and do not change the provider subscription. Click an icon for its reported position time, classification basis, identity, speed, heading/course and available altitude or destination, plus its recent observed track as a dashed line. Icons point in the reported direction; a diamond means direction is unavailable. Motion between reports is estimated as described below. These are public context layers, not verified team reports or safety-of-navigation instruments.

## Aircraft access and classification

Aircraft use the public [ADSB.lol API](https://www.adsb.lol/docs/open-data/api/), without a key. Its data is ODbL-licensed separately from Reticom. Requests cover the visible regional map; they are serialized to at most one attempt per 10 seconds per local backend. Multiple visible viewports/date-line halves share that budget, so individual views may refresh more slowly. Failed requests back off for 30 seconds.

**Military** means the provider's database military flag (`dbFlags & 1`), as documented by [readsb](https://github.com/wiedehopf/readsb/blob/dev/README-json.md). **Commercial / airline (estimated)** uses a medium/heavy aircraft category and an airline-style callsign; it is explicitly not verified ownership. A missing military flag does not establish civilian status. Other records remain unknown. Aircraft positions fade after 45 seconds and disappear after 2 minutes without a new position report. Altitude is barometric, converted from feet to metres—not height above the terrain.

## Vessel access and classification

Get your own key from [AISStream](https://aisstream.io/documentation), then enter it under **Map layers → Source access → AISStream API key** and save. Confirm that your intended use is permitted by the provider; this integration does not assert that AISStream data has an unrestricted redistribution licence. There is no bundled/shared key or scraped tracking service.

The Python backend—not the browser/WebView—opens one compressed, authenticated TLS WebSocket, shared by the visible maps on that backend. AISStream publishes account/IP connection limits; separate Reticom devices each count as a connection. Position and static-data messages arrive separately, so vessel names and types may appear after the first position. The stream does not supply historical replay. An empty view can mean no reports have arrived yet or there is no receiver coverage.

AIS vessel types are self-reported. **Commercial** is inferred from cargo, tanker, passenger, fishing or tug/towing types. **Military / restricted (AIS 35)** includes military *or other restricted operations*, not confirmed military ownership; see the [US Coast Guard AIS guide](https://www.navcen.uscg.gov/sites/default/files/pdf/AIS/AISGuide.pdf). Law-enforcement vessels are classified under service, not automatically military. Positions fade after 2 minutes and disappear after 10 minutes; static metadata cannot renew an old position. Displayed timestamps are stream-report times, not guaranteed GNSS fix times.

## Privacy, lifecycle and limits

### Click to show a recent track

Selecting an aircraft or vessel shows its recent observed positions as a dashed
line, without moving the map camera. Only the selected target's track is shown;
close its popup or click elsewhere to clear it. It updates with new reports and
survives base-map changes. Filtering out, disabling or expiring the target clears
the selection.

Tracks accumulate from real reports received while this map is open, including
before selecting an icon. There is no provider historical replay or planned
itinerary: a newly seen target needs a second consecutive report before a line
can be drawn. Estimated animation positions are never recorded, so the track
ends at the last reported position, not the projected icon. Reception gaps and
implausible jumps break the line; a segment is not proof of the exact path taken
between fixes. Date-line crossings are split to avoid world-spanning lines.

Each map keeps at most 15 minutes, 90 fixes per target and 500 recently updated
targets per traffic pack in memory. Closing/reloading the map or disabling the
pack clears this history. No additional provider requests or team messages are
sent to collect or view tracks.

### Smooth estimated motion

Icons advance locally between position reports using reported ground speed and
course. Aircraft prediction is capped at 30 seconds beyond a report; vessels at
60 seconds. After that the estimate holds, then fades/expires at the existing
age limits. Report timestamps and source coordinates are never rewritten. The
popup identifies estimated, correcting, held and reported positions.

Small position/heading corrections blend over one second, using the short path
across north/the date line. Large corrections (over 2.5 km for aircraft, 300 m for
boats) snap to the new report rather than inventing a journey across the map.
Boats use AIS course over ground for translation, separately from bow heading;
anchored/moored boats and speeds below 0.5 knots do not extrapolate. Missing or
invalid speed/course does not invent motion. No acceleration, turn prediction,
collision avoidance or route-following is implied.

One animation loop updates traffic only, capped at 30 frames/sec, with no extra
network polling or map-camera movement. Hidden maps and idle predictions stop
animating. Reduced-motion mode shows reported positions without prediction or
interpolation. Disabling/removing a layer clears its interpolation state.

These packs need Internet but do not require Command or Reticulum connectivity. Providers receive the map area and public IP; the vessel provider also receives its API key. Team identities, chat and location history are not sent. Source keys are stored in the local Intel-pack settings file, not an encrypted credential vault; protect that device/data directory. Keys are never returned by the settings API, included in map features, sent over Reticulum or exposed in UI errors.

Live positions and recent tracks are held in bounded memory, not written to the persistent Intel cache or offline map downloads. Each pack holds at most 1,500 current targets per view/registry. The UI checks live data every 5 seconds, independently of slower incident layers. Hidden/detached maps stop polling and release vessel subscriptions; abandoned clients expire after 35 seconds. Disabling vessels or replacing/removing the key cancels its socket and clears the registry. Reconnects use exponential backoff with jitter. No track history is retained on disk.

## Development

- `src/retium/traffic.py`: validation, provider normalization, aircraft rate limiting, bounded AIS registry and leased stream lifecycle.
- `src/retium/intel_packs.py`: catalogue/settings and `/api/intel-packs/live/{flights|vessels}` endpoints.
- `src/retium/static/traffic-map.js`: direction icons, position expiry and local filters.
- `src/retium/static/traffic-motion.js`: bounded prediction, correction blending and animation lifecycle.
- `src/retium/static/traffic-tracks.js`: bounded observed history and selected dashed track.
- `src/retium/static/intel-packs.js`: existing map-layer panel, subscriptions, filtering and click details.
- `websockets==15.0.1` is included in desktop/Python and Android dependencies.

Run `.venv/Scripts/python.exe -m pytest tests/test_traffic.py tests/test_intel_packs.py -q` and `node --test tests/traffic-map.test.mjs tests/intel-packs.test.mjs`. Tests inject deterministic provider frames only inside the test suite; no fixtures are served as live traffic. Live aircraft reception was checked locally; live vessel reception still requires an authorized AISStream key.
