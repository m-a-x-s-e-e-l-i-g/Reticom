# Live aircraft and vessels

Open **Map layers → Intel packs** and enable **Live aircraft** or **Live boats & ships**. Both are optional and off on new installations. Zoom to a regional view (level 7+, at most a 250-nautical-mile covering radius). Each pack has a compact **Filters · classification & type** section:

- Classification: all, military, commercial, or other/unknown ownership.
- Aircraft types: light/small/medium/heavy, helicopters, gliders, balloons, reported UAVs and others.
- Vessel types: cargo, tanker, passenger, fishing, sailing, pleasure craft, tug, service and others.
- Search: callsign, registration/model, or vessel name/MMSI. Press **Apply filters** to save.

Filters combine, persist on this device and do not change the provider subscription. Click an icon for its reported position time, classification basis, identity, speed, heading/course and available altitude or destination, plus its recent observed track as a dashed line. Icons point in the reported direction; a diamond means direction is unavailable. Motion between reports is estimated as described below. These are public context layers, not verified team reports or safety-of-navigation instruments.

## Aircraft access and classification

Aircraft use the public [ADSB.lol API](https://www.adsb.lol/docs/open-data/api/), without a key. Its data is ODbL-licensed separately from Reticom. The anonymous quota is 500 requests per day per IP. The backend honors the rate-limit response and retains last-seen positions until they expire. A regional batch can contain up to four requests, and maps share the refresh budget.

For personal, non-commercial use, set `RETICOM_ADSB_FI_PERSONAL=1` to enable the [adsb.fi fallback](https://github.com/adsbfi/opendata). On Windows, Reticom also reads this opt-in from the current user's environment settings if the launcher did not pass a value. The fallback takes over when ADSB.lol is rate limited, fails to respond or returns unusable data. Reticom honors provider cooldowns and shows the source in Map layers and flight details.

**Military** means the provider's database military flag (`dbFlags & 1`), as documented by [readsb](https://github.com/wiedehopf/readsb/blob/dev/README-json.md). **Commercial / airline (estimated)** uses a medium/heavy aircraft category and an airline-style callsign; it is explicitly not verified ownership. A missing military flag does not establish civilian status. Other records remain unknown. Aircraft positions fade after 45 seconds and disappear after 2 minutes without a new position report. Altitude is barometric, converted from feet to metres—not height above the terrain.

## Vessel access and classification

Enable **Live boats & ships** to get regional snapshots from [Open Waters AIS](https://openwaters.io/ais/) without an account or key. Its anonymous tier is for personal use. If you save an [AISStream key](https://aisstream.io/documentation) in Map layers, Reticom prefers that stream and falls back to Open Waters if it fails or stops sending positions.

Save an optional [AISHub contributor key](https://www.aishub.net/join-us) in Map layers for areas where Open Waters returns no recent fixes or is unavailable. Reticom polls AISHub at most once per minute per backend and rotates between active viewports. Vessel popups show the provider and its attribution. The local Python backend handles subscriptions, snapshots and credentials for visible maps.

Selecting a vessel looks up its MMSI on [Open Vessel Photos](https://openvesselphotos.org/docs). When a matching photo is available, the popup shows a thumbnail with the photographer and licence. The photo provider receives the selected public MMSI.

AIS vessel types are self-reported. **Commercial** is inferred from cargo, tanker, passenger, fishing or tug/towing types. **Military / restricted (AIS 35)** includes military *or other restricted operations*, not confirmed military ownership; see the [US Coast Guard AIS guide](https://www.navcen.uscg.gov/sites/default/files/pdf/AIS/AISGuide.pdf). Law-enforcement vessels are classified under service, not automatically military. Positions fade after 2 minutes and disappear after 10 minutes; static metadata cannot renew an old position. Displayed timestamps are stream-report times, not guaranteed GNSS fix times.

## Privacy, lifecycle and limits

### Click to show a recent track

Selecting an aircraft or vessel follows its position and shows its available
observed track as a dashed line. Use **Center** to reposition once, **Pause follow**
to pan freely, or **Show track** to frame the path. Framing the track pauses follow
until you choose **Resume follow**. Only the selected target's track is shown.
It updates with new reports and survives base-map changes. Filtering out or
disabling the target clears the selection.

For a selected aircraft, Reticom requests its full trace from the [ADSB.lol API](https://api.adsb.lol/docs) and merges available fixes from the last 24 hours with locally recorded reports. The request sends the aircraft's public ICAO address. If the API is unavailable, the map uses local fixes. Expand **Flight details** to open the aircraft's daily history on ADSB.lol.

The dashed line curves between reported fixes. A lighter dotted extension connects the latest fix to the estimated marker position. It follows the marker even while camera follow is paused. Reception gaps, provider flight-leg boundaries and implausible jumps break the observed track. Both lines split at the date line. Each map keeps up to 15 minutes of recent fixes in memory. The local backend retains a bounded 24-hour history of observed fixes for aircraft and vessels.

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
network polling. The camera tracks the selected target while follow is active.
Hidden maps and idle predictions stop
animating. Reduced-motion mode shows reported positions without prediction or
interpolation. Disabling/removing a layer clears its interpolation state.

These packs need Internet and can run independently of Reticulum connectivity. Providers receive the map area and public IP. AISStream and AISHub each receive their own API key when used. Team identities, chat and location history are kept local. Source keys are stored in the local Intel-pack settings file. The settings API returns only a masked preview of each key.

Live positions are held in bounded memory. Observed track fixes are retained in the local traffic history database and excluded from offline map downloads. Each pack holds at most 1,500 current targets per view/registry. The UI checks live data every 5 seconds, independently of slower incident layers. Hidden/detached maps stop polling and release vessel subscriptions; abandoned clients expire after 35 seconds. Disabling vessels or replacing/removing the key cancels its socket and clears the registry. Reconnects use exponential backoff with jitter.

## Development

- `src/retium/traffic.py`: validation, provider normalization, aircraft rate limiting, bounded AIS registry and leased stream lifecycle.
- `src/retium/intel_packs.py`: catalogue/settings and `/api/intel-packs/live/{flights|vessels}` endpoints.
- `src/retium/static/traffic-map.js`: direction icons, position expiry and local filters.
- `src/retium/static/traffic-motion.js`: bounded prediction, correction blending and animation lifecycle.
- `src/retium/static/traffic-tracks.js`: bounded observed history and selected dashed track.
- `src/retium/static/intel-packs.js`: existing map-layer panel, subscriptions, filtering and click details.
- `websockets==15.0.1` is included in desktop/Python and Android dependencies.

Run `.venv/Scripts/python.exe -m pytest tests/test_traffic.py tests/test_intel_packs.py -q` and `node --test tests/traffic-map.test.mjs tests/intel-packs.test.mjs`. Tests inject deterministic provider frames only inside the test suite; no fixtures are served as live traffic. Live aircraft reception was checked locally; live vessel reception still requires an authorized AISStream key.
