# Road closures and disruptions

Enable **Map layers → Intel packs → Road closures & disruptions**. The independent, automatically saved toggle defaults to off and needs no API key. Coverage is currently **the Netherlands** and **North Carolina, US**; other countries and US states are not connected. An uncovered view is explicitly labeled. No report does not mean a road is open.

Red segments are reported full closures, amber segments are restrictions, and pale amber segments are incidents whose full closure status is unconfirmed. Clicking a segment opens its reason, description, validity start/end, source update and feed confirmation times, direction and vehicle restrictions when supplied. Actual source geometry is retained, including disconnected road sections. Point-only Dutch reports remain points labeled “road segment unavailable”; no road shape is invented. Clicking does not change map zoom.

Only currently valid records are included. Future, ended, suspended and cancelled records are excluded. Dutch explicit validity/exception periods are checked; unsupported recurring schedules are excluded. Vehicle-specific closures are restrictions rather than full road closures. WZDx reports declaring all lanes open are excluded. Provider coverage and classifications are incomplete and may lag conditions on the road. This overlay does not create signed team markers, notifications or Reticulum events.

## Filters and navigation

Expand **Filters · report type & impact** in the road layer. Select report types (closures/restrictions, roadworks, accidents, vehicle obstruction, weather/flooding and other incidents) and impacts (full closure, restriction or unconfirmed disruption). Both selections apply together. **Vehicle obstruction is off by default**; other types and all impacts start on. Changes apply immediately and are saved on this device, with no Save button. Empty selections remain empty after restart. Filters control display without discarding cached reports.

Driving navigation requests alternative routes from the existing online OSRM or local Valhalla engine and checks their complete geometry against active full-closure segments **before choosing the route**. The candidate with the fewest closure matches is selected, preserving provider order on ties. If no supplied alternative avoids every matched closure, a visible navigation warning reports that limitation. Distances, times and directions belong to the selected candidate. Missing/stale coverage and failed checks remain visible; they are not described as a clear road.

This check requires the road layer to be enabled, but uses all its full closures regardless of display filters. Offline-only mode uses cached reports exclusively; it cannot trigger a provider download. Normal mode refreshes relevant regional feeds within the existing cache/rate limits. The route-check request stays on the local device; public road providers never receive route geometry. Existing online routing still sends origin/destination to the routing service. Walking and shared fixed paths are not automatically altered based on vehicle-road restrictions.

The comparison looks for aligned road segments within 20 metres and overlapping along the route, rather than treating every crossing as a closure (which could be a bridge). It is a geographic approximation without shared road-edge identifiers; direction, parallel carriageways, bridge levels and vehicle-specific access cannot always be resolved. Point-only reports are not treated as exact blocked segments. Lane restrictions are not assumed full closures. Only currently active reports are checked, not closures scheduled for a future departure or predicted arrival time. Driving recalculations repeat the check; this is not a separate live incident subscription while stationary.

[OSRM alternatives](https://project-osrm.org/docs/v5.24.0/api/#route-service) and [Valhalla alternates](https://valhalla.github.io/valhalla/api/route/api-reference/#directions-options) can return fewer alternatives than requested, or none. This implementation compares their candidates; it does not inject live closure edges into the routing graph or guarantee a detour around every closure. Comparison is bounded to four routes, 20,000 positions each, a 4 MB request and bounded geometry work; larger/uncheckable routes get an explicit unavailable warning.

## Connected official feeds

| Provider | Coverage and reports | Endpoint and access | Refresh |
| --- | --- | --- | --- |
| NDW | Netherlands: closures, lane restrictions, accidents, obstructions, weather conditions and works present in the current feed | Public [DATEX II current picture](https://opendata.ndw.nu/actueel_beeld.xml.gz), gzip XML; [NDW portal documentation](https://docs.ndw.nu/handleidingen/NCIS/WebportaalD2v3/) | At most once per 2 minutes |
| NCDOT | North Carolina: currently active WZDx work zones, including closures and restrictions | Public [WZDx feed](https://www.drivenc.gov/api/wzdx); [official developer documentation](https://www.drivenc.gov/developers/doc) | At most once per 5 minutes |

NCDOT's general incident REST API requires developer access; this implementation uses its separately published public WZDx feed. It does not claim to include all North Carolina accidents or emergency incidents. NDW's [open-data factsheet](https://www.ndw.nu/binaries/ndw/documenten/publicaties/2022/6/10/factsheet-open-data/Factsheet%2BOpen%2BData.pdf) describes its reusable open data. Attribution and source links remain on reports; Reticom's software license does not replace provider terms.

Requests use fixed HTTPS endpoints, reject redirects, have a 20-second deadline and a 16 MB response/decompressed XML limit. Feeds are bounded to 10,000 input records and 250,000 retained positions. A feed exceeding a limit fails without claiming a complete update. Only providers intersecting the map's regional coverage are requested. Providers receive the device IP, but no map bounds, operator identities or team history: these are whole-region public downloads. A world view can request both feeds.

## Cache and expiry

Normalized complete regional snapshots are persisted locally. Pan/zoom reuses them, with no minimum zoom or disappearance when zooming out. A successful complete feed replaces that provider's earlier reports across visited views, removing cleared records. An error retains recent reports with an explicit stale label and faded styling. Concurrent requests share a provider lock and failed attempts respect the same retry interval.

Confirmation age uses the older of local receipt and source publication. Reports become stale after 10 minutes without confirmation (or immediately when refresh fails), and disappear after one hour without confirmation or at their supplied end time, whichever comes first. A five-second client timer also expires reports and closes an expired report's popup when no aircraft or boat layer is enabled. Backend requests and restarts cannot extend an old source publication's lifetime. These intervals are app policy, not a provider guarantee.

## Waze and international investigation

Reviewed September 2026:

| Source | Finding and integration decision |
| --- | --- |
| Waze for Cities | Official retrieval is provided through an approved partner feed and agreed geographic access. See [partner feed guidance](https://support.google.com/waze/partners/answer/13458165?hl=en) and [partner data access](https://support.google.com/waze/partners/answer/10618035?hl=en). The documented [CIFS specification](https://developers.google.com/waze/data-feed/overview) primarily describes sending closures/incidents to Waze; it is not unrestricted permission to retrieve worldwide Waze data. No website scraping or assumed partner credentials are implemented. A future connector needs the user's authorized agreement/feed. |
| US DOT WZDx | The [official specification](https://github.com/usdot-jpo-ode/wzdx) and [feed registry](https://catalog.data.gov/dataset/work-zone-data-exchange-wzdx-feed-registry) provide a practical expansion path by state. Availability and access differ per publisher; the registry's Iowa endpoint returned HTTP 403 during investigation, so it is not enabled. North Carolina's official public endpoint was verified and connected. |
| European national access points | The [European Commission directory](https://transport.ec.europa.eu/transport-themes/smart-mobility/road/its-directive-and-action-plan/national-access-points_en) lists national traffic-data access points. [DATEX II](https://docs.datex2.eu/v3.0/general/index.html) standardizes exchange, but countries still require individual endpoint, access, license, coordinate-profile and freshness verification. NDW is the first connected country; there is no assumed global feed. |

Future team-reported blocked/cleared roads can add a separate signed report lifecycle and provenance alongside this public overlay. That requires explicit clearance/conflict handling and is not included in the current external-feed layer.
