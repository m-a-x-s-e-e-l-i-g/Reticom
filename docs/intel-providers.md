# Public Intel packs

These optional map layers are **external context, not verified team reports**. They do not create Reticulum events, trigger team TTS, or become mission-pack markers. Enabling a layer allows this device to request public data over the Internet. Viewport-based providers receive the requested map bounds and the device's public IP; requests do not include operator identities or the team's history.

## Available sources

New installations enable **Disaster alerts** and **Military areas, airstrips & police**. Droughts are excluded by default; the other six GDACS types are included. Explicitly saved intel choices (including everything off or droughts on) are preserved on upgrades. Trails are now always-on basemap features, not a selectable intel pack; legacy trail-overlay preferences are ignored by the current UI.

The **Map layers** panel separates **Map & terrain** (base map, metre-labelled contour lines, national LiDAR raster and ground-height lookup) from **Intel packs** (public incident and area overlays). Hiking is the default, including for the old plain-dark preference. Satellite remains selectable and also shows trails. Paths and tracks use the road color with dashed strokes and no solid underlay. Their visibility depends on available map tiles and zoom, not an intel toggle. Path names, peaks and parks use the existing cached OpenFreeMap/OpenMapTiles vector tiles; offline map downloads support this style. There are no additional Overpass trail requests in the current UI. The old trail endpoint/cache remains for compatibility with earlier clients; it is not a second current map layer. Named hiking-route relations not present in the basemap are not separately fetched. Contours use cached elevation tiles; see [elevation](elevation.md). National LiDAR is an online layer for the Netherlands, England, France, Malta and the United States; see [International LiDAR elevation](ahn-lidar.md).

Waypoint/operator navigation offers **Direct line**, **Walking route** and **Driving route**. Walking uses the actual worldwide foot graph at [FOSSGIS routing](https://routing.openstreetmap.de/about.html), not a driving route with a renamed label. Route requests are separated by at least 1.1 seconds, and movement-based recalculation retains a 15-second minimum interval. New calculated routes need Internet and send the origin/destination to the provider. Routing data can differ from map data; access, conditions and difficult trails require independent checks. Public routing is for light use and has no availability guarantee. Existing route sharing transmits the selected geometry; followers do not recalculate it as a road route.

Mapy's official outdoor map/routing API is an alternative for a future configured integration. It requires a developer project/API key and has credit limits; this build does not scrape Mapy tiles or embed an unauthorized key. See [Mapy API FAQ](https://developer.mapy.com/frequently-asked-questions/).

| Pack | Data shown | Access | Refresh cache |
| --- | --- | --- | --- |
| Hiking trails (legacy endpoint only) | OSM paths and walking/hiking route relations for older clients | No key; zoom 12+ | 24 hours |
| Military areas, airstrips & police | Local Overture Maps snapshot, regional OSM Overpass results, or OpenFreeMap military land use | No key. Overpass downloads start at zoom 9. | Worldwide snapshot on disk, 24-hour regional cache, or downloaded basemap tiles |
| Disaster alerts | Published GDACS app-feed alerts worldwide | No key | 15 minutes |
| International NOTAMs | Geolocated aviation notices distributed worldwide by FAA NMS, via BravoEcho | BravoEcho API key on the team host or this device, zoom 6+, off by default | 5 minutes |
| Road closures & disruptions | NDW Netherlands incidents and NCDOT North Carolina work zones; supplied road segments and points | No key; off by default; partial geographic coverage | 2 minutes NL / 5 minutes NC |
| Satellite heat detections | NASA FIRMS VIIRS NOAA-20 NRT observations, latest 3 days by default | Free FIRMS MAP_KEY on the host or device, zoom 4+ | 24 hours |
| Live aircraft | Reported aircraft positions, direction, altitude and type filters | No key; regional views, zoom 7+ | About 10 seconds; memory only |
| Live boats & ships | AISStream with Open Waters AIS and optional AISHub fallbacks | Regional views, zoom 6+. Open Waters supports anonymous personal use. AISStream and AISHub use their own keys. | Stream or periodic regional snapshots |
| Public transport | Agency vehicle positions plus estimated positions from Transitous realtime trip updates | No key. Zoom 10+, off by default. Coverage depends on feed availability. | 30–60 seconds in memory |
| National LiDAR elevation | PDOK AHN, England Environment Agency, France IGN, Malta Planning Authority and USGS 3DEP | No key; supported national coverage only, close zoom | Upstream WMS; online-only |
| Global bathymetry | GEBCO global seafloor and terrain relief | No key; global coverage, zoom 1+ | Upstream WMS; online-only |
| Night lights | NASA VIIRS Black Marble 2016 annual composite | No key; global coverage, zoom 1+ | NASA GIBS WMTS; historical, online-only |

Both traffic packs are off by default. See [live traffic](live-traffic.md) for setup, military/commercial classification caveats, position expiry, privacy and provider limits.

### Public transport

Enable **Map layers → Intel packs → Public transport**. Filled markers show reported positions from [OpenOV](https://gtfs.ovapi.nl/), [HSL](https://realtime.hsl.fi/), [MBTA](https://github.com/mbta/gtfs-documentation) or [Entur](https://developer.entur.org/pages-real-time-api/). Outlined markers show positions estimated from realtime timing along a route. [Transitous](https://transitous.org/api/) supplies estimates for non-commercial use. When both sources identify the same trip, its reported position takes priority. Available sources continue to appear if another source is offline.

Reticom has no verified live public transport feed for Malta.

### International NOTAMs

Open **Map layers → Intel packs → International NOTAMs**. Save a [BravoEcho API key](https://bravoecho.ai/docs/getting-a-key) on Command or the team host. One Command key serves all teams it hosts. Approved members can enable the layer and choose categories without entering a key. An approved admin can set up the first key remotely under **Set up BravoEcho on the team host**. Replace or remove it from the host. The host contacts BravoEcho and sends bounded map results over an authenticated Reticulum link. Shared access needs a connection to the original host, which needs internet access. A key saved on a member's own device takes priority there.

**Local activity** shows small-area parachuting, drone, fireworks, model-aircraft and air-display notices. Pins show the activity at a glance, and boundaries appear as thin outlines at closer zoom. **All notices** adds the other categories and draws regional areas without fill. Category choices and the view are saved locally.

Both views show notices active now or starting within one hour. Upcoming pins appear lighter. Tap a notice for its text, UTC timing, schedule and map placement. The map skips notices whose schedules it cannot interpret, including sunrise- or sunset-based windows. Q-line-only locations appear as approximate points. Zoom in when the layer reports a result limit. Check your official briefing for flight planning.

### Shared NASA FIRMS access

Save the [NASA FIRMS MAP_KEY](https://firms.modaps.eosdis.nasa.gov/api/map_key/) on Command or the team host under **Map layers → Intel packs → Satellite heat detections**. An approved admin can enter the first key remotely under **Set up NASA FIRMS on the team host**. Approved members across all teams hosted by that Command installation can enable the layer and choose their own detection window. The host caches downloads by viewport and window. Team devices receive map features over Reticulum, and the key stays on the host.

### TomoSAR

**TomoSAR** is deliberately not presented as a fake raster toggle. Sentinel-1 TomoSAR needs a multi-baseline SLC stack and an on-demand processing workflow; it is not equivalent to displaying one public WMS image. The Map layers panel includes a direct link to the DINAMIS TomoSAR service so an operator can start that analysis separately. A native TomoSAR overlay can be added once a concrete processing/output endpoint is selected, including its authentication, AOI limits, output format and retention rules.

The road layer is also off by default. See [road disruptions](road-disruptions.md) for coverage, stale/ended report handling, and the Waze/international source investigation.

Refresh intervals are cache lifetimes, not promises about when upstream data is updated. A successful empty result means the provider returned no records for that request, **not** that an area is safe or contains no relevant features. Errors must not be presented as successful empty results. A limited result is labeled `truncated`.

### OpenStreetMap

The packs query [Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) for a bounded view of at most 2,500 km², also limited to 2 degrees per axis. Results include no more than 3,000 features. The application must cache and serialize requests and respect the public service's quotas; the service is shared, frequently overloaded, and not an availability guarantee. Public app usage counts across all installations, not separately for each user. A widely distributed deployment needs a self-hosted or appropriately licensed provider instead of assuming free public capacity.

Reticom's local daily download budgets are **500 requests / 200 MB for hiking trails** and **500 requests / 100 MB for military areas and airstrips**, independently counted. These are app safeguards, not provider quotas or guaranteed capacity. Both packs still share one serialized request lane and a persistent 30-second minimum interval; failed downloads also consume a request attempt and back off for 60 seconds. Byte accounting uses completed source responses, so the last response can cross a byte threshold. Counters reset at 00:00 UTC, not when the app restarts. Upgrades attribute the old shared counter using that day's cached downloads and conservatively charge any unattributed remainder to both buckets; upgrading does not erase usage. This is a bounded local-testing approach, not a substitute for operating our own data service for a broadly distributed application. See [Overpass public-service guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html).

Area downloads have a **15% pan margin on each side**, reduced near provider area/span and world-edge limits. Fresh overlapping cache rectangles can collectively cover a view without another provider request; actual union coverage is checked so holes are never treated as downloaded. Cached features are deduplicated by OSM ID, retaining separately clipped hiking-route line fragments. Older overlapping data remains explicitly stale when a refresh fails. The existing 24-hour freshness and 30-day stale window are retained, with a 64 MiB / 128-file disk cache. Truncated results cannot claim full coverage for another view; a dense buffered result is retried without padding on the next permitted request, and an exact-view limited result is reused with its warning. This is reusable local area caching, **not a complete country/world dataset or an unattended daily downloader**.

Hiking queries use [`route=hiking`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dhiking), walking routes, and [`highway=path`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dpath)/footway/track/steps. Access tags and trail difficulty are retained when present. Mapped trails can be private, impassable, or outdated; showing a line does not authorize entry or guarantee a traversable route.

Regional queries use [`landuse=military`](https://wiki.openstreetmap.org/wiki/Tag:landuse%3Dmilitary), military tags, airstrips/runways/aerodromes, [`amenity=police`](https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dpolice), `landuse=police` and [police facility tags](https://wiki.openstreetmap.org/wiki/Key:police). Police features use blue points or outlines, based on the mapped geometry. Multipolygon outlines preserve inner holes. The layer shows public community mapping and has no live activity feed.

For worldwide offline coverage, build a local snapshot from the [Overture Maps base theme](https://docs.overturemaps.org/guides/base/). Install DuckDB in the builder's Python environment and run this from the checkout on Windows.

```powershell
$env:PYTHONPATH = Join-Path $PWD src
python tools/build-military-snapshot.py 2026-11-18.0 (Join-Path $env:LOCALAPPDATA 'Reticom/data/intel-packs/military-world.sqlite3')
```

The builder scans large global Parquet files once and atomically replaces the SQLite snapshot. The app opens it read-only and queries only the visible area. Wide views prioritize larger features and cap response size. Updated snapshots include police station points from [Overture Places](https://docs.overturemaps.org/guides/places/taxonomy-explorer/). Older snapshots use regional OpenStreetMap data for police features at zoom 9 and above. Installations without a snapshot continue using the regional cache. If no regional geometry is available, the overlay can highlight military land use from the existing OpenFreeMap vector basemap, including downloaded tiles. Offline basemaps are downloaded separately.

OSM-derived data remains under the [Open Database License](https://www.openstreetmap.org/copyright), separate from Reticom's software license. Map attribution identifies OpenStreetMap contributors.

### NASA FIRMS

Obtain a [free MAP_KEY](https://firms.modaps.eosdis.nasa.gov/api/map_key) and enter it in the app's Intel-pack credentials. The adapter uses the official [area API](https://firms.modaps.eosdis.nasa.gov/api/area/), passing only the chosen bounds, sensor and 3-day time range. Keys are not included in map features or error messages.

These are **thermal anomalies**, not confirmed fires, explosions or attacks. VIIRS observations have approximately 375 m nominal resolution; cloud cover, satellite overpass timing and sensor geometry limit detection. They must not be used as a sole source for preserving life or property. See [NASA's detection explanation and disclaimer](https://firms.modaps.eosdis.nasa.gov/map/). This initial pack intentionally uses one named satellite product so overlapping sensors do not masquerade as separate incidents.

### GDACS

In **Map layers → Intel packs → Disaster alerts**, choose which disaster types to display: earthquakes, tropical cyclones, floods, droughts, wildfires, volcanic activity and tsunamis. All except droughts are selected initially; selecting none hides the pack's events without making a provider request. Filters are saved on this device, survive restarts and are never sent to the team. Disabling the whole pack preserves the selection.

The adapter combines the official [GDACS app alert feed](https://www.gdacs.org/contentdata/xml/gdacsAPP_Home.geojson) with GDACS's per-type GeoJSON feeds, such as [wildfire areas](https://www.gdacs.org/contentdata/xml/gdacsWF.geojson) and [cyclone areas and tracks](https://www.gdacs.org/contentdata/xml/gdacsTC.geojson). The app selection is not an exhaustive emergency incident database. Tsunami alerts use available app-feed records; the adapter does not assume there is a separate tsunami-area feed.

Where the source provides them, the map shows actual Polygon/MultiPolygon outlines and line tracks alongside distinct event icons. Polygon holes are preserved. An outline can describe reported affected land, a modeled hazard, a forecast or a reference boundary; the source's geometry label and basis are retained to distinguish these meanings. A point is not expanded into an invented impact circle. Alert level, event time and links to the relevant GDACS report are retained; upstream text and geometry remain untrusted input.

For views no wider or taller than 10 degrees, the adapter also requests details for at most four visible events without an aggregate outline, using official HTTPS `contentdata/resources/{type}/{event}/geojson_{event}_{episode}.geojson` resources. Paths are constructed only from validated event types and numeric IDs; upstream links are never fetched directly. It prioritizes higher alerts, then wildfire/flood/cyclone reports and proximity to the view's center. Large overview maps avoid per-event requests; zoom closer for additional individual-event outlines. Missing or failed geometry does **not** mean an event has no affected area or that a location is safe. Partial and limited results remain labeled instead of suppressing usable alert points.

GDACS requests share a 20-second deadline, a 16 MB total download budget and a 6 MB limit per document. Raw GDACS feeds/details are reused for 15 minutes in-process. Persistent view caches use the same 15-minute freshness interval, with explicitly stale data available for up to one day. Cache identity includes the disaster selection and geometry schema version. Detail views require an exact viewport cache match, so a broader, point-only overview cannot prevent loading a local event perimeter. A filter change during a request cannot reintroduce the previous selection.

## Developer notes

`src/retium/intel_sources.py` exposes the catalogue `PACKS` and synchronous `load_pack(pack_id, (west, south, east, north), credentials)`. It returns a GeoJSON FeatureCollection with source metadata, normalized features, `source_bytes`, and `truncated`. `ProviderError` contains a fixed, credential-safe message intended for the UI. HTTP transport uses a fixed endpoint allowlist, rejects redirects, times out after 20 seconds and rejects responses above 8 MB. The calling service owns caching, stale-data policy, credentials and rate limits.

Each feature has a stable ID, title, detail, kind, source link, attribution and reported observation date when the upstream source provides it. Provider text remains untrusted: clients must render it as escaped text. The injection hook `fetch(url, *, data=None, headers=None)` permits deterministic offline adapter tests; fixtures are not exposed as live map data.

Tests cover geographic bounds, request construction, military area holes, incomplete geometry, hiking route gaps, GDACS coordinate normalization, time/confidence/precision fields, response caps, missing/expired credentials, safe URLs and credential non-disclosure.
