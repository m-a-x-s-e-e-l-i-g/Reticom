// Public map context is device-local: never insert it into the signed team feed.
import {ROAD_COLOR} from "./outdoor-map.js?v=20260907-5";
import {GDACS_TYPES, GDACS_COLOR, GDACS_ICON, gdacsCode, gdacsIconSvg, gdacsDisplayData, normalizeGdacsTypes, installGdacsIcons} from "./gdacs-markers.js?v=20260907-1";
import {prepareContourTiles, contourLayers, contourInterval, CONTOUR_MIN_ZOOM, CONTOUR_MAX_ZOOM} from "./terrain-contours.js?v=20260907-3";
import {lidarLayers, lidarModeLabel, lidarModeForProvider, lidarProviderForBounds, lidarSource, lidarTileUrl, normalizeAhnMode, AHN_MODES} from "./ahn-layer.js?v=20261125-1";
import {environmentLayers, environmentSource, environmentTileUrl, ENVIRONMENT_RASTER_IDS, ENVIRONMENT_RASTERS, ENVIRONMENT_MIN_ZOOM} from "./environment-layers.js?v=20261125-1";
import {TRAFFIC_IDS, TRAFFIC_TYPES, normalizeTrafficFilters, trafficDisplayData, installTrafficIcons, trafficLayers} from "./traffic-map.js?v=20261125-1";
import {TrafficMotion, createTrafficAnimator} from "./traffic-motion.js?v=20260909-3";
import {TrafficTracks, trafficKey, showTrafficTrack, showTrafficEstimate, clearTrafficTrack} from "./traffic-tracks.js?v=20261125-2";
import {retainTraffic} from "./traffic-cache.js?v=20260909-3";
import {retainIntel} from "./intel-cache.js?v=20260909-4";
import {roadDisplayData, retainRoads, roadLayers, ROAD_IMPACTS, ROAD_TYPES, normalizeRoadFilters, filterRoadReports} from "./road-disruptions.js?v=20260909-7";
const EMPTY = () => ({type: "FeatureCollection", features: []});
// Trails are always-on basemap data, not a separately fetched/toggled intel pack.
const IDS = ["military", "roads", "firms", "gdacs", "notams", "transit", "elevation", "ahn", ...ENVIRONMENT_RASTER_IDS, ...TRAFFIC_IDS];
const CLIENT_RASTER_IDS = new Set(["elevation", "ahn", ...ENVIRONMENT_RASTER_IDS]);
const COLORS = {trails: "#8f9c77", military: "#ad9774", firms: "#c49367", gdacs: "#b6a578", notams: "#c4a3ff"};
const PREFIX = "public-intel-";
const ACCESS = {
  firms_key: {pack: "firms", title: "NASA FIRMS map key", url: "https://firms.modaps.eosdis.nasa.gov/api/map_key/", link: "Get a NASA FIRMS map key"},
  aisstream_key: {pack: "vessels", title: "AISStream API key", url: "https://aisstream.io/", link: "Create an AISStream key / check access terms"},
  aishub_key: {pack: "vessels", title: "AISHub contributor key", url: "https://www.aishub.net/join-us", link: "AISHub contributor access"},
  bravoecho_key: {pack: "notams", title: "BravoEcho API key", url: "https://bravoecho.ai/docs/getting-a-key", link: "Get a BravoEcho API key"},
  host_bravoecho_key: {pack: "notams", title: "Set up BravoEcho on the team host", url: "https://bravoecho.ai/docs/getting-a-key", link: "Get a BravoEcho API key"},
  host_firms_key: {pack: "firms", title: "Set up NASA FIRMS on the team host", url: "https://firms.modaps.eosdis.nasa.gov/api/map_key/", link: "Get a NASA FIRMS map key"},
};
const SHARED_LAYERS = ["notams", "firms"];
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
const sourceId = id => `${PREFIX}${id}`;
const TRAFFIC_SOURCES = new Set(TRAFFIC_IDS.map(sourceId));
const isEnvironmentRaster = id => ENVIRONMENT_RASTER_IDS.includes(id);
const layerIds = id => (TRAFFIC_IDS.includes(id) ? ["points", "icons", "labels"] : id === "transit" ? ["points", "labels"] : id === "notams" ? ["lines", "local-lines", "points"] : id === "elevation" ? ["lines", "labels", "minor-labels"] : id === "ahn" || ENVIRONMENT_RASTER_IDS.includes(id) ? ["raster"] : id === "gdacs" ? ["areas", "lines", "tracks", "points", "icons", "labels"] : id === "military" ? ["areas", "lines", "points", "police-stations", "labels"] : id === "trails" ? ["areas", "lines", "points", "labels", "route-labels"] : ["areas", "lines", "points", "labels"]).map(kind => `${PREFIX}${id}-${kind}`);
const MILITARY_BASEMAP = `${PREFIX}military-basemap`;
const POLICE_COLOR = "#8db9d0";
const POLICE_ICON = "public-police-shield";
const POLICE_STATION = ["==", ["get", "kind"], "police_station"];
const POLICE_LAYOUT = {"icon-image": POLICE_ICON, "icon-size": .85, "icon-allow-overlap": true, "icon-ignore-placement": true};
const NOTAM_ICONS = {
  parachuting: "M4 11a8 8 0 0 1 16 0ZM4 11l8 8 8-8M8 11l4 8 4-8",
  drones: "M9 12h6m-3-3v6M6 6l4 4m8-4-4 4m-8 8 4-4m8 4-4-4M4 5h4M16 5h4M4 19h4m8 0h4",
  fireworks: "M12 4v4m0 8v4M4 12h4m8 0h4M6 6l3 3m6 6 3 3M18 6l-3 3m-6 6-3 3",
  model: "M4 13h16M12 4v16m-4-2 4-2 4 2m-9-5 5-4 5 4",
  airshow: "M4 17l8-12 8 12-8-3ZM12 14v5",
  other: "M12 6v7m0 4h.01",
};
const NOTAM_ACTIVITY_LABELS = {parachuting: "Parachuting", drones: "Drone activity", fireworks: "Fireworks", model: "Model aircraft", airshow: "Air display"};

function installNotamIcons(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined") return;
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 48;
  const ctx = canvas.getContext("2d"); if (!ctx) return;
  ctx.scale(2, 2);
  for (const [activity, path] of Object.entries(NOTAM_ICONS)) {
    const key = `public-notam-${activity}`;
    if (map.hasImage(key)) continue;
    ctx.clearRect(0, 0, 24, 24);
    ctx.beginPath(); ctx.arc(12, 12, 11, 0, Math.PI * 2);
    ctx.fillStyle = "#191326"; ctx.fill();
    ctx.strokeStyle = COLORS.notams; ctx.lineWidth = 1.3; ctx.stroke();
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.stroke(new Path2D(path));
    map.addImage(key, ctx.getImageData(0, 0, 48, 48), {pixelRatio: 2});
  }
}
const MILITARY_FALLBACK_LAYERS = [
  {id: MILITARY_BASEMAP, type: "fill", "source-layer": "landuse", filter: ["==", ["get", "class"], "military"],
    paint: {"fill-color": COLORS.military, "fill-opacity": .24, "fill-outline-color": COLORS.military}},
  {id: `${MILITARY_BASEMAP}-airfield`, type: "line", "source-layer": "aeroway",
    filter: ["in", ["get", "class"], ["literal", ["aerodrome", "airstrip", "runway"]]],
    paint: {"line-color": COLORS.military, "line-width": 2, "line-dasharray": [3, 2]}},
  {id: `${MILITARY_BASEMAP}-police`, type: "symbol", "source-layer": "poi",
    filter: ["==", ["coalesce", ["get", "subclass"], ["get", "class"]], "police"],
    layout: POLICE_LAYOUT},
];

function installPoliceIcon(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined" || map.hasImage(POLICE_ICON)) return;
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 48;
  const ctx = canvas.getContext("2d"); if (!ctx) return;
  ctx.scale(2, 2);
  const shield = new Path2D("M12 2 20 5V11C20 16 17 19 12 22 7 19 4 16 4 11V5Z");
  ctx.lineWidth = 1.7;
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#10171b";
  ctx.fillStyle = POLICE_COLOR;
  ctx.fill(shield);
  ctx.stroke(shield);
  ctx.fillStyle = "#10171b";
  ctx.fill(new Path2D("M12 6 13.5 9.6 17.4 10 14.5 12.6 15.4 16.5 12 14.4 8.6 16.5 9.5 12.6 6.6 10 10.5 9.6Z"));
  map.addImage(POLICE_ICON, ctx.getImageData(0, 0, 48, 48), {pixelRatio: 2});
}
const safeLink = value => {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : ""; } catch { return ""; }
};
const safeAircraftThumbnail = value => /^\/api\/intel-packs\/live\/flights\/[0-9a-f]{6}\/thumbnail$/.test(String(value || "")) ? value : "";
const safeVesselThumbnail = value => /^\/api\/intel-packs\/live\/vessels\/[0-9]{9}\/thumbnail\/[1-9][0-9]{0,3}$/.test(String(value || "")) ? value : "";
const isTeamLayer = layer => /^(verified-|field-|all-team|live-pointing|waypoint-|map-draw|navigation-|shared-navigation)/.test(layer?.id || "") || /^(verified-|field-|all-teams$|shared-navigation)/.test(layer?.source || "");

export function intelViewportBoxes(bounds) {
  const west = Number(bounds?.getWest?.() ?? bounds?.west);
  const east = Number(bounds?.getEast?.() ?? bounds?.east);
  const south = Math.max(-85, Number(bounds?.getSouth?.() ?? bounds?.south));
  const north = Math.min(85, Number(bounds?.getNorth?.() ?? bounds?.north));
  if (![west, east, south, north].every(Number.isFinite) || south >= north || east === west) return [];
  let span = east - west;
  if (span < 0) span = ((span % 360) + 360) % 360;
  if (span >= 360) return [{west: -180, south, east: 180, north}];
  const wrappedWest = ((west + 180) % 360 + 360) % 360 - 180;
  const wrappedEast = wrappedWest + span;
  if (wrappedEast <= 180) return [{west: wrappedWest, south, east: wrappedEast, north}];
  return [{west: wrappedWest, south, east: 180, north}, {west: -180, south, east: wrappedEast - 360, north}];
}

export function intelPackUrls(id, bounds, zoom) {
  if (!IDS.includes(id) || CLIENT_RASTER_IDS.has(id) || !Number.isFinite(Number(zoom))) return [];
  return intelViewportBoxes(bounds).map(box => {
    const query = new URLSearchParams(Object.entries(box).map(([key, value]) => [key, value.toFixed(5)]));
    query.set("zoom", Number(zoom).toFixed(2));
    return `/api/intel-packs/${TRAFFIC_IDS.includes(id) ? "live/" : ""}${id}?${query}`;
  });
}

export function mergeIntelResults(results) {
  if (!results.length) return {...EMPTY(), status: "error", error: "The map view is unavailable."};
  const seen = new Set(), features = [];
  for (const result of results) for (const feature of result.features || []) {
    if (feature?.type !== "Feature" || !feature.geometry) continue;
    const key = feature.id ?? feature.properties?.id ?? JSON.stringify([feature.geometry, feature.properties]);
    if (!seen.has(key)) { seen.add(key); features.push(feature); }
  }
  const warning = results.find(result => ["error", "needs_key", "zoom_in", "stale", "disabled", "waiting"].includes(result.status));
  const deferred = results.find(result => result.status === "deferred");
  const timestamps = results.map(result => result.fetched_at).filter(Boolean);
  return {...results[0], ...EMPTY(), features,
    status: warning ? (features.length ? "stale" : warning.status) : deferred ? "deferred" : results.every(result => result.status === "uncovered") ? "uncovered" : results.every(result => result.status === "cached") ? "cached" : "fresh",
    ...(results.some(result => result.replaced_sources) ? {replaced_sources: [...new Set(results.flatMap(result => result.replaced_sources || []))]} : {}),
    ...(results.some(result => result.waiting_for_host || result.waiting_for_notams) ? {waiting_for_host: true} : {}),
    ...(results.some(result => result.unresolved_schedules) ? {unresolved_schedules: true} : {}),
    capped: results.some(result => result.capped), truncated: results.some(result => result.truncated),
    ...(results.some(result => result.coverage_complete !== undefined) ? {coverage_complete: results.every(result => result.coverage_complete !== false)} : {}),
    fetched_at: timestamps.length ? timestamps.sort((a, b) => new Date(Number(a) * 1000 || a) - new Date(Number(b) * 1000 || b))[0] : undefined,
    ...(warning ? {error: warning.error, note: warning.note || (results.length > 1 ? "Some of this view could not be refreshed." : undefined)} : {}),
    ...(!warning && deferred ? {note: deferred.note, coverage_complete: false} : {}),
  };
}

function stamp(value, withYear = false) {
  if (!value) return "";
  const date = new Date(typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], {...(withYear ? {year: "numeric"} : {}), month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
}

export function intelStatusText(pack, result) {
  if (!pack.enabled) return "Off";
  if (pack.configured === false || result?.status === "needs_key") return "Source access needed below";
  if (!result) return pack.id === "elevation" ? "Contour lines · heights in metres" : pack.id === "ahn" ? "National LiDAR · source chosen by map area" : ENVIRONMENT_RASTER_IDS.includes(pack.id) ? `${ENVIRONMENT_RASTERS[pack.id].title} · online only` : "Waiting for a map view";
  if (result.status === "loading") return "Loading this map view…";
  if (result.status === "deferred") return result.note || "Waiting to download coverage";
  if (result.status === "zoom_in") return result.note || `Zoom in to level ${pack.min_zoom || 10} to load this pack`;
  if (result.status === "error") return result.error || "Source unavailable · try again later";
  if (result.status === "disabled") return "Off";
  if (pack.id === "roads") return `${result.features?.length || 0} road reports${result.status === "stale" ? " · stale" : ""}`;
  if (TRAFFIC_IDS.includes(pack.id)) {
    if (result.status === "waiting") return result.note || "Waiting for live reports…";
    const count = result.features?.length || 0;
    return `${count} ${pack.id === "flights" ? "aircraft" : "vessels"} shown${result.status === "stale" ? " · stale" : ""}${result.capped ? " · display cap" : ""}${result.total_received > count ? ` / ${result.total_received} received` : ""}`;
  }
  if (pack.id === "gdacs" && result.gdacs_types?.length === 0) return "No disaster types selected";
  if (pack.id === "elevation") return result.note || "Contour lines · heights in metres";
  if (pack.id === "ahn") return result.note || "National LiDAR · source chosen by map area";
  if (ENVIRONMENT_RASTER_IDS.includes(pack.id)) return result.note || `${ENVIRONMENT_RASTERS[pack.id].title} · online only`;
  const count = result.features?.length || 0;
  const amount = count ? `${count.toLocaleString()} public feature${count === 1 ? "" : "s"}` : result.coverage_complete === false ? "No saved features · incomplete coverage" : "No public features in this view";
  const state = result.status === "stale" ? "stale cache" : result.status === "cached" ? "cached" : "fetched";
  return `${amount} · ${state}${stamp(result.fetched_at) ? ` ${stamp(result.fetched_at)}` : ""}${count && result.coverage_complete === false ? " · partial coverage" : ""}${result.capped || result.truncated ? " · result limit reached" : ""}`;
}

// The layer picker is configuration, not a live telemetry dashboard. Routine
// refreshes must not insert/remove lines as counts and cache timestamps change.
export function intelLayerWarning(pack, result) {
  if (!pack.enabled) return "";
  const hostAccess = pack.host_access || pack.host_notams;
  if (hostAccess && result?.status === "needs_key") return `The team host needs a ${pack.source} key`;
  if (hostAccess && result?.status === "waiting") return "Waiting for data from the team host";
  if (hostAccess && result?.truncated) return "Team host result limit reached. Zoom in for more data.";
  if (pack.configured === false || result?.status === "needs_key") return "Source access needed below";
  if (result?.status === "error") return result.error || "Source unavailable · try again later";
  if (pack.id === "roads" && result?.status === "uncovered") return result.note;
  if (pack.id === "transit" && ["zoom_in", "uncovered"].includes(result?.status)) return result.note;
  if (pack.id === "transit" && result?.truncated) return "Public transport result limit reached. Zoom in to see more.";
  if (pack.id === "ahn" && result?.status === "uncovered") return result.note;
  if (result?.status === "stale") return result.error || "Source unavailable · showing saved data";
  if ((hostAccess || pack.id === "notams") && result?.status === "zoom_in") return "Zoom in to load data for this area";
  if (pack.id === "notams" && result?.truncated) return "NOTAM result limit reached. Zoom in for more notices.";
  if (pack.id === "notams" && result?.unresolved_schedules) return "Some notices have unverified timing.";
  if (result?.status === "deferred") return "";
  if (result?.coverage_complete === false) return "Incomplete coverage · some areas are not available";
  return "";
}

export function intelNoteText(pack, result) {
  if (!pack.enabled || result?.status === "zoom_in") return "";
  const status = intelStatusText(pack, result).trim();
  return [...new Set([result?.note, result?.status === "stale" ? result.error : ""]
    .filter(value => typeof value === "string").map(value => value.trim())
    .filter(value => value && value !== status))].join(" ");
}

export function intelFeatureHtml(feature, pack, result) {
  const props = feature?.properties || {};
  const source = safeLink(props.source_url || pack.source_url);
  const title = props.title || props.name || pack.title || "Public map feature";
  const detail = props.detail || props.description || "";
  const observed = stamp(props.observed_at || props.date);
  if (pack.id === "transit") {
    const position = props.estimated ? "Estimated from realtime trip updates" : props.feed_timestamp ? "Reported position · feed timestamp" : "Reported vehicle position";
    return `<article class="intel-feature-popup"><small>${escape(props.mode_label)} · ${escape(props.source)}</small><strong>${escape(title)}</strong>
      <p>${position}${observed ? ` · ${escape(observed)}` : ""}</p>${props.vehicle ? `<p>Vehicle ${escape(props.vehicle)}</p>` : ""}
      ${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.source)} ↗</a>` : ""}</article>`;
  }
  if (pack.id === "notams") {
    const utc = value => {
      const date = new Date(value || "");
      return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 16).replace("T", " ") + " UTC" : "";
    };
    const heading = [props.notice_number, title !== props.notice_number ? title : ""].filter(Boolean).join(" · ");
    const end = props.notam_end ? utc(props.notam_end * 1000) : utc(props.valid_to);
    const fields = [["Location", props.location], ["Starts", utc(props.notam_start ? props.notam_start * 1000 : props.valid_from)], ["Ends", end ? [end, props.estimated && !props.notam_end ? "estimated" : ""].filter(Boolean).join(" · ") : "No end reported"],
      ["Schedule", props.schedule], ["Map placement", props.geometry_note]];
    const phase = props.notam_phase === "upcoming" ? "Starts within an hour" : "Active now";
    return `<article class="intel-feature-popup"><small>${phase} · ${escape(NOTAM_ACTIVITY_LABELS[props.local_activity] || props.category_label || "NOTAM")}</small><strong>${escape(heading)}</strong>
      <dl>${fields.filter(([, value]) => value).map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("")}</dl>
      <p class="intel-notam-text">${escape(props.notice_text)}</p>
      <p class="intel-feature-caution">${result?.status === "stale" ? "Cached copy. " : ""}Check your official briefing for flight planning.</p>
      ${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(pack.attribution || pack.source)} ↗</a>` : ""}</article>`;
  }
  if (pack.id === "roads") {
    const fields = [["Impact",ROAD_IMPACTS[props.road_impact] || "Unknown"],["Reason",props.reason],
      ["Starts",stamp(props.starts_at, true)],["Ends",stamp(props.ends_at, true) || "Not supplied"],
      ["Last update",stamp(props.updated_at, true) || "Not supplied"],["Feed confirmed",stamp(props.confirmed_at, true)],
      ["Direction",props.direction],["Restrictions",props.restrictions],["Authority",props.authority],["Location",props.geometry_note]];
    return `<article class="intel-feature-popup"><small>PUBLIC ROAD REPORT · ${escape(props.source)}</small><strong>${escape(title)}</strong><p>${escape(props.stale ? "STALE · current status unconfirmed" : ROAD_IMPACTS[props.road_impact] || "Disruption reported")}</p><p>${escape(detail)}</p><dl>${fields.filter(([,v])=>v).map(([k,v])=>`<div><dt>${escape(k)}</dt><dd>${escape(v)}</dd></div>`).join("")}</dl><p class="intel-feature-caution">Coverage is incomplete. No report does not mean a road is open. Vehicle and direction restrictions may apply.</p>${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.attribution || pack.attribution)} ↗</a>` : ""}</article>`;
  }
  if (TRAFFIC_IDS.includes(pack.id)) {
    const altitude = props.altitude_m == null ? props.altitude_reference : `${props.altitude_m} m`;
    const speed = props.speed_knots == null ? "Unknown speed" : `${Number(props.speed_knots).toFixed(0)} kn`;
    const seconds = Math.max(0, Math.floor(Date.now()/1000 - Number(props.position_time)));
    const age = Number.isFinite(seconds) ? `${props.stale ? "Last seen " : ""}${seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`} ${props.stale ? "ago" : "old"}` : "Time unknown";
    const type = pack.id === "flights" && props.aircraft_model
      ? [props.aircraft_manufacturer, props.aircraft_model].filter(Boolean).join(" · ")
      : props.type_code || props.ship_type_code || TRAFFIC_TYPES[pack.id][props.traffic_type] || "Type unknown";
    const thumbnail = pack.id === "flights" ? safeAircraftThumbnail(props.aircraft_photo_url) : "";
    const fields = [["Position report", observed], ["Age", age], ["Map position", props.motion_status || "Reported position"],
      ["Prediction", props.prediction_seconds > 0 ? `${Math.round(props.prediction_seconds)} seconds beyond report` : null],
      ["Category", TRAFFIC_TYPES[pack.id][props.traffic_type] || "Unknown"], [pack.id === "flights" ? "ICAO type" : "Ship type code", props.aircraft_type_code || props.type_code || props.ship_type_code || "Unknown"],
      [pack.id === "flights" ? "ICAO address" : "MMSI", props.identity], ["Registration", props.aircraft_registration || props.registration], ["Callsign", props.callsign],
      ["Speed", props.speed_knots == null ? "Unknown" : `${Number(props.speed_knots).toFixed(1)} kn`], ["Direction", props.heading == null ? "Unknown" : `${Math.round(props.heading)}°`],
      ...(pack.id === "flights" ? [["Altitude", props.altitude_m == null ? props.altitude_reference : `${props.altitude_m} m · barometric`], ["Registered owner", props.aircraft_owner]] : [["Reported length", props.length_m ? `${props.length_m} m` : null], ["Destination", props.destination]])];
    if (pack.id === "flights") {
      const photoSource = safeLink(props.aircraft_photo_source);
      const registration = props.aircraft_registration || props.registration;
      const heading = [title, registration && registration !== title ? registration : null].filter(Boolean).join(" · ");
      const telemetry = [altitude, props.speed_knots != null ? speed : null, age !== "Time unknown" ? age : null].filter(Boolean).join(" · ");
      const position = props.motion_status === "Reported position" ? "" : props.motion_status;
      const identity = String(props.identity || "").toLowerCase();
      const traceDay = new Date(Number(props.position_time) * 1000);
      const historyUrl = /^[0-9a-f]{6}$/.test(identity) && Number.isFinite(traceDay.getTime())
        ? `https://adsb.lol/?icao=${identity}&showTrace=${traceDay.toISOString().slice(0, 10)}` : "";
      const extra = [["Aircraft", type !== "Type unknown" ? type : null], ...fields.filter(([label]) =>
        !["Age", "Map position", "Registration", "Callsign", "Speed", "Altitude"].includes(label))];
      const rows = extra.filter(([, value]) => value != null && value !== "" && value !== "Unknown")
        .map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("");
      const links = [source && `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.attribution || pack.attribution || pack.source)} ↗</a>`,
        historyUrl && `<a href="${escape(historyUrl)}" target="_blank" rel="noopener noreferrer">ADSB.lol history ↗</a>`,
        photoSource && `<a href="${escape(photoSource)}" target="_blank" rel="noopener noreferrer">Photo source ↗</a>`].filter(Boolean).join("");
      return `<article class="intel-feature-popup intel-traffic-card"><strong>${escape(heading)}</strong>
        ${telemetry ? `<p>${escape(telemetry)}</p>` : ""}${position ? `<small>${escape(position)}</small>` : ""}
        ${props.track_label ? `<small>${escape(props.track_label)}</small>` : ""}
        <details class="intel-traffic-details"><summary>Flight details</summary><dl>${rows}</dl>
          ${thumbnail ? `<img class="intel-aircraft-photo" src="${escape(thumbnail)}" alt="Public photo of ${escape(type !== "Type unknown" ? type : title)}" loading="lazy">` : ""}
          ${props.classification_note ? `<p>${escape(props.classification_note)}</p>` : ""}
          <p class="intel-feature-caution">Public ADS-B data. Not a verified team report or a navigation service.</p>
          ${props.aircraft_model ? `<small>Aircraft details · ADSBDB</small>` : ""}<div class="intel-flight-links">${links}</div>
        </details></article>`;
    }
    const summary = [TRAFFIC_TYPES.vessels[props.traffic_type] || type, props.speed_knots != null ? speed : null, age].filter(Boolean).join(" · ");
    const vesselThumbnail = safeVesselThumbnail(props.vessel_photo_url);
    const vesselPhotoSource = safeLink(props.vessel_photo_source);
    const photo = vesselThumbnail && props.vessel_photo_credit ? `<figure class="intel-vessel-photo"><img src="${escape(vesselThumbnail)}" alt="Public photo of ${escape(title)}" loading="lazy"><figcaption>${vesselPhotoSource ? `<a href="${escape(vesselPhotoSource)}" target="_blank" rel="noopener noreferrer">${escape(props.vessel_photo_credit)} ↗</a>` : escape(props.vessel_photo_credit)}</figcaption></figure>` : "";
    return `<article class="intel-feature-popup intel-traffic-card"><strong>${escape(title)}</strong><p>${escape(summary)}</p>
      ${props.track_label ? `<small>${escape(props.track_label)}</small>` : ""}
      <details class="intel-traffic-details"><summary>Vessel details</summary><dl>${fields.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("")}</dl>
      ${photo}<p>${escape(props.classification_note)}</p><p class="intel-feature-caution">Coverage and classifications are incomplete. Reports may be delayed or incorrect. Not a verified team report or a navigation/collision-avoidance service.${props.stale ? " This position is stale." : ""}</p>
      ${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.attribution || pack.attribution || pack.source)} ↗</a>` : `<small>${escape(props.source || pack.source)}</small>`}</details></article>`;
  }
  const fields = [["Reported", observed], ["Type", props.disaster_type || String(props.kind || "").replaceAll("_", " ")], ["Area / vector", props.area_label], ["GDACS level", pack.id === "gdacs" ? props.severity : null], ["Access", props.access], ["Confidence", props.confidence], ["Location precision", props.precision === 1 ? "Reported location" : props.precision === 2 ? "Approximate location" : props.precision === 3 ? "Regional location" : props.precision]];
  return `<article class="intel-feature-popup"><small>PUBLIC SOURCE · ${escape(props.source || pack.source || pack.title)}</small><strong>${pack.id === "gdacs" ? gdacsIconSvg(gdacsCode(props)) : ""}${escape(title)}</strong>${detail ? `<p>${escape(detail)}</p>` : ""}<dl>${fields.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("")}</dl><p class="intel-feature-caution">${pack.id === "gdacs" ? "Provider areas may be reported extents, forecast zones or reference buffers; check the area label. No outline does not mean no impact. " : ""}${pack.id === "firms" ? "Satellite thermal detection, not a confirmed fire. " : ""}${pack.id === "military" ? "Public map data. Boundaries and access may be incomplete or outdated. " : ""}${pack.id === "trails" ? "A mapped trail is not proof of safe or permitted access. " : ""}Not a verified team report.${result?.status === "stale" ? " Showing stale cached data." : ""}</p>${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.attribution || pack.attribution || "View source")} ↗</a>` : ""}${result?.fetched_at ? `<small>Fetched ${escape(stamp(result.fetched_at))}</small>` : ""}</article>`;
}

function updateTrafficChildren(current, next) {
  const key = node => current.tagName === "DL" ? node.firstElementChild.textContent : `${node.tagName} ${node.className}`;
  const unused = [...current.children];
  const pairs = [...next.children].map(child => {
    const index = unused.findIndex(existing => key(existing) === key(child));
    return [index === -1 ? null : unused.splice(index, 1)[0], child];
  });
  for (const child of unused) child.remove();
  let cursor = current.firstElementChild;
  for (const [existing, child] of pairs) {
    if (!existing) { current.insertBefore(child, cursor); continue; }
    if (existing !== cursor) current.insertBefore(existing, cursor);
    cursor = existing.nextElementSibling;
    if (existing.isEqualNode(child)) continue;
    if (existing.matches("details, dl")) updateTrafficChildren(existing, child);
    else if (current.tagName === "DL") existing.lastElementChild.textContent = child.lastElementChild.textContent;
    else if (existing.matches("strong, p, small")) existing.textContent = child.textContent;
    else existing.replaceWith(child);
  }
}

// Keep the open disclosure and its scroll anchors mounted during live updates.
export function replaceTrafficDetails(body, html) {
  if (!body) return;
  const current = body.firstElementChild;
  if (!current) { body.innerHTML = html; return; }
  const template = body.ownerDocument.createElement("template");
  template.innerHTML = html;
  updateTrafficChildren(current, template.content.firstElementChild);
}

// MapLibre keeps the same camera; adding/removing public layers never calls fitBounds.
export function installIntelLayers(map, pack, data = EMPTY(), contourUrl = null, ahnMode = "dtm", lidarProvider = null) {
  const id = sourceId(pack.id);
  const layers = map.getStyle?.()?.layers || [];
  // Contour tiles can finish loading after the live traffic layers have been installed.
  const before = layers.find(layer => (pack.id === "elevation" && TRAFFIC_SOURCES.has(layer.source))
    || isTeamLayer(layer) || (layer.type === "symbol" && !layer.id.startsWith(PREFIX)))?.id;
  if (pack.id === "elevation") {
    if (!contourUrl) return;
    if (!map.getSource(id)) map.addSource(id, {type: "vector", tiles: [contourUrl], minzoom: CONTOUR_MIN_ZOOM, maxzoom: CONTOUR_MAX_ZOOM});
    for (const layer of contourLayers(id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (pack.id === "transit") {
    if (!map.getSource(id)) map.addSource(id, {type: "geojson", data});
    else map.getSource(id).setData(data);
    const estimated = ["boolean", ["get", "estimated"], false];
    const mode = ["get", "kind"];
    const definitions = [
      {id: `${id}-points`, type: "circle", paint: {
        "circle-color": ["case", estimated, "#17383a", "#75ddd0"], "circle-radius": 8,
        "circle-stroke-color": "#75ddd0", "circle-stroke-width": 1.3, "circle-opacity": ["case", estimated, .72, .95]}},
      {id: `${id}-labels`, type: "symbol", layout: {
        "text-field": ["match", mode, "bus", "B", "train", "R", "metro", "M", "tram", "T", "ferry", "F", "•"],
        "text-font": ["Noto Sans Regular"], "text-size": 10, "text-allow-overlap": true, "text-ignore-placement": true},
        paint: {"text-color": ["case", estimated, "#b6fff2", "#102a2d"]}},
    ];
    for (const layer of definitions) if (!map.getLayer(layer.id)) map.addLayer({...layer, source: id}, before);
    return;
  }
  if (pack.id === "ahn") {
    const provider = lidarProvider || lidarProviderForBounds(map.getBounds?.());
    if (!provider) return;
    const tile = lidarTileUrl(ahnMode, provider);
    if (map.getSource(id)?.tiles?.[0] !== tile) {
      if (map.getSource(id)) removeIntelLayers(map, pack.id);
      map.addSource(id, lidarSource(id, provider, ahnMode));
    }
    for (const layer of lidarLayers(id, provider)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (ENVIRONMENT_RASTER_IDS.includes(pack.id)) {
    if (!map.getSource(id)) map.addSource(id, environmentSource(id, pack.id));
    for (const layer of environmentLayers(id, pack.id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (pack.id === "gdacs") { installGdacsIcons(map); data = gdacsDisplayData(data); }
  if (pack.id === "military") {
    installPoliceIcon(map);
    const showBasemap = map.getSource("offline") && (data.basemap_fallback ?? (!data.features.length && !data.worldwide_snapshot));
    for (const layer of MILITARY_FALLBACK_LAYERS) {
      if (showBasemap && !map.getLayer(layer.id)) map.addLayer({...layer, source: "offline"}, before);
      else if (!showBasemap && map.getLayer(layer.id)) map.removeLayer(layer.id);
    }
  }
  if (!map.getSource(id)) map.addSource(id, {type: "geojson", data});
  else map.getSource(id).setData(data);
  if (pack.id === "roads") {
    for (const layer of roadLayers(id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (TRAFFIC_IDS.includes(pack.id)) {
    installTrafficIcons(map);
    for (const layer of trafficLayers(id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (pack.id === "notams") {
    installNotamIcons(map);
    const upcoming = ["==", ["get", "notam_phase"], "upcoming"];
    const local = ["in", ["get", "local_activity"], ["literal", Object.keys(NOTAM_ACTIVITY_LABELS)]];
    const line = ["!=", ["geometry-type"], "Point"];
    const definitions = [
      {id: `${id}-lines`, type: "line", filter: ["all", line, ["!", local]],
        paint: {"line-color": COLORS.notams, "line-width": 1, "line-opacity": ["case", upcoming, .25, .5]}},
      {id: `${id}-local-lines`, type: "line", minzoom: 10, filter: ["all", line, local],
        paint: {"line-color": COLORS.notams, "line-width": 1.2, "line-opacity": ["case", upcoming, .35, .7]}},
      {id: `${id}-points`, type: "symbol", filter: ["any", local, ["==", ["geometry-type"], "Point"]],
        layout: {"icon-image": ["match", ["get", "local_activity"],
          ...Object.keys(NOTAM_ACTIVITY_LABELS).flatMap(key => [key, `public-notam-${key}`]), "public-notam-other"],
          "icon-size": .85, "icon-allow-overlap": true, "icon-ignore-placement": true},
        paint: {"icon-opacity": ["case", upcoming, .55, 1]}},
    ];
    for (const layer of definitions) if (!map.getLayer(layer.id)) map.addLayer({...layer, source: id}, before);
    return;
  }
  const color = pack.id === "military" ? ["match", ["get", "kind"], ["police_station", "police_property"], POLICE_COLOR, COLORS.military]
    : pack.id === "gdacs" ? GDACS_COLOR : COLORS[pack.id] || COLORS.gdacs;
  const pointFilter = ["==", ["geometry-type"], "Point"];
  const definitions = [
    {id: `${id}-areas`, type: "fill", filter: ["==", ["geometry-type"], "Polygon"], paint: {"fill-color": color, "fill-opacity": .12}},
    {id: `${id}-lines`, type: "line", filter: pack.id === "gdacs" ? ["==", ["geometry-type"], "Polygon"] : ["!=", ["geometry-type"], "Point"], layout: {"line-join": "round", "line-cap": pack.id === "trails" ? "butt" : "round"}, paint: {"line-color": pack.id === "trails" ? ROAD_COLOR : color, "line-width": pack.id === "trails" ? 2 : 1.5, "line-opacity": pack.id === "trails" ? .82 : .8, ...(pack.id === "trails" || pack.id === "military" ? {"line-dasharray": [3, 2]} : {})}},
    ...(pack.id === "gdacs" ? [{id: `${id}-tracks`, type: "line", filter: ["==", ["geometry-type"], "LineString"], paint: {"line-color": color, "line-width": 2, "line-dasharray": [3, 2], "line-opacity": .85}}] : []),
    {id: `${id}-points`, type: "circle", filter: pack.id === "military" ? ["all", pointFilter, ["!", POLICE_STATION]] : pointFilter, paint: {"circle-radius": pack.id === "gdacs" ? 13 : pack.id === "firms" ? 4 : 6, "circle-color": pack.id === "gdacs" ? "#10160f" : color, "circle-opacity": pack.id === "gdacs" ? .95 : .7, "circle-stroke-color": pack.id === "gdacs" ? color : "#1a1d15", "circle-stroke-width": 1.5}},
    ...(pack.id === "military" ? [{id: `${id}-police-stations`, type: "symbol", filter: POLICE_STATION, layout: POLICE_LAYOUT}] : []),
    ...(pack.id === "gdacs" ? [{id: `${id}-icons`, type: "symbol", filter: ["==", ["geometry-type"], "Point"], layout: {"icon-image": GDACS_ICON, "icon-size": .82, "icon-allow-overlap": true, "icon-ignore-placement": true}}] : []),
    {id: `${id}-labels`, type: "symbol", minzoom: pack.id === "trails" ? 13 : 9, filter: ["==", ["geometry-type"], "Point"], layout: {"text-field": ["coalesce", ["get", "title"], ["get", "name"], ""], "text-size": 10, "text-font": ["Noto Sans Regular"], "text-anchor": "top", "text-offset": [0, 1.1], "text-max-width": 16}, paint: {"text-color": color, "text-halo-color": "#0e130e", "text-halo-width": 1.5}},
  ];
  if (pack.id === "trails") definitions.push({id: `${id}-route-labels`, type: "symbol", minzoom: 12,
    filter: ["==", ["geometry-type"], "LineString"], layout: {"symbol-placement": "line", "text-field": ["coalesce", ["get", "title"], ["get", "name"], ""], "text-font": ["Noto Sans Regular"], "text-size": 11},
    paint: {"text-color": COLORS.trails, "text-halo-color": "#0e130e", "text-halo-width": 1.5}});
  for (const layer of definitions) if (!map.getLayer(layer.id)) map.addLayer({...layer, source: id}, before);
}

export function removeIntelLayers(map, id) {
  if (id === "military") for (const layer of MILITARY_FALLBACK_LAYERS) if (map.getLayer(layer.id)) map.removeLayer(layer.id);
  for (const layer of layerIds(id).reverse()) if (map.getLayer(layer)) map.removeLayer(layer);
  if (map.getSource(sourceId(id))) map.removeSource(sourceId(id));
}

export function createIntelPacks({button = null, panel = null, fetchImpl = globalThis.fetch?.bind(globalThis), getMapStyle = () => "hiking", setMapStyle = null, prepareContours = prepareContourTiles} = {}) {
  let catalogue = {packs: [], credentials: {}}, enabled = new Set(), gdacsTypes = normalizeGdacsTypes(), firmsDays = 3, ahnMode = "dtm", saving = false, destroyed = false, catalogueGeneration = 0, notice = "";
  let contourUrl = null, contourPending = null, contourFailed = false;
  let trafficFilters = normalizeTrafficFilters();
  let roadFilters = normalizeRoadFilters();
  let notamCategories = [], notamView = "local";
  const maps = new Map(), results = new Map(), credentialDrafts = new Map(), trafficDrafts = new Map(), autosaveTimers = new Map();
  const listeners = [];
  const listen = (target, event, handler) => { target?.addEventListener(event, handler); listeners.push(() => target?.removeEventListener(event, handler)); };
  const packById = id => catalogue.packs.find(pack => pack.id === id);
  const disasterTypes = () => {
    const types = packById("gdacs")?.disaster_types?.filter(type => GDACS_TYPES.some(known => known.id === type.id));
    return types?.length ? types : GDACS_TYPES;
  };
  const notamOptions = () => Object.entries(packById("notams")?.notam_categories || {});
  const visibleData = (id, data) => {
    if (id === "transit") {
      const now = Date.now() / 1000;
      return {...data, features: (data.features || []).filter(({properties}) =>
        now - properties.observed_at <= (properties.estimated ? 90 : 120))};
    }
    if (id === "notams") {
      const now = Date.now() / 1000;
      const recent = !data.fetched_at || now - data.fetched_at <= 1800;
      return {...data, features: recent ? (data.features || []).flatMap(feature => {
        const props = feature.properties;
        if (notamView === "local" ? !Object.hasOwn(NOTAM_ACTIVITY_LABELS, props.local_activity) : !notamCategories.includes(props.kind)) return [];
        const periods = props.notam_periods ?? (props.schedule && !["H24", "CONTINUOUS"].includes(props.schedule.toUpperCase()) ? [] : [[Date.parse(props.valid_from) / 1000, props.valid_until]]);
        const period = periods.find(([start, end]) => start <= now + 3600 && (end == null || end > now));
        if (!period) return [];
        const [start, end] = period;
        return [{...feature, properties: {...props, notam_phase: start > now ? "upcoming" : "active", notam_start: start, notam_end: end}}];
      }) : []};
    }
    return id === "roads" ? filterRoadReports(data, roadFilters) : TRAFFIC_IDS.includes(id) ? trafficDisplayData(id, data, trafficFilters[id]) : id === "gdacs" ? {...gdacsDisplayData(data, gdacsTypes), gdacs_types: [...gdacsTypes]} : data;
  };
  const currentPacks = () => catalogue.packs.map(pack => ({...pack, enabled: enabled.has(pack.id)}));
  const isVisible = context => !context.paused && globalThis.document?.visibilityState !== "hidden" && (!context.map.getContainer?.()?.getClientRects || context.map.getContainer().getClientRects().length > 0);
  const activeMap = () => [...maps.values()].reverse().find(isVisible);
  const packSource = pack => {
    if (pack.id !== "ahn") return {url: safeLink(pack.source_url), label: pack.source || "Source"};
    const provider = lidarProviderForBounds(activeMap()?.map.getBounds?.());
    return provider ? {url: provider.source_url, label: provider.source} : {url: safeLink(pack.source_url), label: "Provider source"};
  };
  const hasParsedStyle = context => {
    try { return Array.isArray(context.map.getStyle?.()?.layers); } catch { return false; }
  };
  const motionPreference = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  const animatedData = (context, id, data, now = Date.now()/1000) => {
    const selected = context.selectedTraffic;
    if (selected?.pack === id) {
      const reported = data.features.find(feature => trafficKey(feature) === selected.key);
      if (reported && reported.properties.position_time >= selected.feature.properties.position_time) selected.feature = reported;
      data = {...data, features: [...data.features.filter(feature => trafficKey(feature) !== selected.key),
        trafficDetailFeature(context, selected.feature, id, {traffic_selected: true})]};
    }
    const filtered = visibleData(id, data);
    return TRAFFIC_IDS.includes(id) ? context.motion.get(id).sample(filtered, now, !motionPreference?.matches) : {data: filtered, active: false};
  };
  const trafficAnimator = createTrafficAnimator({
    visible: () => !destroyed && !motionPreference?.matches && [...maps.values()].some(context => isVisible(context) && hasParsedStyle(context)),
    paint: now => {
      let active = false;
      for (const context of maps.values()) {
        if (!isVisible(context) || !hasParsedStyle(context)) continue;
        for (const id of TRAFFIC_IDS) {
          const source = context.map.getSource(sourceId(id));
          if (!enabled.has(id) || !source || !context.data.has(id)) continue;
          const frame = animatedData(context, id, context.data.get(id), now);
          source.setData(frame.data);
          if (context.selectedTraffic?.pack === id) {
            const feature = frame.data.features.find(item => trafficKey(item) === context.selectedTraffic.key);
            showTrafficEstimate(context.map, feature);
            followTraffic(context, feature);
          }
          active ||= frame.active;
        }
      }
      return active;
    },
  });
  listen(motionPreference, "change", () => {
    trafficAnimator.stop();
    for (const context of maps.values()) { for (const model of context.motion.values()) model.clear(); syncLayers(context); }
  });

  function setOpen(open) {
    if (!panel) return;
    panel.classList.toggle("hidden", !open);
    panel.hidden = !open;
    button?.setAttribute("aria-expanded", String(open));
    if (open) {
      const select = panel.querySelector("[data-map-style]");
      if (select) select.value = getMapStyle();
      panel.querySelector("[data-intel-close]")?.focus();
    }
    else button?.focus();
  }

  function buildPanel() {
    if (!panel) return;
    panel.setAttribute("aria-labelledby", "mapLayersHeading");
    panel.innerHTML = `
      <header class="intel-packs-head"><h2 id="mapLayersHeading">Map layers</h2><button type="button" data-intel-close aria-label="Close map layers">×</button></header>
      <section class="map-layer-options">
        <h3>Map & terrain</h3>
        ${setMapStyle ? '<label class="map-style-select">Base map<select data-map-style><option value="hiking">Hiking map</option><option value="satellite">Satellite</option></select></label>' : ''}
        <div data-map-features></div>
      </section>
      <h3 class="intel-section-title">Public layers</h3>
      <p class="intel-packs-privacy">Public layers share the viewed area with their providers. Your settings and access keys stay on this device.</p>
      <div class="intel-pack-list"></div>
      <p class="intel-packs-notice" role="status"></p>
      <div class="intel-packs-actions"><button type="button" data-intel-refresh>Refresh this view</button></div>
      <p class="intel-packs-footnote">Public coverage and freshness vary. Traffic keeps its last reported position for up to 30 minutes. Zoom in to fetch more detail. Public layers use a separate cache from offline maps.</p>`;
    const styleSelect = panel.querySelector("[data-map-style]");
    listen(styleSelect, "change", async () => {
      styleSelect.disabled = true;
      try { await setMapStyle(styleSelect.value); }
      finally { styleSelect.value = getMapStyle(); styleSelect.disabled = false; }
    });
    listen(panel.querySelector("[data-intel-close]"), "click", () => setOpen(false));
    listen(panel, "change", event => {
      const id = event.target?.dataset?.intelPack;
      if (id) void setEnabled(id, event.target.checked);
      if (event.target?.matches?.("[data-ahn-mode]")) void setAhnMode(event.target.value);
      if (event.target?.matches?.("[data-firms-days]")) void setFirmsDays(Number(event.target.value));
      const type = event.target?.dataset?.gdacsType;
      if (type) void setGdacsTypes(event.target.checked ? [...gdacsTypes, type] : gdacsTypes.filter(id => id !== type));
      const category = event.target?.dataset?.notamCategory;
      if (category) void setNotamCategories(event.target.checked ? [...notamCategories, category] : notamCategories.filter(id => id !== category));
      if (event.target?.matches?.("[data-notam-view]")) void setNotamView(event.target.value);
      const roadGroup = event.target?.dataset?.roadGroup;
      const roadValue = event.target?.dataset?.roadValue;
      if (roadGroup && roadValue) void setRoadFilters({...roadFilters, [roadGroup]: event.target.checked ? [...roadFilters[roadGroup], roadValue] : roadFilters[roadGroup].filter(v => v !== roadValue)});
    });
    listen(panel.querySelector("[data-intel-refresh]"), "click", () => void refresh());
    const saveField = (target, delay) => {
      const key = target?.name;
      if (ACCESS[key]) {
        const value = target.value;
        credentialDrafts.set(key, value);
        if (!key.startsWith("host_")) scheduleAutosave(`credential:${key}`, () => updateCredentials({[key]: value.trim()}), delay);
      }
      const form = target?.closest?.("[data-traffic-form]");
      const id = form?.dataset?.trafficForm;
      if (TRAFFIC_IDS.includes(id)) {
        const value = {affiliation: form.elements.affiliation.value, type: form.elements.type.value, query: form.elements.query.value};
        trafficDrafts.set(id, value);
        scheduleAutosave(`traffic:${id}`, async () => {
          const saved = await setTrafficFilters(id, value);
          if (saved && trafficDrafts.get(id) === value) trafficDrafts.delete(id);
        }, delay);
      }
    };
    listen(panel, "input", event => saveField(event.target, 500));
    listen(panel, "change", event => saveField(event.target, 0));
    listen(panel, "submit", event => {
      if (!event.target?.matches?.("[data-traffic-form], [data-intel-access-form]")) return;
      event.preventDefault();
      const input = event.target.querySelector("input");
      if (input.name.startsWith("host_")) void updateCredentials({[input.name]: input.value.trim()});
      else saveField(input, 0);
    });
    listen(panel, "click", event => {
      const key = event.target?.closest?.("[data-intel-clear]")?.dataset?.intelClear;
      if (ACCESS[key]) {
        credentialDrafts.set(key, "");
        scheduleAutosave(`credential:${key}`, () => updateCredentials({[key]: ""}), 0);
      }
      const selection = event.target?.closest?.("[data-gdacs-select]")?.dataset?.gdacsSelect;
      if (selection) void setGdacsTypes(selection === "all" ? disasterTypes().map(type => type.id) : []);
      const download = event.target?.closest?.("[data-intel-download]")?.dataset?.intelDownload;
      if (download) void downloadView(download);
    });
  }

  // Coalesce typing and defer saves while another setting is being persisted.
  function scheduleAutosave(key, action, delay) {
    clearTimeout(autosaveTimers.get(key));
    const run = () => {
      if (destroyed) return;
      if (saving) { autosaveTimers.set(key, setTimeout(run, 50)); return; }
      autosaveTimers.delete(key);
      void action();
    };
    autosaveTimers.set(key, setTimeout(run, delay));
  }

  function renderCatalogue() {
    const active = panel?.ownerDocument?.activeElement;
    const focusedTraffic = active?.closest?.("[data-traffic-form]")?.dataset?.trafficForm;
    const focusedName = active?.name;
    const selectionStart = active?.selectionStart, selectionEnd = active?.selectionEnd;
    const focusedCredential = active?.name;
    const focusedPack = panel?.ownerDocument?.activeElement?.dataset?.intelPack;
    const focusedType = panel?.ownerDocument?.activeElement?.dataset?.gdacsType;
    const focusedAhnMode = focusedName === "ahn-mode";
    const filtersOpen = Boolean(panel?.querySelector('[data-pack-row="gdacs"] .intel-disaster-filter')?.open);
    const notamOpen = Boolean(panel?.querySelector("[data-notam-filter]")?.open);
    const focusedCategory = active?.dataset?.notamCategory;
    const roadOpen = Boolean(panel?.querySelector("[data-road-filter]")?.open);
    const focusedRoadGroup = active?.dataset?.roadGroup, focusedRoadValue = active?.dataset?.roadValue;
    const accessOpen = Object.keys(ACCESS).filter(key => panel?.querySelector(`[data-intel-access="${key}"]`)?.open);
    const trafficOpen = TRAFFIC_IDS.filter(id => panel?.querySelector(`[data-traffic-filter="${id}"]`)?.open);
    if (panel) panel.querySelector(".intel-pack-list").innerHTML = currentPacks().map(pack => { const source = packSource(pack); return `<section class="intel-pack-row" data-pack-row="${escape(pack.id)}"><label class="intel-pack-switch"><span><strong>${escape(pack.title)}</strong><small>${escape(pack.description)}</small></span><input type="checkbox" role="switch" data-intel-pack="${escape(pack.id)}" aria-describedby="intel-status-${escape(pack.id)}" ${pack.enabled ? "checked" : ""}/><i aria-hidden="true"></i></label><div class="intel-pack-meta"><span id="intel-status-${escape(pack.id)}" data-intel-status="${escape(pack.id)}"></span>${source.url ? `<a href="${escape(source.url)}" target="_blank" rel="noopener noreferrer">${escape(source.label)} ↗</a>` : ""}</div><p class="intel-pack-note" data-intel-note="${escape(pack.id)}"></p>${pack.offline_downloadable ? `<button type="button" class="intel-pack-download" data-intel-download="${escape(pack.id)}">SAVE THIS VIEW OFFLINE</button><small class="intel-pack-download-note">Keeps this visible map area on this device without an expiry.</small>` : ""}</section>`; }).join("");
    panel?.querySelector('[data-pack-row="firms"]')?.insertAdjacentHTML("beforeend", `<label class="intel-firms-window">Detection window<select data-firms-days aria-label="NASA FIRMS detection window">${[[1, "24 hours"], [3, "3 days"], [7, "7 days"]].map(([days, label]) => `<option value="${days}" ${days === firmsDays ? "selected" : ""}>${label}</option>`).join("")}</select></label>`);
    const gdacsRow = panel?.querySelector('[data-pack-row="gdacs"]');
    panel?.querySelector('[data-pack-row="notams"]')?.insertAdjacentHTML("beforeend", `<label class="intel-firms-window">NOTAM view<select name="notam-view" data-notam-view><option value="local">Local activity</option><option value="all">All notices</option></select></label><small class="intel-pack-download-note">Active now or starting within one hour</small><details class="intel-disaster-filter" data-notam-filter ${notamOpen ? "open" : ""}><summary>NOTAM categories <span data-notam-summary></span></summary><div class="intel-disaster-grid">${notamOptions().map(([id, label]) => `<label><input type="checkbox" data-notam-category="${escape(id)}"/><span>${escape(label)}</span></label>`).join("")}</div></details>`);
    panel?.querySelector('[data-pack-row="roads"]')?.insertAdjacentHTML("beforeend", `<details class="intel-disaster-filter" data-road-filter ${roadOpen ? "open" : ""}><summary>Filters · report type & impact</summary>${[["types", "Report types", ROAD_TYPES], ["impacts", "Impact", ROAD_IMPACTS]].map(([group, title, choices]) => `<fieldset><legend>${title}</legend><div class="intel-disaster-grid">${Object.entries(choices).map(([value, label]) => `<label><input type="checkbox" data-road-group="${group}" data-road-value="${value}"/><span>${escape(label)}</span></label>`).join("")}</div></fieldset>`).join("")}<p>Vehicle obstructions are off initially. Filters change the map; navigation still checks full closures while this layer is enabled.</p></details>`);
    for (const [key, field] of Object.entries(ACCESS)) {
      const hostKey = key.startsWith("host_");
      const pack = packById(field.pack);
      if (hostKey && !pack?.can_provide_host_key) continue;
      const title = !hostKey && (pack?.host_access || pack?.host_notams) ? `${field.title} on this device` : field.title;
      panel?.querySelector(`[data-pack-row="${field.pack}"]`)?.insertAdjacentHTML("beforeend", `<details class="intel-access-section" data-intel-access="${key}" ${accessOpen.includes(key) ? "open" : ""}><summary>${title}</summary><form class="intel-access-field" data-intel-access-form="${key}"><label for="intel-${key}">${title}</label><div class="intel-access-input"><input id="intel-${key}" name="${key}" type="password" autocomplete="off" spellcheck="false" maxlength="8192" value="${escape(credentialDrafts.get(key) || "")}" placeholder="Paste ${escape(field.title)}"/></div><div class="intel-access-links"><a href="${field.url}" target="_blank" rel="noopener noreferrer">${field.link} ↗</a>${hostKey ? '<button type="submit">Save on host</button>' : `<button type="button" data-intel-clear="${key}">Remove saved key</button>`}</div></form></details>`);
    }
    const mapFeatures = panel?.querySelector("[data-map-features]");
    if (mapFeatures) {
      mapFeatures.replaceChildren();
      for (const id of ["elevation", "ahn", ...ENVIRONMENT_RASTER_IDS]) {
        const row = panel.querySelector(`[data-pack-row="${id}"]`);
        if (row) mapFeatures.append(row);
      }
      mapFeatures.insertAdjacentHTML("beforeend", `<section class="intel-analysis-card"><strong>Sentinel-1 TomoSAR analysis</strong><p>Multi-baseline 3D radar analysis is generated on demand from an SLC stack; there is no reliable public live tile layer to toggle here.</p><a href="https://ids-dinamis.data-terra.org/tomosar-services" target="_blank" rel="noopener noreferrer">Open TomoSAR service ↗</a></section>`);
    }
    panel?.querySelector('[data-pack-row="ahn"]')?.insertAdjacentHTML("beforeend", `<label class="intel-ahn-mode">Elevation view<select name="ahn-mode" data-ahn-mode aria-label="LiDAR elevation view">${Object.entries(AHN_MODES).map(([value, label]) => `<option value="${value}">${escape(label)}</option>`).join("")}</select></label><p class="intel-pack-note">Available coverage includes the Netherlands, England, France, Malta and the United States. Online only.</p>`);
    if (gdacsRow) gdacsRow.insertAdjacentHTML("beforeend", `<details class="intel-disaster-filter" ${filtersOpen ? "open" : ""}><summary>Disaster types <span data-gdacs-summary></span></summary><div class="intel-disaster-actions"><button type="button" data-gdacs-select="all">All</button><button type="button" data-gdacs-select="none">None</button></div><div class="intel-disaster-grid">${disasterTypes().map(type => `<label><input type="checkbox" data-gdacs-type="${escape(type.id)}"/>${gdacsIconSvg(type.id)}<span>${escape(type.label)}</span></label>`).join("")}</div><p>Icon shows the disaster type. Color shows the GDACS alert level, not whether an area is safe.</p></details>`);
    for (const id of TRAFFIC_IDS) {
      const selected = trafficDrafts.get(id) || trafficFilters[id];
      const options = (items, value) => Object.entries(items).map(([key, label]) => `<option value="${key}" ${key === value ? "selected" : ""}>${escape(label)}</option>`).join("");
      panel?.querySelector(`[data-pack-row="${id}"]`)?.insertAdjacentHTML("beforeend", `<details class="intel-traffic-filter" data-traffic-filter="${id}" ${trafficOpen.includes(id) ? "open" : ""}><summary>Filters · classification & type</summary><form data-traffic-form="${id}"><label>Classification<select name="affiliation">${options({all:"All classifications",military:id === "flights" ? "Military (provider flag)" : "Military / restricted (AIS 35)", commercial:id === "flights" ? "Commercial / airline (estimated)" : "Commercial (AIS type)",unknown:"Other / unknown ownership"},selected.affiliation)}</select></label><label>Type<select name="type">${options({all:"All types",...TRAFFIC_TYPES[id]},selected.type)}</select></label><label>${id === "flights" ? "Callsign, registration or model" : "Vessel name, callsign or MMSI"}<input name="query" type="search" maxlength="64" value="${escape(selected.query)}" placeholder="Search received traffic"/></label><p>${id === "flights" ? "Commercial is an estimate from callsign and aircraft category, not confirmed ownership. Missing military flags do not prove civilian status." : "AIS type is self-reported and can arrive after a position. Type 35 includes military and other restricted operations. Unknown vessels stay separate."}</p></form></details>`);
    }
    if (packById("flights")?.personal_fallback) {
      panel?.querySelector('[data-pack-row="flights"] .intel-pack-meta')?.insertAdjacentHTML("beforeend",
        '<a href="https://adsb.fi/" target="_blank" rel="noopener noreferrer">adsb.fi fallback ↗</a>');
    }
    renderStatus();
    const restore = focusedTraffic ? panel?.querySelector(`[data-traffic-form="${focusedTraffic}"] [name="${focusedName}"]`) : ACCESS[focusedCredential] ? panel?.querySelector(`[name="${focusedCredential}"]`) : null;
    restore?.focus();
    if (restore && selectionStart != null) restore.setSelectionRange(selectionStart, selectionEnd);
    if (focusedPack && IDS.includes(focusedPack)) panel.querySelector(`[data-intel-pack="${focusedPack}"]`)?.focus();
    if (focusedAhnMode) panel.querySelector("[data-ahn-mode]")?.focus();
    if (focusedType && GDACS_TYPES.some(type => type.id === focusedType)) panel.querySelector(`[data-gdacs-type="${focusedType}"]`)?.focus();
    if (focusedCategory) [...panel.querySelectorAll("[data-notam-category]")].find(input => input.dataset.notamCategory === focusedCategory)?.focus();
    if (focusedName === "notam-view") panel.querySelector("[data-notam-view]")?.focus();
    if (["types", "impacts"].includes(focusedRoadGroup) && [...Object.keys(ROAD_TYPES), ...Object.keys(ROAD_IMPACTS)].includes(focusedRoadValue)) panel.querySelector(`[data-road-group="${focusedRoadGroup}"][data-road-value="${focusedRoadValue}"]`)?.focus();
  }

  function renderStatus() {
    const windowSelect = panel?.querySelector("[data-firms-days]");
    if (windowSelect) { windowSelect.value = String(firmsDays); windowSelect.disabled = saving; }
    const ahnSelect = panel?.querySelector("[data-ahn-mode]");
    if (ahnSelect) { ahnSelect.value = ahnMode; ahnSelect.disabled = saving; }
    const count = enabled.size;
    if (button) {
      button.classList.add("intel-packs-toggle");
      button.setAttribute("aria-label", `Map layers${count ? `, ${count} enabled` : ""}`);
      button.title = `Map layers and intel packs · ${count} enabled`;
      button.classList.toggle("is-active", count > 0);
      const counter = button.querySelector("[data-intel-count]");
      if (counter) { counter.textContent = String(count); counter.hidden = !count; }
    }
    if (!panel) return;
    const typeSummary = panel.querySelector("[data-gdacs-summary]");
    if (typeSummary) typeSummary.textContent = gdacsTypes.length === disasterTypes().length ? "All" : gdacsTypes.length ? `${gdacsTypes.length} of ${disasterTypes().length}` : "None";
    for (const input of panel.querySelectorAll("[data-gdacs-type]")) { input.checked = gdacsTypes.includes(input.dataset.gdacsType); input.disabled = saving; }
    for (const control of panel.querySelectorAll("[data-gdacs-select]")) control.disabled = saving;
    const notamSummary = panel.querySelector("[data-notam-summary]");
    if (notamSummary) notamSummary.textContent = notamCategories.length === notamOptions().length ? "All" : notamCategories.length ? `${notamCategories.length} of ${notamOptions().length}` : "None";
    const notamSelect = panel.querySelector("[data-notam-view]");
    if (notamSelect) { notamSelect.value = notamView; notamSelect.disabled = saving; }
    const notamFilter = panel.querySelector("[data-notam-filter]");
    if (notamFilter) notamFilter.hidden = notamView !== "all";
    for (const input of panel.querySelectorAll("[data-notam-category]")) { input.checked = notamCategories.includes(input.dataset.notamCategory); input.disabled = saving; }
    for (const input of panel.querySelectorAll("[data-road-group]")) { input.checked = roadFilters[input.dataset.roadGroup].includes(input.dataset.roadValue); input.disabled = saving; }
    for (const control of panel.querySelectorAll("[data-traffic-form] button, [data-traffic-form] select, [data-traffic-form] input")) control.disabled = false;
    for (const pack of currentPacks()) {
      const state = results.get(pack.id);
      const input = panel.querySelector(`[data-intel-pack="${pack.id}"]`);
      if (input) { input.checked = pack.enabled; input.disabled = saving; }
      const status = panel.querySelector(`[data-intel-status="${pack.id}"]`);
      if (status) { status.textContent = intelLayerWarning(pack, state); status.dataset.state = state?.status || (pack.enabled && pack.configured === false ? "needs_key" : ""); }
      const note = panel.querySelector(`[data-intel-note="${pack.id}"]`);
      if (note) note.textContent = "";
    }
    panel.querySelector(".intel-packs-notice").textContent = notice;
    for (const [key, field] of Object.entries(ACCESS)) {
      const configured = Boolean(catalogue.credentials?.[key]);
      const input = panel.querySelector(`[name="${key}"]`);
      if (input) {
        input.placeholder = configured ? catalogue.credential_previews?.[key] || "••••••••" : `Paste ${field.title}`;
        input.title = configured ? "Saved key · enter a new key to replace it" : field.title;
        input.disabled = false;
      }
      const remove = panel.querySelector(`[data-intel-clear="${key}"]`);
      if (remove) remove.hidden = !configured;
    }
    for (const element of panel.querySelectorAll("[data-intel-access-form] button")) element.disabled = saving;
  }

  async function request(url, options = {}) {
    const response = await fetchImpl(url, {cache: "no-store", ...options});
    let payload;
    try { payload = await response.json(); } catch { throw new Error("The local app returned an unreadable response."); }
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : payload.error || "The local app could not load public intel.");
    return payload;
  }

  function applyCatalogue(value, refreshMaps = true) {
    catalogue = {...value, packs: (value.packs || []).filter(pack => IDS.includes(pack.id))};
    enabled = new Set(value.settings?.enabled || catalogue.packs.filter(pack => pack.enabled).map(pack => pack.id));
    enabled = new Set([...enabled].filter(id => IDS.includes(id)));
    gdacsTypes = normalizeGdacsTypes(value.settings?.gdacs_types, disasterTypes());
    const selected = value.settings?.notam_categories;
    notamCategories = notamOptions().map(([id]) => id).filter(id => !Array.isArray(selected) || selected.includes(id));
    notamView = value.settings?.notam_view === "all" ? "all" : "local";
    trafficFilters = normalizeTrafficFilters(value.settings?.traffic_filters);
    roadFilters = normalizeRoadFilters(value.settings?.road_filters);
    ahnMode = normalizeAhnMode(value.settings?.ahn_mode);
    firmsDays = [1, 3, 7].includes(value.settings?.firms_days) ? value.settings.firms_days : 3;
    renderCatalogue();
    for (const context of maps.values()) { syncLayers(context); if (refreshMaps) schedule(context, 0); }
  }

  async function refresh({resetHostData = false} = {}) {
    const generation = ++catalogueGeneration;
    if (resetHostData) {
      for (const id of SHARED_LAYERS) {
        for (const context of maps.values()) {
          invalidate(context, id);
          context.data.delete(id);
          context.map.getSource(sourceId(id))?.setData(EMPTY());
          if (context.popupPack === id) context.popup?.remove();
        }
        results.delete(id);
      }
    }
    try {
      const value = await request("/api/intel-packs");
      if (!destroyed && generation === catalogueGeneration) {
        notice = "";
        // Recreate failed vector tiles so reconnecting retries contour generation.
        for (const context of maps.values()) if (hasParsedStyle(context)) for (const id of ["elevation", "ahn", ...ENVIRONMENT_RASTER_IDS]) removeIntelLayers(context.map, id);
        contourFailed = false;
        results.delete("elevation");
        results.delete("ahn");
        for (const id of ENVIRONMENT_RASTER_IDS) results.delete(id);
        applyCatalogue(value);
      }
      return value;
    } catch (error) {
      if (!destroyed && generation === catalogueGeneration) { notice = error.message; renderStatus(); }
      return null;
    }
  }

  async function save(body) {
    if (saving || destroyed) return false;
    saving = true;
    ++catalogueGeneration;
    renderStatus();
    try {
      const value = await request("/api/intel-packs/settings", {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (destroyed) return false;
      notice = body.credentials ? Object.keys(body.credentials).some(key => key.startsWith("host_")) ? "Source access saved on the team host." : "Source access saved on this device." : "";
      if (body.credentials) for (const key of Object.keys(body.credentials)) {
        if (key.startsWith("host_") || credentialDrafts.get(key)?.trim() === body.credentials[key]) credentialDrafts.delete(key);
      }
      const displayOnly = Boolean(body.notam_categories || body.notam_view);
      if (!displayOnly) results.clear();
      applyCatalogue(value, !displayOnly);
      return true;
    } catch (error) { notice = error.message; return false; }
    finally { saving = false; renderStatus(); }
  }

  async function setEnabled(id, value) {
    if (!packById(id) || saving || destroyed) return false;
    const restoreFocus = panel?.ownerDocument?.activeElement?.dataset?.intelPack === id;
    const previous = new Set(enabled);
    if (value) enabled.add(id); else enabled.delete(id);
    for (const context of maps.values()) { invalidate(context, id); syncLayers(context); }
    renderStatus();
    const saved = await save({enabled: [...enabled]});
    if (!saved) { enabled = previous; for (const context of maps.values()) { syncLayers(context); schedule(context, 0); } renderStatus(); }
    else if (value && packById(id)?.configured === false && panel) panel.querySelector(`[data-pack-row="${id}"] input[type="password"]`)?.focus();
    if (restoreFocus) panel.querySelector(`[data-intel-pack="${id}"]`)?.focus();
    return saved;
  }

  async function setAhnMode(value) {
    if (!packById("ahn") || saving || destroyed) return false;
    const previous = ahnMode;
    ahnMode = normalizeAhnMode(value);
    for (const context of maps.values()) {
      removeIntelLayers(context.map, "ahn");
      syncLayers(context);
    }
    renderStatus();
    const saved = await save({ahn_mode: ahnMode});
    if (!saved) {
      ahnMode = previous;
      for (const context of maps.values()) { removeIntelLayers(context.map, "ahn"); syncLayers(context); }
      renderStatus();
    }
    panel?.querySelector("[data-ahn-mode]")?.focus();
    return saved;
  }

  async function updateCredentials(credentials) {
    const filtered = Object.fromEntries(Object.entries(credentials).filter(([key, value]) => ACCESS[key] && typeof value === "string"));
    if (!Object.keys(filtered).length) return false;
    for (const context of maps.values()) {
      invalidate(context);
      for (const id of new Set(Object.keys(filtered).map(key => ACCESS[key].pack))) if (SHARED_LAYERS.includes(id)) {
        context.data.delete(id);
        context.map.getSource(sourceId(id))?.setData(EMPTY());
        if (context.popupPack === id) context.popup?.remove();
      }
    }
    return save({credentials: filtered});
  }

  async function setNotamCategories(value) {
    if (!packById("notams") || saving || destroyed) return false;
    const previous = notamCategories;
    notamCategories = notamOptions().map(([id]) => id).filter(id => value.includes(id));
    const saved = await save({notam_categories: notamCategories});
    if (!saved) notamCategories = previous;
    for (const context of maps.values()) {
      if (context.popupPack === "notams") context.popup?.remove();
      syncLayers(context);
    }
    renderStatus();
    return saved;
  }

  async function setNotamView(value) {
    if (!packById("notams") || !["local", "all"].includes(value)) return false;
    const saved = await save({notam_view: value});
    if (saved) for (const context of maps.values()) if (context.popupPack === "notams") context.popup?.remove();
    return saved;
  }

  async function downloadView(id) {
    const context = activeMap();
    const pack = packById(id);
    if (!context || !pack?.enabled || saving || destroyed) { notice = "Enable Military areas, airstrips & police, then open the map view you want to save."; renderStatus(); return false; }
    const bounds = context.map.getBounds();
    const button = panel?.querySelector(`[data-intel-download="${id}"]`);
    if (button) { button.disabled = true; button.textContent = "SAVING VIEW…"; }
    try {
      const result = await request(`/api/intel-packs/${encodeURIComponent(id)}/download`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()], zoom: context.map.getZoom()})});
      results.set(id, visibleData(id, result));
      context.data.set(id, result);
      syncLayers(context);
      notice = "Map area saved on this device for offline use.";
      return true;
    } catch (error) { notice = error.message; return false; }
    finally { renderStatus(); }
  }

  async function setFirmsDays(value) {
    if (![1, 3, 7].includes(value) || saving || destroyed) return false;
    const previous = firmsDays;
    firmsDays = value;
    results.delete("firms");
    for (const context of maps.values()) {
      invalidate(context, "firms");
      context.data.delete("firms");
      context.popup?.remove();
      syncLayers(context);
    }
    const saved = await save({firms_days: value});
    if (!saved) {
      firmsDays = previous;
      for (const context of maps.values()) schedule(context, 0);
      renderStatus();
    }
    panel?.querySelector("[data-firms-days]")?.focus();
    return saved;
  }

  async function setRoadFilters(value) {
    if (saving || destroyed) return false;
    const focused = panel?.ownerDocument?.activeElement?.dataset;
    const focusGroup = focused?.roadGroup, focusValue = focused?.roadValue;
    const previous = roadFilters;
    roadFilters = normalizeRoadFilters(value);
    for (const context of maps.values()) syncLayers(context);
    renderStatus();
    const saved = await save({road_filters: roadFilters});
    if (!saved) {
      roadFilters = previous;
      for (const context of maps.values()) syncLayers(context);
      renderStatus();
    }
    if (["types", "impacts"].includes(focusGroup) && [...Object.keys(ROAD_TYPES), ...Object.keys(ROAD_IMPACTS)].includes(focusValue)) panel?.querySelector(`[data-road-group="${focusGroup}"][data-road-value="${focusValue}"]`)?.focus();
    return saved;
  }

  async function setGdacsTypes(value) {
    if (!packById("gdacs") || !Array.isArray(value) || saving || destroyed) return false;
    const previous = gdacsTypes;
    gdacsTypes = normalizeGdacsTypes(value, disasterTypes());
    const previousResult = results.get("gdacs");
    if (previousResult) results.set("gdacs", visibleData("gdacs", previousResult));
    for (const context of maps.values()) { invalidate(context, "gdacs"); context.popup?.remove(); syncLayers(context); }
    renderStatus();
    const saved = await save({gdacs_types: [...gdacsTypes]});
    if (!saved) {
      gdacsTypes = previous;
      if (previousResult) results.set("gdacs", previousResult); else results.delete("gdacs");
      for (const context of maps.values()) { syncLayers(context); schedule(context, 0); }
      renderStatus();
    }
    return saved;
  }

  async function setTrafficFilters(id, value) {
    if (!TRAFFIC_IDS.includes(id) || saving || destroyed) return false;
    const previous = trafficFilters;
    trafficFilters = normalizeTrafficFilters({...trafficFilters, [id]: value});
    for (const context of maps.values()) { context.popup?.remove(); syncLayers(context); }
    const saved = await save({traffic_filters: trafficFilters});
    if (!saved) { trafficFilters = previous; for (const context of maps.values()) syncLayers(context); renderCatalogue(); }
    return saved;
  }

  function invalidate(context, onlyId) {
    for (const [id, request] of context.requests) if (!onlyId || id === onlyId) { request.abort.abort(); context.requests.delete(id); }
  }

  function updateCredit(context) {
    if (context.transitCredit) {
      const credits = new Map();
      for (const {properties} of context.data.get("transit")?.features || []) {
        if (properties.source === "Transitous") {
          credits.set("Transitous sources", "https://transitous.org/sources/");
          credits.set("© OpenStreetMap", "https://www.openstreetmap.org/copyright");
        } else credits.set(properties.source, safeLink(properties.source_url));
      }
      context.transitCredit.hidden = !enabled.has("transit") || !credits.size;
      context.transitCredit.innerHTML = [...credits].filter(([, url]) => url).map(([label, url]) =>
        `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`).join(" · ");
    }
    if (!context.credit) return;
    const credits = new Map();
    for (const pack of currentPacks().filter(pack => pack.enabled && pack.configured !== false)) {
      const provider = pack.id === "ahn" ? lidarProviderForBounds(context.map.getBounds?.()) : null;
      const url = pack.id === "elevation" ? "https://github.com/tilezen/joerd/blob/master/docs/attribution.md" : pack.id === "ahn" && provider ? provider.attribution_url : ["trails", "military"].includes(pack.id) ? "https://www.openstreetmap.org/copyright" : safeLink(pack.attribution_url || pack.source_url);
      const label = pack.id === "elevation" ? "Terrain: Mapzen / data providers" : pack.id === "ahn" && provider ? provider.attribution : pack.attribution || pack.source || pack.title;
      credits.set(label, url);
    }
    context.credit.innerHTML = [...credits].map(([label, url]) => url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>` : escape(label)).join(" · ");
    context.credit.hidden = !credits.size;
  }

  function syncLayers(context) {
    const map = context.map;
    if (!hasParsedStyle(context)) return;
    for (const id of IDS) {
      const pack = packById(id);
      // Vector requests wait for the saved catalogue; DEM requests start as soon
      // as addSource runs. Never request a newly-enabled DEM before PATCH commits.
      if (!enabled.has(id) || pack?.configured === false || (id === "elevation" && pack?.enabled === false)) { removeIntelLayers(map, id); context.motion.get(id)?.clear(); context.tracks.get(id)?.clear(); }
      else if (pack) {
        try {
          if (id === "elevation" && !contourUrl) {
            if (!contourPending && !contourFailed) {
              results.set(id, {status: "loading"});
              contourPending = Promise.resolve().then(prepareContours).then(url => {
                contourUrl = url;
                if (destroyed) return;
                results.delete("elevation");
                for (const current of maps.values()) { syncLayers(current); schedule(current, 0); }
              }).catch(error => {
                contourFailed = true;
                if (!destroyed) { results.set(id, {status: "error", error: `Contour lines unavailable: ${error.message}`}); renderStatus(); }
              }).finally(() => { contourPending = null; });
            }
            continue;
          }
          const provider = id === "ahn" ? lidarProviderForBounds(map.getBounds?.()) : null;
          const source = map.getSource(sourceId(id));
          const desiredTile = id === "ahn" && provider && Number(map.getZoom?.()) >= provider.minzoom ? lidarTileUrl(ahnMode, provider) : isEnvironmentRaster(id) ? environmentTileUrl(id) : null;
          if ((id === "ahn" || isEnvironmentRaster(id)) && source && source.tiles?.[0] !== desiredTile) {
            removeIntelLayers(map, id);
          }
          const rasterReady = id === "ahn" ? Boolean(provider && Number(map.getZoom?.()) >= provider.minzoom) : isEnvironmentRaster(id) ? Number(map.getZoom?.()) >= ENVIRONMENT_MIN_ZOOM[id] : true;
          const needsInstall = id === "ahn" || isEnvironmentRaster(id) ? Boolean(rasterReady && (!map.getSource(sourceId(id)) || layerIds(id).some(layer => !map.getLayer(layer)))) : !CLIENT_RASTER_IDS.has(id) || !map.getSource(sourceId(id)) || layerIds(id).some(layer => !map.getLayer(layer));
          if (needsInstall) installIntelLayers(map, pack, animatedData(context, id, context.data.get(id) || EMPTY()).data, contourUrl, ahnMode, provider);
        }
        catch (error) { results.set(id, {status: "error", error: `Map layer unavailable: ${error.message}`}); }
      }
    }
    updateCredit(context);
    updateTrafficTrack(context);
    updateRoadPopup(context);
    renderStatus();
    trafficAnimator.start();
  }

  function schedule(context, delay = 650) {
    clearTimeout(context.timer);
    // Let public requests finish into the cache even if we pan. A new viewport
    // is queued after completion instead of repeatedly restarting slow feeds.
    context.needsRefresh = true;
    context.timer = setTimeout(() => void refreshMap(context), delay);
  }

  async function refreshMap(context, trafficOnly = false, onlyPack = null) {
    if (destroyed || !isVisible(context) || !hasParsedStyle(context)) return;
    context.needsRefresh = false;
    const map = context.map;
    // The active national source can change while panning between countries.
    // Reconcile it before updating status so MapLibre never keeps the previous
    // country's raster alive under the new viewport.
    syncLayers(context);
    if (enabled.has("elevation") && contourUrl && results.get("elevation")?.status !== "error") {
      const interval = contourInterval(map.getZoom());
      results.set("elevation", interval ? {status: "ready", note: `${interval} m contour interval · labels in metres`} : {status: "zoom_in", note: "Zoom in to see contour lines"});
    }
    if (enabled.has("ahn")) {
      const provider = lidarProviderForBounds(map.getBounds?.());
      const providerMode = provider ? lidarModeForProvider(provider, ahnMode) : "dtm";
      if (!provider) results.set("ahn", {status: "uncovered", note: "No national high-resolution LiDAR source is available for this map area · global contours remain available"});
      else if (map.getZoom() < provider.minzoom) results.set("ahn", {status: "zoom_in", note: `Zoom in to level ${provider.minzoom} to view ${provider.label} LiDAR`});
      else results.set("ahn", {status: "ready", note: `${provider.label} · ${lidarModeLabel(provider, providerMode)} · ${provider.resolution} raster`});
    }
    for (const id of ENVIRONMENT_RASTER_IDS) if (enabled.has(id)) {
      const pack = packById(id);
      if (map.getZoom() < Number(pack?.min_zoom || ENVIRONMENT_MIN_ZOOM[id])) results.set(id, {status: "zoom_in", note: `Zoom in to level ${pack?.min_zoom || ENVIRONMENT_MIN_ZOOM[id]} to view ${ENVIRONMENT_RASTERS[id].title.toLowerCase()}`});
      else results.set(id, {status: "ready", note: `${ENVIRONMENT_RASTERS[id].source} raster · online only`});
    }
    await Promise.all(currentPacks().filter(pack => pack.enabled && !CLIENT_RASTER_IDS.has(pack.id) && !context.requests.has(pack.id) && (!onlyPack || pack.id === onlyPack) && (!trafficOnly || TRAFFIC_IDS.includes(pack.id))).map(async pack => {
      if (pack.configured === false) { results.set(pack.id, {status: "needs_key"}); return; }
      if (pack.id === "gdacs" && !gdacsTypes.length) {
        map.getSource(sourceId(pack.id))?.setData(EMPTY());
        results.set(pack.id, {...EMPTY(), status: "filtered", gdacs_types: []});
        return;
      }
      const traffic = TRAFFIC_IDS.includes(pack.id);
      const belowZoom = map.getZoom() < Number(pack.min_zoom || (traffic ? 6 : 0));
      if (belowZoom && !traffic) {
        // Zoom limits restrict provider downloads, not display of saved areas.
        // The backend can also return disk-cached geometry at this zoom.
        results.set(pack.id, {...context.data.get(pack.id), status: "zoom_in", note: "Showing saved areas. Zoom in to download additional coverage."});
      }
      const requestedBoxes = intelViewportBoxes(map.getBounds());
      const requestedUrls = intelPackUrls(pack.id, map.getBounds(), map.getZoom());
      const viewport = requestedUrls.join("|");
      const regional = traffic && (belowZoom || context.rejectedTrafficViews.get(pack.id) === viewport);
      const urls = (regional ? context.trafficRegions.get(pack.id) || [] : requestedUrls)
        .map((url, index) => traffic ? `${url}&client=${context.trafficClient}_${index}` : url);
      if (regional) results.set(pack.id, {status: "zoom_in", note: "Showing cached traffic · last regional feed stays active. Zoom in to load a new area."});
      if (!urls.length) return;
      if (pack.id === "vessels") context.vesselLeased = true;
      const pending = {abort: new AbortController()};
      context.requests.set(pack.id, pending);
      results.set(pack.id, {status: "loading"});
      renderStatus();
      const timeout = setTimeout(() => pending.abort.abort(), 65000);
      try {
        let parts = await Promise.all(urls.map(url => request(url, {signal: pending.abort.signal})));
        if (destroyed || !enabled.has(pack.id) || context.requests.get(pack.id) !== pending) return;
        if (traffic && parts.some(part => part.status === "zoom_in")) {
          context.rejectedTrafficViews.set(pack.id, viewport);
          const fallback = context.trafficRegions.get(pack.id);
          if (fallback?.length) parts = await Promise.all(fallback.map((url, index) => request(`${url}&client=${context.trafficClient}_${index}`, {signal: pending.abort.signal})));
          if (destroyed || !enabled.has(pack.id) || context.requests.get(pack.id) !== pending) return;
        } else if (traffic && !regional && parts.every(part => ["fresh", "stale", "waiting"].includes(part.status))) {
          context.trafficRegions.set(pack.id, requestedUrls);
          context.rejectedTrafficViews.delete(pack.id);
        }
        let data = mergeIntelResults(parts);
        if (pack.id === "military") {
          data.basemap_fallback = !data.worldwide_snapshot && (!data.features.length || data.coverage_complete === false || data.status === "stale");
          if (data.basemap_fallback && map.getSource("offline")) {
            data.note = ["Showing available military land, airfields and police stations from the basemap.", data.note].filter(Boolean).join(" ");
          }
        }
        const previous = context.data.get(pack.id);
        if (traffic) data = retainTraffic(previous, data);
        else if (pack.id === "roads") data = retainRoads(previous, data);
        else if (!["notams", "transit"].includes(pack.id)) data = retainIntel(previous, data, requestedBoxes);
        results.set(pack.id, visibleData(pack.id, data));
        context.data.set(pack.id, data);
        if (pack.id === "transit") updateCredit(context);
        context.tracks.get(pack.id)?.ingest(data);
        if (hasParsedStyle(context)) installIntelLayers(map, pack, animatedData(context, pack.id, data).data);
        if (pack.id === "notams" && context.popupPack === pack.id) {
          const current = results.get(pack.id);
          const ids = new Set(current.features.map(feature => feature.id));
          if (current.status === "stale" || context.selectedNotams.some(id => !ids.has(id))) context.popup?.remove();
        }
        if (pack.id === "roads") updateRoadPopup(context);
        trafficAnimator.start();
      } catch (error) {
        if (destroyed || !enabled.has(pack.id) || context.requests.get(pack.id) !== pending) return;
        let old = context.data.get(pack.id);
        const failure = error.name === "AbortError" ? "Source request timed out. Try again later." : error.message;
        if (pack.id === "roads" && old) {
          old = roadDisplayData({...old, features: old.features.map(feature => ({...feature, properties: {...feature.properties, stale: true}}))});
          context.data.set(pack.id, old);
          map.getSource(sourceId(pack.id))?.setData(visibleData(pack.id, old));
          updateRoadPopup(context);
        }
        if (pack.id === "notams") {
          old = old && {...old, status: "stale", error: failure};
          if (old) context.data.set(pack.id, old);
          map.getSource(sourceId(pack.id))?.setData(visibleData(pack.id, old || EMPTY()));
          if (context.popupPack === pack.id) context.popup?.remove();
        }
        if (pack.id === "transit") map.getSource(sourceId(pack.id))?.setData(visibleData(pack.id, old || EMPTY()));
        results.set(pack.id, visibleData(pack.id, old?.features?.length ? {...old, status: "stale", error: failure} : {status: "error", error: failure}));
      } finally {
        clearTimeout(timeout);
        if (context.requests.get(pack.id) === pending) {
          context.requests.delete(pack.id);
          const moved = intelPackUrls(pack.id, map.getBounds(), map.getZoom()).join("|") !== viewport;
          if (!destroyed && enabled.has(pack.id) && (moved || context.needsRefresh)) schedule(context, 0);
        }
        renderStatus();
      }
    }));
    updateTrafficTrack(context);
    renderStatus();
  }


  function trafficDetailFeature(context, feature, packId, additions = {}) {
    const identity = String(feature?.properties?.identity || "").toLowerCase();
    const profile = TRAFFIC_IDS.includes(packId) ? context.trafficProfiles.get(identity) : null;
    return {...feature, properties: {...feature.properties, ...(profile || {}), ...additions}};
  }

  function updateRoadPopup(context) {
    if (!context.selectedRoad) return;
    if (!enabled.has("roads")) { context.popup?.remove(); return; }
    const feature = visibleData("roads", context.data.get("roads")).features.find(f => f.id === context.selectedRoad);
    if (!feature) { context.popup?.remove(); return; }
    context.popup?.setHTML(intelFeatureHtml(feature, packById("roads"), results.get("roads")));
  }

  async function loadTrafficProfile(context, pack, identity) {
    identity = String(identity || "").toLowerCase();
    const flights = pack === "flights";
    if (!(flights ? /^[0-9a-f]{6}$/ : /^[0-9]{9}$/).test(identity) || context.trafficProfiles.has(identity)) return;
    // Mark in-flight/unknown lookups so opening the same card never hammers a
    // public metadata provider while a map is refreshing.
    if (context.trafficProfiles.size >= 512) context.trafficProfiles.delete(context.trafficProfiles.keys().next().value);
    context.trafficProfiles.set(identity, null);
    try {
      const path = `/api/intel-packs/live/${pack}/${identity}`;
      const profile = await request(`${path}/${flights ? "profile" : "photo"}`);
      if (destroyed || !profile) return;
      context.trafficProfiles.set(identity, flights ? {
        aircraft_model: profile.model, aircraft_manufacturer: profile.manufacturer,
        aircraft_type_code: profile.type_code, aircraft_registration: profile.registration,
        aircraft_owner: profile.owner,
        aircraft_photo_url: profile.photo_available ? `${path}/thumbnail` : "",
        aircraft_photo_source: profile.photo_source_url,
      } : {
        vessel_photo_url: `${path}/thumbnail/${profile.number}`,
        vessel_photo_credit: profile.credit, vessel_photo_source: profile.source_url,
      });
    } catch {
      // Public enrichment is optional. The signed Reticom picture and live
      // traffic view remain useful when this secondary provider is unavailable.
      context.trafficProfiles.set(identity, null);
    }
    if (context.selectedTraffic?.pack === pack && context.selectedTraffic.key === identity) updateTrafficTrack(context);
  }

  function followTraffic(context, feature) {
    const {map, selectedTraffic: selected} = context;
    if (!selected?.following || !feature || map.isMoving?.() || !isVisible(context)) return;
    const [lng, lat] = feature.geometry.coordinates;
    const current = map.getCenter();
    const center = [lng + 360 * Math.round((current.lng - lng) / 360), lat];
    const offset = map.project(center).dist(map.project(current));
    if (offset >= 6 || (selected.recenter && offset > 0)) map.jumpTo({center}, {trafficFollow: true});
    selected.recenter = false;
  }

  function closeTrafficDrawer(context) {
    const selected = context.selectedTraffic;
    if (!selected) return;
    context.selectedTraffic = null;
    if (context.drawer) {
      if (context.drawer.contains(context.drawer.ownerDocument.activeElement)) context.map.getCanvas?.()?.focus({preventScroll: true});
      context.drawer.hidden = true;
      context.drawerBody.innerHTML = "";
    }
    clearTrafficTrack(context.map);
    const data = context.data.get(selected.pack);
    if (data) context.map.getSource(sourceId(selected.pack))?.setData(animatedData(context, selected.pack, data).data);
  }

  function updateTrafficTrack(context) {
    if (!hasParsedStyle(context)) return;
    const selected = context.selectedTraffic;
    if (!selected) { clearTrafficTrack(context.map); return; }
    const pack = packById(selected.pack);
    const visible = animatedData(context, selected.pack, context.data.get(selected.pack) || EMPTY()).data.features;
    const feature = enabled.has(selected.pack) && pack?.configured !== false
      ? visible.find(item => trafficKey(item) === selected.key) : null;
    if (!feature) {
      closeTrafficDrawer(context);
      return;
    }
    const route = context.tracks.get(selected.pack).route(selected.key, Date.now()/1000, selected.history);
    context.map.getSource(sourceId(selected.pack))?.setData({type: "FeatureCollection", features: visible});
    showTrafficTrack(context.map, route.data);
    showTrafficEstimate(context.map, feature);
    if (context.drawer) {
      context.drawer.hidden = false;
      context.followButton.textContent = selected.following ? "Pause follow" : "Resume follow";
      replaceTrafficDetails(context.drawerBody, intelFeatureHtml(trafficDetailFeature(context, feature, selected.pack, {track_label: route.label}), pack, results.get(selected.pack)));
    }
    followTraffic(context, feature);
    if (selected.history && Date.now() - selected.historyAt > 60000) void loadTrafficRoute(context, selected);
  }

  async function loadTrafficRoute(context, selected) {
    selected.historyAt = Date.now();
    try {
      const history = await request(`/api/intel-packs/live/${selected.pack}/${encodeURIComponent(selected.key)}/route`);
      if (!Array.isArray(history.points)) throw new Error("Invalid route history");
      if (context.selectedTraffic !== selected) return;
      if (history.points.length || !selected.history?.points?.length) selected.history = history;
      updateTrafficTrack(context);
    } catch {
      if (context.selectedTraffic === selected) {
        selected.history ||= {points:[],note:"History unavailable · showing locally observed positions"};
        updateTrafficTrack(context);
      }
    }
  }

  function attachMap(map, {Popup} = {}) {
    if (maps.has(map)) { if (Popup) maps.get(map).Popup = Popup; return; }
    const context = {map, Popup, motion: new Map(TRAFFIC_IDS.map(id => [id, new TrafficMotion(id)])), tracks: new Map(TRAFFIC_IDS.map(id => [id, new TrafficTracks(id)])), selectedTraffic: null, trafficProfiles: new Map(), trafficRegions: new Map(), rejectedTrafficViews: new Map(), trafficClient: globalThis.crypto?.randomUUID?.() || `map-${Math.random().toString(36).slice(2)}`, requests: new Map(), data: new Map(), timer: null, popup: null, credit: null, needsRefresh: true};
    maps.set(map, context);
    const container = map.getContainer?.();
    if (container?.ownerDocument) {
      context.credit = container.ownerDocument.createElement("div");
      context.credit.className = "intel-map-credit";
      context.credit.hidden = true;
      container.append(context.credit);
      context.transitCredit = container.ownerDocument.createElement("small");
      context.transitCredit.className = "intel-transit-credit";
      context.transitCredit.hidden = true;
      container.append(context.transitCredit);
      context.credit.addEventListener("pointerdown", event => event.stopPropagation());
      context.drawer = container.ownerDocument.createElement("aside");
      context.drawer.className = "intel-traffic-drawer";
      context.drawer.setAttribute("aria-label", "Aircraft and vessel details");
      context.drawer.hidden = true;
      context.drawer.innerHTML = `<div class="intel-traffic-drawer-body"></div><div class="intel-traffic-drawer-actions"><button type="button" data-traffic-follow>Pause follow</button><button type="button" data-traffic-close aria-label="Close traffic details">×</button></div>`;
      context.drawerBody = context.drawer.querySelector(".intel-traffic-drawer-body");
      context.followButton = context.drawer.querySelector("[data-traffic-follow]");
      context.followButton.addEventListener("click", () => {
        const selected = context.selectedTraffic;
        if (!selected) return;
        selected.following = !selected.following;
        selected.recenter = selected.following;
        updateTrafficTrack(context);
      });
      context.drawer.querySelector("[data-traffic-close]").addEventListener("click", () => closeTrafficDrawer(context));
      context.drawer.addEventListener("keydown", event => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        closeTrafficDrawer(context);
      });
      container.append(context.drawer);
    }
    context.onStyle = () => { invalidate(context); syncLayers(context); schedule(context, 0); };
    context.onMove = event => { if (!event?.trafficFollow) schedule(context); };
    context.onDrag = () => {
      if (!context.selectedTraffic?.following) return;
      context.selectedTraffic.following = false;
      if (context.followButton) context.followButton.textContent = "Resume follow";
    };
    // A map may attach before its style is parsed. Idle is only a fallback for that
    // initial case; continuously streaming team data must not block public requests.
    context.onIdle = () => {
      if (context.needsRefresh && !context.requests.size && isVisible(context)) { syncLayers(context); schedule(context, 0); }
    };
    context.onError = event => {
      if (enabled.has("elevation") && (event.sourceId === sourceId("elevation") || event.error?.message?.includes("/api/intel-packs/elevation/"))) {
        results.set("elevation", {status: "error", error: "Terrain tiles unavailable here. Cached tiles may still be visible."});
        renderStatus();
      }
      if (enabled.has("ahn") && (event.sourceId === sourceId("ahn") || event.error?.message?.includes("service.pdok.nl/rws/ahn/") || event.error?.message?.includes("environment.data.gov.uk/spatialdata/") || event.error?.message?.includes("elevation.nationalmap.gov/arcgis/"))) {
        results.set("ahn", {status: "error", error: "National LiDAR tiles unavailable here. Try again later or use the global contour layer."});
        renderStatus();
      }
      for (const id of ENVIRONMENT_RASTER_IDS) if (enabled.has(id) && event.sourceId === sourceId(id)) {
        results.set(id, {status: "error", error: `${ENVIRONMENT_RASTERS[id].title} tiles unavailable here. Try again later.`});
        renderStatus();
      }
    };
    context.onClick = event => {
      if (event.originalEvent?.target?.closest?.(".maplibregl-marker,.maplibregl-popup,.intel-traffic-drawer,button,a")) return;
      context.popup?.remove(); context.popup = null;
      context.selectedRoad = null;
      const features = map.queryRenderedFeatures(event.point);
      if (features.some(feature => isTeamLayer(feature.layer))) return;
      let feature = features.find(item => item.layer?.id?.startsWith(PREFIX) && !item.layer.id.startsWith(`${PREFIX}elevation-`));
      if (!feature) return;
      const id = IDS.find(id => feature.layer.id.startsWith(`${PREFIX}${id}-`));
      const pack = packById(id);
      if (!pack || !enabled.has(id)) return;
      if (TRAFFIC_IDS.includes(id)) {
        closeTrafficDrawer(context);
        const key = trafficKey(feature);
        const observed = context.data.get(id)?.features.find(item => trafficKey(item) === key);
        // Rendered IDs may be MapLibre IDs and coordinates may be predicted.
        const coordinates = [feature.properties.reported_lon, feature.properties.reported_lat];
        const original = observed || {...feature, geometry: {...feature.geometry, coordinates: coordinates.every(Number.isFinite) ? coordinates : feature.geometry.coordinates}};
        const selected = {pack: id, key, feature: original, following: true, recenter: true};
        context.selectedTraffic = selected;
        updateTrafficTrack(context);
        if (context.selectedTraffic === selected) {
          void loadTrafficRoute(context, selected);
          void loadTrafficProfile(context, id, feature.properties?.identity);
        }
        return;
      }
      if (!context.Popup) return;
      closeTrafficDrawer(context);
      if (feature.layer.id.startsWith(MILITARY_BASEMAP)) {
        const label = feature.layer.id.endsWith("-police") ? "Police station"
          : feature.layer.id.endsWith("-airfield") ? ({airstrip: "Airstrip", runway: "Runway"}[feature.properties?.class] || "Airfield") : "Military area";
        feature = {...feature, properties: {...feature.properties, title: feature.properties?.name || feature.properties?.ref || label, detail: label,
          source: "OpenFreeMap / OpenStreetMap", source_url: "https://openfreemap.org/", attribution: "OpenFreeMap · © OpenStreetMap contributors"}};
      }
      const result = results.get(id);
      let html = intelFeatureHtml(feature, pack, result);
      if (id === "notams") {
        const hits = new Set(features.filter(hit => hit.layer?.source === sourceId(id))
          .map(hit => hit.properties?.id || hit.id));
        hits.add(feature.properties?.id || feature.id);
        const notices = visibleData(id, context.data.get(id) || EMPTY()).features.filter(notice => hits.has(notice.id));
        if (!notices.length) return;
        context.selectedNotams = notices.map(notice => notice.id);
        if (notices.length === 1) html = intelFeatureHtml(notices[0], pack, result);
        else html = `<div class="intel-feature-popup"><strong>${notices.length} notices here</strong>${notices.map(notice =>
          `<details class="intel-notam-overlap"><summary>${escape([notice.properties.notice_number, notice.properties.category_label, notice.properties.title !== notice.properties.notice_number ? notice.properties.title : ""].filter(Boolean).join(" · "))}</summary>${intelFeatureHtml(notice, pack, result)}</details>`).join("")}</div>`;
      }
      context.popup = new context.Popup({maxWidth: "320px", closeButton: true, className: "public-intel-popup"}).setLngLat(event.lngLat).setHTML(html).addTo(map);
      context.popupPack = id;
      const popup = context.popup;
      popup.on?.("close", () => {
        if (context.popup !== popup) return;
        context.popup = null;
        context.popupPack = null;
        context.selectedRoad = null;
      });
      if (id === "roads") context.selectedRoad = feature.properties?.road_id || feature.id;
    };
    context.onRemove = () => detachMap(map);
    map.on("style.load", context.onStyle);
    map.on("load", context.onStyle);
    map.on("moveend", context.onMove);
    map.on("dragstart", context.onDrag);
    map.on("idle", context.onIdle);
    map.on("error", context.onError);
    map.on("click", context.onClick);
    map.on("remove", context.onRemove);
    syncLayers(context);
    schedule(context, 0);
  }

  function detachMap(map) {
    const context = maps.get(map);
    if (!context) return;
    clearTimeout(context.timer); invalidate(context); context.popup?.remove(); context.credit?.remove(); context.transitCredit?.remove(); context.drawer?.remove();
    clearTrafficTrack(map);
    releaseTraffic(context);
    for (const [event, handler] of [["style.load", context.onStyle], ["load", context.onStyle], ["moveend", context.onMove], ["dragstart", context.onDrag], ["idle", context.onIdle], ["error", context.onError], ["click", context.onClick], ["remove", context.onRemove]]) map.off(event, handler);
    maps.delete(map);
    for (const model of context.motion.values()) model.clear();
    for (const model of context.tracks.values()) model.clear();
    if (!maps.size) trafficAnimator.stop();
  }

  function releaseTraffic(context) {
    if (!context.vesselLeased) return;
    context.vesselLeased = false;
    for (let index = 0; index < 2; index++) void fetchImpl(`/api/intel-packs/live/vessels/lease/${context.trafficClient}_${index}`, {method: "DELETE", keepalive: true}).catch(() => {});
  }

  function setMapPaused(map, paused) {
    const context = maps.get(map);
    if (!context || Boolean(context.paused) === paused) return;
    context.paused = paused;
    if (paused) {
      clearTimeout(context.timer);
      invalidate(context);
      releaseTraffic(context);
    } else schedule(context, 0);
  }

  buildPanel();
  if (button) { button.setAttribute("aria-expanded", "false"); if (panel?.id) button.setAttribute("aria-controls", panel.id); }
  listen(button, "click", () => setOpen(Boolean(panel?.hidden || panel?.classList.contains("hidden"))));
  listen(panel?.ownerDocument, "keydown", event => { if (event.key === "Escape" && panel && !panel.hidden && !panel.classList.contains("hidden")) { event.stopPropagation(); setOpen(false); } });
  if (typeof window !== "undefined") listen(window, "online", () => { if (enabled.has("elevation")) void refresh(); });
  const interval = setInterval(() => { for (const context of maps.values()) if (isVisible(context) && !context.requests.size) schedule(context, 0); }, 60000);
  const transitInterval = setInterval(() => {
    if (enabled.has("transit")) for (const context of maps.values()) if (isVisible(context) && !context.needsRefresh) void refreshMap(context, false, "transit");
  }, 30000);
  const trafficInterval = setInterval(() => {
    if (enabled.has("notams")) for (const context of maps.values()) {
      const source = context.map.getSource(sourceId("notams")), original = context.data.get("notams");
      if (!source || !original || !isVisible(context)) continue;
      const frame = visibleData("notams", original);
      const signature = JSON.stringify(frame.features.map(feature => [feature.id, feature.properties.notam_phase, feature.properties.notam_start]));
      if (context.notamFrame === signature) continue;
      context.notamFrame = signature;
      source.setData(frame);
      if (context.popupPack === "notams" && context.selectedNotams.some(id => !frame.features.some(feature => feature.id === id))) context.popup?.remove();
    }
    for (const id of SHARED_LAYERS) if (enabled.has(id) && results.get(id)?.waiting_for_host) {
      for (const context of maps.values()) if (isVisible(context) && !context.needsRefresh && !context.requests.has(id)) void refreshMap(context, false, id);
    }
    if (!enabled.has("roads") && !TRAFFIC_IDS.some(id => enabled.has(id))) return;
    for (const context of maps.values()) {
      if (!isVisible(context)) { releaseTraffic(context); continue; }
      if (enabled.has("roads") && context.data.has("roads")) {
        const data = roadDisplayData(context.data.get("roads"));
        context.data.set("roads", data);
        context.map.getSource(sourceId("roads"))?.setData(visibleData("roads", data));
        updateRoadPopup(context);
      }
      for (const id of TRAFFIC_IDS) if (enabled.has(id) && context.data.has(id)) {
        const data = visibleData(id, context.data.get(id));
        context.map.getSource(sourceId(id))?.setData(animatedData(context, id, context.data.get(id)).data);
        results.set(id, data);
      }
      updateTrafficTrack(context);
      void refreshMap(context, true);
    }
    trafficAnimator.start();
  }, 5000);
  listen(globalThis.document, "visibilitychange", () => {
    trafficAnimator.stop();
    for (const context of maps.values()) {
      if (!isVisible(context)) { invalidate(context); releaseTraffic(context); }
      else schedule(context, 0);
    }
    trafficAnimator.start();
  });
  const ready = refresh();
  return {ready, attachMap, setMapPaused, refresh, setEnabled, setRoadFilters, setFirmsDays, setGdacsTypes, setNotamCategories, setNotamView, setTrafficFilters, updateCredentials, getState: () => ({packs: currentPacks(), settings: {road_filters: roadFilters, firms_days: firmsDays, gdacs_types: [...gdacsTypes], notam_categories: [...notamCategories], notam_view: notamView, traffic_filters: trafficFilters, ahn_mode: ahnMode}, results: Object.fromEntries(results)}), destroy() {
    destroyed = true; ++catalogueGeneration; clearInterval(interval); clearInterval(trafficInterval); clearInterval(transitInterval);
    for (const timer of autosaveTimers.values()) clearTimeout(timer);
    trafficAnimator.destroy();
    for (const context of maps.values()) { if (hasParsedStyle(context)) for (const id of IDS) removeIntelLayers(context.map, id); detachMap(context.map); }
    for (const cleanup of listeners) cleanup();
  }};
}
