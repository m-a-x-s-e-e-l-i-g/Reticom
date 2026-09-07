# Contour lines / elevation map feature

Find this under **Map layers → Map & terrain → Contour lines**, alongside the base map and hiking controls. This replaces terrain shading with height lines, thicker major contours and labels such as **250 m** following the lines. The saved elevation toggle is preserved. Enabling/disabling the layer does not move, zoom or tilt the map.

Contours appear at zoom 9+. Minor/major intervals in metres are: zoom 9: 200/1000; 11: 100/500; 12: 50/250; 13: 20/100; 14: 10/50; 15+: 5/25. The panel reports the current minor interval. Major contours carry collision-aware metre labels, including negative heights below sea level; zoom 14+ also labels minor lines so flatter areas still have readable heights. Flat areas may have few or no contour crossings; that is not a download error. A 5 m interval does not imply 5 m accuracy.

The DEM source is installed only after the enable setting is acknowledged by the server. This prevents optimistic toggles from sending tile requests while the endpoint still considers terrain disabled. Refresh, or reconnecting to the Internet, recreates the source so previously failed tiles can be retried; it does not move the camera.

The optional elevation feature adds contour lines and estimated terrain heights to the map. It uses real, public **Mapzen Terrain Tiles**, hosted through the AWS Open Data program. No account or API key is required. Tile requests reveal the area being viewed to the public tile host; they do not contain team messages or operator identities. [Dataset and access information](https://registry.opendata.aws/terrain-tiles/).

This is reference terrain, **not live survey data, GPS altitude, a safe route, or an aviation/navigation guarantee**. Heights can contain source errors, seams and missing data. Native source resolution varies by area; increasing zoom does not create more accurate terrain. The data combines sources including SRTM, USGS, EU-DEM and national surveys, with ocean bathymetry at much coarser detail. The provider documents irregular updates and processing artifacts. [Sources, resolution and limitations](https://github.com/tilezen/joerd/blob/master/docs/data-sources.md).

Heights are reported in metres from the source DEM, not converted to the phone's GNSS vertical datum. Do not treat them as directly interchangeable with ellipsoidal GPS altitude: the composite source datums are not identified per point by this endpoint. The on-screen estimate is rounded to a tenth of a metre for presentation, **not a claim of centimetre or decimetre accuracy**.

## Cache and offline behaviour

Viewed elevation tiles are cached locally for later use. Refresh is attempted after 30 days; an older cached tile remains usable if the provider is unavailable. Freshness describes the local download time, not the source survey date. Missing, uncached areas are explicitly unavailable offline. This is an automatically bounded viewing cache, **not a full offline elevation-area download** and not part of the existing basemap download packs.

The cache is limited to 1,500 tiles / 128 MiB of tile payload, with least-recently-used eviction. SQLite metadata adds small storage overhead. No elevation tiles are sent through Reticulum or inserted into signed team intel.

## Implementation

- `ElevationStore(data_dir)` stores an auto-vacuum SQLite cache below `data_dir/elevation-cache`.
- `tile(z, x, y)` returns PNG bytes and content type; `tile_result` also reports `cached`, `stale` and Unix `fetched_at` time.
- The fixed upstream is `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`. No user-defined upstream or redirect is followed. Requests are capped at 512 KiB with a 10-second socket timeout and four simultaneous downloads. A brief failure cooldown avoids repeatedly waiting on an offline connection.
- Tiles are validated as 256 × 256 RGB/RGBA PNGs. Coordinates accept integer XYZ zoom levels 0–14. Web Mercator excludes the polar caps beyond approximately ±85.05°.
- `static/terrain-contours.js` lazily loads the vendored BSD-3-Clause **maplibre-contour 0.1.0**. Its worker decodes Terrarium data, joins neighboring tiles and generates contour vector tiles locally. No external script CDN or contour API is involved. The DEM protocol uses the existing local tile endpoint and disk cache.
- The worker retains at most 64 entries per tile-cache stage. One shared worker serves Command, Field and All Teams. A vector source (zoom 9–15) renders lines and metre labels beneath team overlays and base labels. `overzoom: 1` reduces neighboring-tile downloads; source resolution is capped at DEM zoom 14. No hillshade or 3D terrain layer is installed.

Terrarium stores a height as `red * 256 + green + blue / 256 - 32768`. Raw data are in the PNG colour channels; colour manipulation would corrupt the height. [Provider format specification](https://github.com/tilezen/joerd/blob/master/docs/formats.md).

## Attribution and data licensing

The application's code license does not relicense the terrain data. Show a visible **Terrain: Mapzen / data providers** link pointing to the [provider attribution and licensing page](https://github.com/tilezen/joerd/blob/master/docs/attribution.md) whenever terrain is enabled. This composite has multiple upstream licenses; retain the source acknowledgements when redistributing or exporting terrain. The provider's attribution page contains the required notices for ArcticDEM, Australia, Austria, Canada, Copernicus EU-DEM, NOAA ETOPO1, Mexico INEGI, New Zealand LINZ, Norway Kartverket, the UK Environment Agency and USGS 3DEP/GMTED/SRTM.

## Verification

Run `python -m pytest tests/test_elevation.py`. Tests cover valid decoding, coordinate bounds, corrupt/oversized/no-data PNGs, cache reuse and refresh, stale offline fallback, download cooldown, concurrent deduplication and bounded LRU storage. These tests use deterministic image fixtures; live source verification is recorded separately in development output and is not substituted with seeded team intel.

Run `node --test tests/terrain-contours.test.mjs tests/intel-packs.test.mjs` for contour generation from known Terrarium pixels, below-sea-level/zero-height contours, zoom intervals, metre labels, layer ordering, unchanged camera, source refresh and disabled-during-initialization races.

Browser verification on 2026-09-07 used live tiles at 46.5763 N, 8.4151 E (Swiss Alps): 187 rendered contour features and 21 major labels at zoom 13.5. Satellite style replacement and contour toggles kept the exact center and zoom. A second check near 51.5786 N, 4.7785 E showed actual **5 m** labels at zoom 15 on flatter Dutch terrain. These checks verify rendering, not survey accuracy.

Live source checked on 2026-09-06: the public tile at zoom 14 for **35.3606° N, 138.7274° E** returned successfully and decoded to **3,753.5 m** at the sampled cell. A second lookup returned the same sample from the disk cache. This verifies provider access and decoding, not survey accuracy at that coordinate.
