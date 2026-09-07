# Offline walking and driving

Under **User settings → Offline navigation → Download regions**, search for a
region and download its walking-and-driving graph. Alternatively, expand
**Import a region file instead** to import a `.retiroute` pack.
Then select a waypoint/person and choose a walking or driving route as usual.
An **OFFLINE** route label means the graph on this device calculated the route.
Recalculation uses the same local engine when your position changes.

- **Offline first** uses local data whenever available. It calls the existing
  online service only if no region covers both endpoints, or this build has no
  native engine. A local routing failure does not silently fall back online.
- **Offline only** never submits route coordinates to an Internet route service.
- Navigation calculation does not require a team host, Reticulum, or Internet.
  Sharing a calculated route still requires an eventual team connection.
- Map images and routing data are separate downloads. Save map images in the
  existing Offline maps section. Satellite imagery still requires Internet.
- Each route must remain inside one pack's bounds; cross-pack routes are not yet
  supported. A regional graph may omit a better detour beyond its boundary.
- No live traffic, current closures, or guarantee of access/safety. OSM paths and
  restrictions depend on the extract's completeness and age.

**Initial catalogue:** Noord-Brabant (46 MB download / 123 MB installed), Zeeland
(11 / 28 MB), and Limburg (23 / 59 MB), built from real Geofabrik OSM extracts
dated 2026-09-06. Sizes use binary MB. This is starter coverage, not a worldwide
catalogue. Search is local; the list displays source date, download and storage
sizes, progress, cancellation, retry, installed status and available updates.

**Publication status:** these three packs have been built and tested locally,
but their public GitHub data releases have not been published. Normal builds
show **PENDING RELEASE** until a published catalogue is available. The local
development server can exercise the prepared files using the explicit override
documented below; this does not make downloads available on another device.

**Not yet shipped:** automatic graph updates, address search, spoken turn-by-turn
guidance, or offline satellite maps.
This is local route calculation integrated into Reticom's existing guidance UI,
not a replacement for a fully featured turn-by-turn navigation application.
Only import packs built by a trusted source; hashes detect corruption, not who
published a pack. No example/test region is installed into a user's map.

## Catalogue downloads and updates

The built-in list works without Internet; **Refresh** retrieves an updated list
from Reticom's fixed GitHub release endpoint. Data packs live in separate dated
releases, so a phone does not require the Command laptop or a new APK for every
region update. Catalogue downloads allow only this repository's HTTPS release
URLs and GitHub's release-asset redirect host. Region files are checked against
the catalogue's SHA-256 and exact byte size, then validated and installed
atomically. A failed download/update preserves the existing region.

One download runs at a time. Cancel during download or verification; installation
finishes atomically. Interrupted downloads offer **Retry** (restart, not byte-range
resume). Existing graphs remain usable offline. Updates are opt-in; **Refresh**
does not download new graphs automatically. Native routing and installs are
serialized, so a route request may briefly wait while a pack is installed.

## Platforms

Android packages the arm64 Valhalla native engine from
`io.github.rallista:valhalla-mobile:0.6.3` on Maven Central. Gradle extracts only its
native library; a small Java JNI adapter calls it from Chaquopy. HTTP callbacks
are explicitly null. No Kotlin UI/model dependencies or remote tile loader.

Desktop source runs can enable the native engine with **Python 3.12 or newer**:

```powershell
python -m pip install -e ".[offline-routing]"
```

This installs `pyvalhalla==3.8.3`. Older Python builds, the existing Windows
standalone release, and the existing Python 3.11 Docker image do not yet bundle
this engine; settings report it unavailable instead of claiming offline support.

## Building a region

The repeatable catalogue builder downloads dated Geofabrik extracts and builds
compatible walking/driving packs; run on Linux/WSL:

```sh
PYTHONPATH=src python tools/build-routing-catalogue.py \
  --regions noord-brabant zeeland limburg --date 2026-09-06 \
  --tag routing-data-20260907 --output runtime/routing-catalogue \
  --cache runtime/routing-catalogue-build
```

The output contains the packs and `catalogue.json` with actual sizes and hashes.
For local testing, set `RETICOM_ROUTING_CATALOGUE_DIR` to that absolute output
directory before starting Reticom. The UI clearly identifies development data.
Never accept this directory from a browser request.

`.github/workflows/routing-data.yml` provides the same manual build on GitHub
Actions. Publishing is disabled by default. If deliberately enabled, it creates
a separate dated data release and updates `routing-catalogue-v1/catalogue.json`;
neither is marked as the latest application release. Existing dated releases
are not overwritten. Only mark a catalogue `published: true` after its region
assets are uploaded; bundled catalogues must reference real published assets.
Routing data retains OSM's ODbL license, separate from Reticom's application license.

Use a **current regional OSM PBF** from a provider such as
[Geofabrik](https://download.geofabrik.de/). Download it on a laptop, then build
the routing graph once. Do not build planet-sized data on a phone. Include a
generous buffer around the intended travel area; both endpoints and the complete
route must fit the manifest bounds and the actual source extract.

The builder is verified on Linux/WSL, Python 3.12, pyvalhalla 3.8.3. The Windows
binding calculates routes, but its graph-building executable currently needs
additional native DLLs on this machine, so use WSL for preparing data.

```sh
python -m pip install -e '.[offline-routing]'
python tools/build-routing-pack.py region.osm.pbf region.retiroute \
  --id my-region --name 'My region' \
  --bounds WEST SOUTH EAST NORTH \
  --source 'Provider URL; OSM extract date YYYY-MM-DD'
```

Copy the resulting `.retiroute` file to the phone (Downloads is convenient) and
import it in settings. The system file picker does not require broad storage
permission. You can distribute this data file separately from the APK, subject
to the OSM data license. Routing stays entirely on the recipient's device after
import; the laptop does not need to remain online.

## Pack format and safeguards

A ZIP contains `manifest.json` and `tiles/{level}/.../*.gph` only. The manifest
identifies format `reticom-routing-v1`, builder version `3.8.3`, both profiles,
coverage bounds, source, build date, and a SHA-256 for every graph tile.
Executable configuration, arbitrary paths, duplicate files, and symlinks are
rejected. Device-local configuration is generated by Reticom, never imported.

Limits: 512 MB compressed per import, 2 GB expanded per pack, 4 GB total routing
storage. Validation happens in staging; a failed import leaves an existing pack
intact. Imports and native route calls are serialized, including cancellations.
An engine is retained for repeated routes and closed before pack replacement or
removal. Removing a routing pack does not delete basemaps, messages, or shared
route geometry.

The API is device-scoped and exists independently of the selected Command team:

- `GET /api/offline-routing` — capability and installed regions.
- `GET /api/offline-routing/catalogue` — saved/built-in catalogue and progress.
- `POST /api/offline-routing/catalogue/refresh` — refresh the public list.
- `POST /api/offline-routing/catalogue/{id}/download` — download a known region.
- `POST /api/offline-routing/catalogue/cancel` — cancel the active download.
- `POST /api/offline-routing/import` — binary pack, `application/octet-stream`.
- `DELETE /api/offline-routing/{id}` — remove a region from this device.
- `POST /api/offline-routing/route` — JSON `origin`, `target` as `[lon,lat]`, and
  `profile`: `walking` or `driving`. Returns OSRM-format GeoJSON and steps plus
  `source: offline`, pack name and build timestamp.

## Verification

Unit tests cover validation, corruption, replacement, bounds, profiles, API
availability without a team, and preventing unwanted online fallback.
Catalogue tests additionally cover untrusted URLs, partial/corrupt downloads,
cancellation, unpublished assets, old-cache precedence, replacement quotas and
restart/retry state. The local UI installed the actual Noord-Brabant pack and
the native Windows engine calculated both walking and driving routes in Breda.

For a real-engine test, build a pack using the historical Liechtenstein OSM
fixture in Valhalla's `3.8.3/test/data/liechtenstein-latest.osm.pbf` with bounds
`9.47 47.045 9.64 47.28`. **This is test data, not a current travel map.** Then:

```sh
python tools/check-offline-routing.py liechtenstein-test.retiroute
# Stronger proof on Linux: run the same command in an isolated network namespace.
sudo unshare --net /absolute/path/to/python tools/check-offline-routing.py liechtenstein-test.retiroute
```

Verified locally with networking disabled: walking/driving, two start positions,
route geometry/instructions, and refusal outside coverage. Windows returned the
same results. Android APK compilation passed; physical-device routing still
needs verification when the phone is available through ADB.

Engine attribution: [Valhalla / Valhalla Mobile MIT notices](../src/retium/static/vendor/valhalla/LICENSE.txt).
OpenStreetMap data retains its [ODbL attribution](https://www.openstreetmap.org/copyright).
