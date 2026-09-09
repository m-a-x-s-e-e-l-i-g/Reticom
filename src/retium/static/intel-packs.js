// Public map context is device-local: never insert it into the signed team feed.
import {ROAD_COLOR} from "./outdoor-map.js?v=20260907-5";
import {GDACS_TYPES, GDACS_COLOR, GDACS_ICON, gdacsCode, gdacsIconSvg, gdacsDisplayData, normalizeGdacsTypes, installGdacsIcons} from "./gdacs-markers.js?v=20260907-1";
import {prepareContourTiles, contourLayers, contourInterval, CONTOUR_MIN_ZOOM, CONTOUR_MAX_ZOOM} from "./terrain-contours.js?v=20260907-3";
import {TRAFFIC_IDS, TRAFFIC_TYPES, normalizeTrafficFilters, trafficDisplayData, installTrafficIcons, trafficLayers} from "./traffic-map.js?v=20260909-3";
import {TrafficMotion, createTrafficAnimator} from "./traffic-motion.js?v=20260909-3";
import {TrafficTracks, trafficKey, showTrafficTrack, clearTrafficTrack} from "./traffic-tracks.js?v=20260909-3";
import {retainTraffic} from "./traffic-cache.js?v=20260909-3";
import {retainIntel} from "./intel-cache.js?v=20260909-4";
const EMPTY = () => ({type: "FeatureCollection", features: []});
// Trails are always-on basemap data, not a separately fetched/toggled intel pack.
const IDS = ["military", "acled", "firms", "gdacs", "elevation", ...TRAFFIC_IDS];
const COLORS = {trails: "#8f9c77", military: "#ad9774", acled: "#ba8077", firms: "#c49367", gdacs: "#b6a578"};
const PREFIX = "public-intel-";
const ACCESS = {
  firms_key: {pack: "firms", title: "NASA FIRMS map key", url: "https://firms.modaps.eosdis.nasa.gov/api/map_key/", link: "Get a NASA FIRMS map key"},
  acled_token: {pack: "acled", title: "ACLED access token", url: "https://acleddata.com/api-documentation/getting-started", link: "Account and token instructions"},
  aisstream_key: {pack: "vessels", title: "AISStream API key", url: "https://aisstream.io/", link: "Create an AISStream key / check access terms"},
};
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
const sourceId = id => `${PREFIX}${id}`;
const layerIds = id => (TRAFFIC_IDS.includes(id) ? ["points", "icons", "labels"] : id === "elevation" ? ["lines", "labels", "minor-labels"] : id === "gdacs" ? ["areas", "lines", "tracks", "points", "icons", "labels"] : id === "trails" ? ["areas", "lines", "points", "labels", "route-labels"] : ["areas", "lines", "points", "labels"]).map(kind => `${PREFIX}${id}-${kind}`);
const safeLink = value => {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : ""; } catch { return ""; }
};
const safeAircraftThumbnail = value => /^\/api\/intel-packs\/live\/flights\/[0-9a-f]{6}\/thumbnail$/.test(String(value || "")) ? value : "";
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
  if (!IDS.includes(id) || id === "elevation" || !Number.isFinite(Number(zoom))) return [];
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
  const timestamps = results.map(result => result.fetched_at).filter(Boolean);
  return {...results[0], ...EMPTY(), features,
    status: warning ? (features.length ? "stale" : warning.status) : results.every(result => result.status === "cached") ? "cached" : "fresh",
    capped: results.some(result => result.capped), truncated: results.some(result => result.truncated),
    ...(results.some(result => result.coverage_complete !== undefined) ? {coverage_complete: results.every(result => result.coverage_complete !== false)} : {}),
    fetched_at: timestamps.length ? timestamps.sort((a, b) => new Date(Number(a) * 1000 || a) - new Date(Number(b) * 1000 || b))[0] : undefined,
    ...(warning ? {error: warning.error, note: warning.note || (results.length > 1 ? "Some of this view could not be refreshed." : undefined)} : {}),
  };
}

function stamp(value) {
  if (!value) return "";
  const date = new Date(typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
}

export function intelStatusText(pack, result) {
  if (!pack.enabled) return "Off";
  if (pack.configured === false || result?.status === "needs_key") return "Source access needed below";
  if (!result) return pack.id === "elevation" ? "Contour lines · heights in metres" : "Waiting for a map view";
  if (result.status === "loading") return "Loading this map view…";
  if (result.status === "zoom_in") return result.note || `Zoom in to level ${pack.min_zoom || 10} to load this pack`;
  if (result.status === "error") return result.error || "Source unavailable · try again later";
  if (result.status === "disabled") return "Off";
  if (TRAFFIC_IDS.includes(pack.id)) {
    if (result.status === "waiting") return result.note || "Waiting for live reports…";
    const count = result.features?.length || 0;
    return `${count} ${pack.id === "flights" ? "aircraft" : "vessels"} shown${result.status === "stale" ? " · stale" : ""}${result.capped ? " · display cap" : ""}${result.total_received > count ? ` / ${result.total_received} received` : ""}`;
  }
  if (pack.id === "gdacs" && result.gdacs_types?.length === 0) return "No disaster types selected";
  if (pack.id === "elevation") return result.note || "Contour lines · heights in metres";
  const count = result.features?.length || 0;
  const amount = count ? `${count.toLocaleString()} public feature${count === 1 ? "" : "s"}` : result.coverage_complete === false ? "No saved features · incomplete coverage" : "No public features in this view";
  const state = result.status === "stale" ? "stale cache" : result.status === "cached" ? "cached" : "fetched";
  return `${amount} · ${state}${stamp(result.fetched_at) ? ` ${stamp(result.fetched_at)}` : ""}${count && result.coverage_complete === false ? " · partial coverage" : ""}${result.capped || result.truncated ? " · result limit reached" : ""}`;
}

// The layer picker is configuration, not a live telemetry dashboard. Routine
// refreshes must not insert/remove lines as counts and cache timestamps change.
export function intelLayerWarning(pack, result) {
  if (!pack.enabled) return "";
  if (pack.configured === false || result?.status === "needs_key") return "Source access needed below";
  if (result?.status === "error") return result.error || "Source unavailable · try again later";
  if (result?.status === "stale") return result.error || "Source unavailable · showing saved data";
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
      ["Track", props.track_label], ["Prediction", props.prediction_seconds > 0 ? `${Math.round(props.prediction_seconds)} seconds beyond report` : null],
      ["Category", TRAFFIC_TYPES[pack.id][props.traffic_type] || "Unknown"], ["ICAO type", props.aircraft_type_code || props.type_code || props.ship_type_code || "Unknown"],
      [pack.id === "flights" ? "ICAO address" : "MMSI", props.identity], ["Registration", props.aircraft_registration || props.registration], ["Callsign", props.callsign],
      ["Speed", props.speed_knots == null ? "Unknown" : `${Number(props.speed_knots).toFixed(1)} kn`], ["Direction", props.heading == null ? "Unknown" : `${Math.round(props.heading)}°`],
      ...(pack.id === "flights" ? [["Altitude", props.altitude_m == null ? props.altitude_reference : `${props.altitude_m} m · barometric`], ["Registered owner", props.aircraft_owner]] : [["Destination", props.destination]])];
    const summary = [[props.aircraft_registration || props.registration || (pack.id === "flights" ? "Registration unknown" : null), "registration"], [altitude, "altitude"], [speed, "speed"], [age, "age"]]
      .filter(([value]) => value).map(([value, label]) => `<span title="${escape(label)}">${escape(value)}</span>`).join("");
    const photoSource = safeLink(props.aircraft_photo_source);
    return `<article class="intel-feature-popup intel-traffic-popup"><small>PUBLIC ${pack.id === "flights" ? "AIRCRAFT" : "VESSEL"} · ${escape(pack.source)}</small><div class="intel-traffic-summary">${thumbnail ? `<img class="intel-aircraft-photo" src="${escape(thumbnail)}" alt="Public photo of ${escape(type || title)}" loading="lazy">` : ""}<div><strong>${escape(title)}</strong><p class="intel-traffic-type">${escape(type)}</p><div class="intel-traffic-facts">${summary}</div></div></div><details class="intel-traffic-details"><summary>${pack.id === "flights" ? "Flight details" : "Vessel details"}</summary><dl>${fields.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("")}</dl><p>${escape(props.classification_note)}</p><p class="intel-feature-caution">Coverage and classifications are incomplete. Reports may be delayed or incorrect. Not a verified team report or a navigation/collision-avoidance service.${props.stale ? " This position is stale." : ""}</p></details>${props.aircraft_model ? `<small>Aircraft details · ADSBDB public database</small>` : ""}${photoSource ? `<a href="${escape(photoSource)}" target="_blank" rel="noopener noreferrer">Photo source ↗</a>` : ""}${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(pack.attribution)} ↗</a>` : ""}</article>`;
  }
  const fields = [["Reported", observed], ["Type", props.disaster_type || String(props.kind || "").replaceAll("_", " ")], ["Area / vector", props.area_label], ["GDACS level", pack.id === "gdacs" ? props.severity : null], ["Access", props.access], ["Confidence", props.confidence], ["Location precision", props.precision === 1 ? "Reported location" : props.precision === 2 ? "Approximate location" : props.precision === 3 ? "Regional location" : props.precision]];
  return `<article class="intel-feature-popup"><small>PUBLIC SOURCE · ${escape(pack.source || props.source || pack.title)}</small><strong>${pack.id === "gdacs" ? gdacsIconSvg(gdacsCode(props)) : ""}${escape(title)}</strong>${detail ? `<p>${escape(detail)}</p>` : ""}<dl>${fields.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("")}</dl><p class="intel-feature-caution">${pack.id === "gdacs" ? "Provider areas may be reported extents, forecast zones or reference buffers; check the area label. No outline does not mean no impact. " : ""}${pack.id === "firms" ? "Satellite thermal detection, not a confirmed fire. " : ""}${pack.id === "military" ? "Public map data. Boundaries and access may be incomplete or outdated. " : ""}${pack.id === "trails" ? "A mapped trail is not proof of safe or permitted access. " : ""}Not a verified team report.${result?.status === "stale" ? " Showing stale cached data." : ""}</p>${source ? `<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">${escape(props.attribution || pack.attribution || "View source")} ↗</a>` : ""}${result?.fetched_at ? `<small>Fetched ${escape(stamp(result.fetched_at))}</small>` : ""}</article>`;
}

// Live traffic refreshes several times per minute (and aircraft profiles can
// arrive just after the card opens). Replacing popup HTML must retain an
// operator's expanded details instead of snapping the disclosure shut.
export function replaceTrafficPopup(popup, html) {
  const wasOpen = Boolean(popup?.getElement?.()?.querySelector?.(".intel-traffic-details")?.open);
  popup?.setHTML(html);
  const details = popup?.getElement?.()?.querySelector?.(".intel-traffic-details");
  if (details) details.open = wasOpen;
}

// MapLibre keeps the same camera; adding/removing public layers never calls fitBounds.
export function installIntelLayers(map, pack, data = EMPTY(), contourUrl = null) {
  const id = sourceId(pack.id);
  const layers = map.getStyle?.()?.layers || [];
  const before = layers.find(layer => isTeamLayer(layer) || (layer.type === "symbol" && !layer.id.startsWith(PREFIX)))?.id;
  if (pack.id === "elevation") {
    if (!contourUrl) return;
    if (!map.getSource(id)) map.addSource(id, {type: "vector", tiles: [contourUrl], minzoom: CONTOUR_MIN_ZOOM, maxzoom: CONTOUR_MAX_ZOOM});
    for (const layer of contourLayers(id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  if (pack.id === "gdacs") { installGdacsIcons(map); data = gdacsDisplayData(data); }
  if (!map.getSource(id)) map.addSource(id, {type: "geojson", data});
  else map.getSource(id).setData(data);
  if (TRAFFIC_IDS.includes(pack.id)) {
    installTrafficIcons(map);
    for (const layer of trafficLayers(id)) if (!map.getLayer(layer.id)) map.addLayer(layer, before);
    return;
  }
  const color = pack.id === "gdacs" ? GDACS_COLOR : COLORS[pack.id] || COLORS.gdacs;
  const definitions = [
    {id: `${id}-areas`, type: "fill", filter: ["==", ["geometry-type"], "Polygon"], paint: {"fill-color": color, "fill-opacity": .12}},
    {id: `${id}-lines`, type: "line", filter: pack.id === "gdacs" ? ["==", ["geometry-type"], "Polygon"] : ["!=", ["geometry-type"], "Point"], layout: {"line-join": "round", "line-cap": pack.id === "trails" ? "butt" : "round"}, paint: {"line-color": pack.id === "trails" ? ROAD_COLOR : color, "line-width": pack.id === "trails" ? 2 : 1.5, "line-opacity": pack.id === "trails" ? .82 : .8, ...(pack.id === "trails" || pack.id === "military" ? {"line-dasharray": [3, 2]} : {})}},
    ...(pack.id === "gdacs" ? [{id: `${id}-tracks`, type: "line", filter: ["==", ["geometry-type"], "LineString"], paint: {"line-color": color, "line-width": 2, "line-dasharray": [3, 2], "line-opacity": .85}}] : []),
    {id: `${id}-points`, type: "circle", filter: ["==", ["geometry-type"], "Point"], paint: {"circle-radius": pack.id === "gdacs" ? 13 : pack.id === "firms" ? 4 : 6, "circle-color": pack.id === "gdacs" ? "#10160f" : color, "circle-opacity": pack.id === "gdacs" ? .95 : .7, "circle-stroke-color": pack.id === "gdacs" ? color : "#1a1d15", "circle-stroke-width": 1.5}},
    ...(pack.id === "gdacs" ? [{id: `${id}-icons`, type: "symbol", filter: ["==", ["geometry-type"], "Point"], layout: {"icon-image": GDACS_ICON, "icon-size": .82, "icon-allow-overlap": true, "icon-ignore-placement": true}}] : []),
    {id: `${id}-labels`, type: "symbol", minzoom: pack.id === "trails" ? 13 : 9, filter: ["==", ["geometry-type"], "Point"], layout: {"text-field": ["coalesce", ["get", "title"], ["get", "name"], ""], "text-size": 10, "text-font": ["Noto Sans Regular"], "text-anchor": "top", "text-offset": [0, 1.1], "text-max-width": 16}, paint: {"text-color": color, "text-halo-color": "#0e130e", "text-halo-width": 1.5}},
  ];
  if (pack.id === "trails") definitions.push({id: `${id}-route-labels`, type: "symbol", minzoom: 12,
    filter: ["==", ["geometry-type"], "LineString"], layout: {"symbol-placement": "line", "text-field": ["coalesce", ["get", "title"], ["get", "name"], ""], "text-font": ["Noto Sans Regular"], "text-size": 11},
    paint: {"text-color": COLORS.trails, "text-halo-color": "#0e130e", "text-halo-width": 1.5}});
  for (const layer of definitions) if (!map.getLayer(layer.id)) map.addLayer({...layer, source: id}, before);
}

export function removeIntelLayers(map, id) {
  for (const layer of layerIds(id).reverse()) if (map.getLayer(layer)) map.removeLayer(layer);
  if (map.getSource(sourceId(id))) map.removeSource(sourceId(id));
}

export function createIntelPacks({button = null, panel = null, fetchImpl = globalThis.fetch?.bind(globalThis), getMapStyle = () => "hiking", setMapStyle = null, prepareContours = prepareContourTiles} = {}) {
  let catalogue = {packs: [], credentials: {}}, enabled = new Set(), gdacsTypes = normalizeGdacsTypes(), firmsDays = 3, saving = false, destroyed = false, catalogueGeneration = 0, notice = "";
  let contourUrl = null, contourPending = null, contourFailed = false;
  let trafficFilters = normalizeTrafficFilters();
  const maps = new Map(), results = new Map(), credentialDrafts = new Map(), trafficDrafts = new Map(), autosaveTimers = new Map();
  const listeners = [];
  const listen = (target, event, handler) => { target?.addEventListener(event, handler); listeners.push(() => target?.removeEventListener(event, handler)); };
  const packById = id => catalogue.packs.find(pack => pack.id === id);
  const disasterTypes = () => {
    const types = packById("gdacs")?.disaster_types?.filter(type => GDACS_TYPES.some(known => known.id === type.id));
    return types?.length ? types : GDACS_TYPES;
  };
  const visibleData = (id, data) => TRAFFIC_IDS.includes(id) ? trafficDisplayData(id, data, trafficFilters[id]) : id === "gdacs" ? {...gdacsDisplayData(data, gdacsTypes), gdacs_types: [...gdacsTypes]} : data;
  const currentPacks = () => catalogue.packs.map(pack => ({...pack, enabled: enabled.has(pack.id)}));
  const isVisible = context => globalThis.document?.visibilityState !== "hidden" && (!context.map.getContainer?.()?.getClientRects || context.map.getContainer().getClientRects().length > 0);
  const activeMap = () => [...maps.values()].reverse().find(isVisible);
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
        {...selected.feature, properties: {...selected.feature.properties, traffic_selected: true}}]};
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
    panel.setAttribute("aria-label", "Public intel packs");
    panel.innerHTML = `<header class="intel-packs-head"><div><span class="intel-packs-eyebrow">MAP CONTEXT</span><h2>Intel packs</h2></div><button type="button" data-intel-close aria-label="Close intel packs">×</button></header><p class="intel-packs-intro">Optional public layers. Separate from your team's reports.</p><p class="intel-packs-privacy">Enabling a pack sends the viewed map area to its provider over the Internet. Choices and access keys stay on this device.</p><div class="intel-pack-list"></div><p class="intel-packs-notice" role="status" aria-live="polite"></p><div class="intel-packs-actions"><button type="button" data-intel-refresh>Refresh this view</button></div><p class="intel-packs-footnote">Recently viewed data is cached locally, not included in offline map downloads. Public coverage and freshness vary. Never rely on these layers alone for safety or access.</p>`;
    panel.setAttribute("aria-label", "Map layers and intel packs");
    panel.querySelector(".intel-packs-footnote").textContent = "Public layers are cached locally, separate from offline maps. Saved markers and areas stay visible when zooming out; zoom in to download additional coverage. Traffic motion is estimated briefly between reports, then pauses. Last-seen positions remain for 30 minutes; a selected target stays until closed. Wide views keep the last regional feed active. Coverage varies; never rely on these layers alone for safety or access.";
    panel.querySelector("h2").textContent = "Map layers";
    panel.querySelector(".intel-packs-intro").textContent = "Choose your map, terrain and public context.";
    panel.querySelector(".intel-pack-list").insertAdjacentHTML("beforebegin", `<section class="map-layer-options"><h3>Map & terrain</h3>${setMapStyle ? '<label class="map-style-select">Base map<select data-map-style aria-label="Base map"><option value="hiking">Hiking map</option><option value="satellite">Satellite</option></select></label><p>Trails are always shown as dashed lines. Mapped access and conditions can change.</p>' : ''}<div data-map-features></div></section><h3 class="intel-section-title">Intel packs</h3>`);
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
      if (event.target?.matches?.("[data-firms-days]")) void setFirmsDays(Number(event.target.value));
      const type = event.target?.dataset?.gdacsType;
      if (type) void setGdacsTypes(event.target.checked ? [...gdacsTypes, type] : gdacsTypes.filter(id => id !== type));
    });
    listen(panel.querySelector("[data-intel-refresh]"), "click", () => void refresh());
    const saveField = (target, delay) => {
      const key = target?.name;
      if (ACCESS[key]) {
        const value = target.value;
        credentialDrafts.set(key, value);
        scheduleAutosave(`credential:${key}`, () => updateCredentials({[key]: value.trim()}), delay);
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
      saveField(event.target.querySelector("input"), 0);
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
    const filtersOpen = Boolean(panel?.querySelector(".intel-disaster-filter")?.open);
    const accessOpen = Object.keys(ACCESS).filter(key => panel?.querySelector(`[data-intel-access="${key}"]`)?.open);
    const trafficOpen = TRAFFIC_IDS.filter(id => panel?.querySelector(`[data-traffic-filter="${id}"]`)?.open);
    if (panel) panel.querySelector(".intel-pack-list").innerHTML = currentPacks().map(pack => `<section class="intel-pack-row" data-pack-row="${escape(pack.id)}"><label class="intel-pack-switch"><span><strong>${escape(pack.title)}</strong><small>${escape(pack.description)}</small></span><input type="checkbox" role="switch" data-intel-pack="${escape(pack.id)}" aria-describedby="intel-status-${escape(pack.id)}" ${pack.enabled ? "checked" : ""}/><i aria-hidden="true"></i></label><div class="intel-pack-meta"><span id="intel-status-${escape(pack.id)}" data-intel-status="${escape(pack.id)}"></span>${safeLink(pack.source_url) ? `<a href="${escape(safeLink(pack.source_url))}" target="_blank" rel="noopener noreferrer">${escape(pack.source || "Source")} ↗</a>` : ""}</div><p class="intel-pack-note" data-intel-note="${escape(pack.id)}"></p>${pack.offline_downloadable ? `<button type="button" class="intel-pack-download" data-intel-download="${escape(pack.id)}">SAVE THIS VIEW OFFLINE</button><small class="intel-pack-download-note">Keeps this visible map area on this device without an expiry.</small>` : ""}</section>`).join("");
    panel?.querySelector('[data-pack-row="firms"]')?.insertAdjacentHTML("beforeend", `<label class="intel-firms-window">Detection window<select data-firms-days aria-label="NASA FIRMS detection window">${[[1, "24 hours"], [3, "3 days"], [7, "7 days"]].map(([days, label]) => `<option value="${days}" ${days === firmsDays ? "selected" : ""}>${label}</option>`).join("")}</select></label>`);
    const gdacsRow = panel?.querySelector('[data-pack-row="gdacs"]');
    for (const [key, field] of Object.entries(ACCESS)) {
      panel?.querySelector(`[data-pack-row="${field.pack}"]`)?.insertAdjacentHTML("beforeend", `<details class="intel-access-section" data-intel-access="${key}" ${accessOpen.includes(key) ? "open" : ""}><summary>${field.title}</summary><form class="intel-access-field" data-intel-access-form="${key}"><label for="intel-${key}">${field.title}</label><div class="intel-access-input"><input id="intel-${key}" name="${key}" type="password" autocomplete="off" spellcheck="false" maxlength="8192" value="${escape(credentialDrafts.get(key) || "")}" placeholder="Paste ${escape(field.title)}"/></div><div class="intel-access-links"><a href="${field.url}" target="_blank" rel="noopener noreferrer">${field.link} ↗</a><button type="button" data-intel-clear="${key}">Remove saved key</button></div></form></details>`);
    }
    const mapFeatures = panel?.querySelector("[data-map-features]");
    if (mapFeatures) {
      mapFeatures.replaceChildren();
      for (const id of ["elevation"]) {
        const row = panel.querySelector(`[data-pack-row="${id}"]`);
        if (row) mapFeatures.append(row);
      }
    }
    if (gdacsRow) gdacsRow.insertAdjacentHTML("beforeend", `<details class="intel-disaster-filter" ${filtersOpen ? "open" : ""}><summary>Disaster types <span data-gdacs-summary></span></summary><div class="intel-disaster-actions"><button type="button" data-gdacs-select="all">All</button><button type="button" data-gdacs-select="none">None</button></div><div class="intel-disaster-grid">${disasterTypes().map(type => `<label><input type="checkbox" data-gdacs-type="${escape(type.id)}"/>${gdacsIconSvg(type.id)}<span>${escape(type.label)}</span></label>`).join("")}</div><p>Icon shows the disaster type. Color shows the GDACS alert level, not whether an area is safe.</p></details>`);
    for (const id of TRAFFIC_IDS) {
      const selected = trafficDrafts.get(id) || trafficFilters[id];
      const options = (items, value) => Object.entries(items).map(([key, label]) => `<option value="${key}" ${key === value ? "selected" : ""}>${escape(label)}</option>`).join("");
      panel?.querySelector(`[data-pack-row="${id}"]`)?.insertAdjacentHTML("beforeend", `<details class="intel-traffic-filter" data-traffic-filter="${id}" ${trafficOpen.includes(id) ? "open" : ""}><summary>Filters · classification & type</summary><form data-traffic-form="${id}"><label>Classification<select name="affiliation">${options({all:"All classifications",military:id === "flights" ? "Military (provider flag)" : "Military / restricted (AIS 35)", commercial:id === "flights" ? "Commercial / airline (estimated)" : "Commercial (AIS type)",unknown:"Other / unknown ownership"},selected.affiliation)}</select></label><label>Type<select name="type">${options({all:"All types",...TRAFFIC_TYPES[id]},selected.type)}</select></label><label>${id === "flights" ? "Callsign, registration or model" : "Vessel name, callsign or MMSI"}<input name="query" type="search" maxlength="64" value="${escape(selected.query)}" placeholder="Search received traffic"/></label><p>${id === "flights" ? "Commercial is an estimate from callsign and aircraft category, not confirmed ownership. Missing military flags do not prove civilian status." : "AIS type is self-reported and can arrive after a position. Type 35 includes military and other restricted operations. Unknown vessels stay separate."}</p></form></details>`);
    }
    renderStatus();
    const restore = focusedTraffic ? panel?.querySelector(`[data-traffic-form="${focusedTraffic}"] [name="${focusedName}"]`) : ACCESS[focusedCredential] ? panel?.querySelector(`[name="${focusedCredential}"]`) : null;
    restore?.focus();
    if (restore && selectionStart != null) restore.setSelectionRange(selectionStart, selectionEnd);
    if (focusedPack && IDS.includes(focusedPack)) panel.querySelector(`[data-intel-pack="${focusedPack}"]`)?.focus();
    if (focusedType && GDACS_TYPES.some(type => type.id === focusedType)) panel.querySelector(`[data-gdacs-type="${focusedType}"]`)?.focus();
  }

  function renderStatus() {
    const windowSelect = panel?.querySelector("[data-firms-days]");
    if (windowSelect) { windowSelect.value = String(firmsDays); windowSelect.disabled = saving; }
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

  function applyCatalogue(value) {
    catalogue = {...value, packs: (value.packs || []).filter(pack => IDS.includes(pack.id))};
    enabled = new Set(value.settings?.enabled || catalogue.packs.filter(pack => pack.enabled).map(pack => pack.id));
    enabled = new Set([...enabled].filter(id => IDS.includes(id)));
    gdacsTypes = normalizeGdacsTypes(value.settings?.gdacs_types, disasterTypes());
    trafficFilters = normalizeTrafficFilters(value.settings?.traffic_filters);
    firmsDays = [1, 3, 7].includes(value.settings?.firms_days) ? value.settings.firms_days : 3;
    renderCatalogue();
    for (const context of maps.values()) { syncLayers(context); schedule(context, 0); }
  }

  async function refresh() {
    const generation = ++catalogueGeneration;
    try {
      const value = await request("/api/intel-packs");
      if (!destroyed && generation === catalogueGeneration) {
        notice = "";
        // Recreate failed vector tiles so reconnecting retries contour generation.
        for (const context of maps.values()) if (hasParsedStyle(context)) removeIntelLayers(context.map, "elevation");
        contourFailed = false;
        results.delete("elevation");
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
      notice = body.credentials ? "Source access saved on this device." : "";
      if (body.credentials) for (const key of Object.keys(body.credentials)) {
        if (credentialDrafts.get(key)?.trim() === body.credentials[key]) credentialDrafts.delete(key);
      }
      results.clear();
      applyCatalogue(value);
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

  async function updateCredentials(credentials) {
    const filtered = Object.fromEntries(Object.entries(credentials).filter(([key, value]) => ACCESS[key] && typeof value === "string"));
    if (!Object.keys(filtered).length) return false;
    for (const context of maps.values()) invalidate(context);
    return save({credentials: filtered});
  }

  async function downloadView(id) {
    const context = activeMap();
    const pack = packById(id);
    if (!context || !pack?.enabled || saving || destroyed) { notice = "Enable Military areas, then open the map view you want to save."; renderStatus(); return false; }
    const bounds = context.map.getBounds();
    const button = panel?.querySelector(`[data-intel-download="${id}"]`);
    if (button) { button.disabled = true; button.textContent = "SAVING VIEW…"; }
    try {
      const result = await request(`/api/intel-packs/${encodeURIComponent(id)}/download`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()], zoom: context.map.getZoom()})});
      results.set(id, visibleData(id, result));
      context.data.set(id, result);
      syncLayers(context);
      notice = "Military areas saved on this device. They remain available offline.";
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
    if (!context.credit) return;
    const credits = new Map();
    for (const pack of currentPacks().filter(pack => pack.enabled && pack.configured !== false)) {
      const url = pack.id === "elevation" ? "https://github.com/tilezen/joerd/blob/master/docs/attribution.md" : ["trails", "military"].includes(pack.id) ? "https://www.openstreetmap.org/copyright" : safeLink(pack.attribution_url || pack.source_url);
      const label = pack.id === "elevation" ? "Terrain: Mapzen / data providers" : pack.attribution || pack.source || pack.title;
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
          if (id !== "elevation" || !map.getSource(sourceId(id)) || layerIds(id).some(layer => !map.getLayer(layer))) installIntelLayers(map, pack, animatedData(context, id, context.data.get(id) || EMPTY()).data, contourUrl);
        }
        catch (error) { results.set(id, {status: "error", error: `Map layer unavailable: ${error.message}`}); }
      }
    }
    updateCredit(context);
    updateTrafficTrack(context);
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

  async function refreshMap(context, trafficOnly = false) {
    if (destroyed || !isVisible(context) || !hasParsedStyle(context)) return;
    context.needsRefresh = false;
    const map = context.map;
    if (enabled.has("elevation") && contourUrl && results.get("elevation")?.status !== "error") {
      const interval = contourInterval(map.getZoom());
      results.set("elevation", interval ? {status: "ready", note: `${interval} m contour interval · labels in metres`} : {status: "zoom_in", note: "Zoom in to see contour lines"});
    }
    await Promise.all(currentPacks().filter(pack => pack.enabled && pack.id !== "elevation" && !context.requests.has(pack.id) && (!trafficOnly || TRAFFIC_IDS.includes(pack.id))).map(async pack => {
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
        const previous = context.data.get(pack.id);
        if (traffic) data = retainTraffic(previous, data);
        else data = retainIntel(previous, data, requestedBoxes);
        results.set(pack.id, visibleData(pack.id, data));
        context.data.set(pack.id, data);
        context.tracks.get(pack.id)?.ingest(data);
        if (hasParsedStyle(context)) installIntelLayers(map, pack, animatedData(context, pack.id, data).data);
        trafficAnimator.start();
      } catch (error) {
        if (destroyed || !enabled.has(pack.id) || context.requests.get(pack.id) !== pending) return;
        const old = context.data.get(pack.id);
        const failure = error.name === "AbortError" ? "Source request timed out. Try again later." : error.message;
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


  function trafficPopupFeature(context, feature, packId, additions = {}) {
    const identity = String(feature?.properties?.identity || "").toLowerCase();
    const profile = packId === "flights" ? context.aircraftProfiles.get(identity) : null;
    return {...feature, properties: {...feature.properties, ...(profile || {}), ...additions}};
  }

  async function loadAircraftProfile(context, identity) {
    identity = String(identity || "").toLowerCase();
    if (!/^[0-9a-f]{6}$/.test(identity) || context.aircraftProfiles.has(identity)) return;
    // Mark in-flight/unknown lookups so opening the same card never hammers a
    // public metadata provider while a map is refreshing.
    context.aircraftProfiles.set(identity, null);
    try {
      const profile = await request(`/api/intel-packs/live/flights/${identity}/profile`);
      if (destroyed || !profile) return;
      context.aircraftProfiles.set(identity, {
        aircraft_model: profile.model, aircraft_manufacturer: profile.manufacturer,
        aircraft_type_code: profile.type_code, aircraft_registration: profile.registration,
        aircraft_owner: profile.owner,
        aircraft_photo_url: profile.photo_available ? `/api/intel-packs/live/flights/${identity}/thumbnail` : "",
        aircraft_photo_source: profile.photo_source_url,
      });
    } catch {
      // Public enrichment is optional. The signed Reticom picture and live
      // traffic view remain useful when this secondary provider is unavailable.
      context.aircraftProfiles.set(identity, null);
    }
    if (context.selectedTraffic?.pack === "flights" && context.selectedTraffic.key === identity) updateTrafficTrack(context);
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
      context.selectedTraffic = null;
      context.popup?.remove(); context.popup = null;
      clearTrafficTrack(context.map);
      return;
    }
    const route = context.tracks.get(selected.pack).route(selected.key, Date.now()/1000, selected.history);
    context.map.getSource(sourceId(selected.pack))?.setData({type: "FeatureCollection", features: visible});
    showTrafficTrack(context.map, route.data);
    replaceTrafficPopup(context.popup, intelFeatureHtml(trafficPopupFeature(context, feature, selected.pack, {track_label: route.label}), pack, results.get(selected.pack)));
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
    const context = {map, Popup, motion: new Map(TRAFFIC_IDS.map(id => [id, new TrafficMotion(id)])), tracks: new Map(TRAFFIC_IDS.map(id => [id, new TrafficTracks(id)])), selectedTraffic: null, aircraftProfiles: new Map(), trafficRegions: new Map(), rejectedTrafficViews: new Map(), trafficClient: globalThis.crypto?.randomUUID?.() || `map-${Math.random().toString(36).slice(2)}`, requests: new Map(), data: new Map(), timer: null, popup: null, credit: null, needsRefresh: true};
    maps.set(map, context);
    const container = map.getContainer?.();
    if (container?.ownerDocument) {
      context.credit = container.ownerDocument.createElement("div");
      context.credit.className = "intel-map-credit";
      context.credit.hidden = true;
      container.append(context.credit);
      context.credit.addEventListener("pointerdown", event => event.stopPropagation());
    }
    context.onStyle = () => { invalidate(context); syncLayers(context); schedule(context, 0); };
    context.onMove = () => schedule(context);
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
    };
    context.onClick = event => {
      if (!context.Popup || event.originalEvent?.target?.closest?.(".maplibregl-marker,button,a")) return;
      context.popup?.remove(); context.popup = null;
      context.selectedTraffic = null;
      clearTrafficTrack(map);
      const features = map.queryRenderedFeatures(event.point);
      if (features.some(feature => isTeamLayer(feature.layer))) return;
      const feature = features.find(item => item.layer?.id?.startsWith(PREFIX) && !item.layer.id.startsWith(`${PREFIX}elevation-`));
      if (!feature) return;
      const id = IDS.find(id => feature.layer.id.startsWith(`${PREFIX}${id}-`));
      const pack = packById(id);
      if (!pack || !enabled.has(id)) return;
      context.popup = new context.Popup({maxWidth: "320px", closeButton: true, className: "public-intel-popup"}).setLngLat(event.lngLat).setHTML(intelFeatureHtml(trafficPopupFeature(context, feature, id), pack, results.get(id))).addTo(map);
      const popup = context.popup;
      popup.on?.("close", () => {
        if (context.popup !== popup) return;
        context.popup = null; context.selectedTraffic = null;
        clearTrafficTrack(map);
      });
      if (TRAFFIC_IDS.includes(id)) {
        const observed = context.data.get(id)?.features.find(item => trafficKey(item) === trafficKey(feature));
        // Rendered IDs may be MapLibre IDs and coordinates may be predicted.
        const coordinates = [feature.properties.reported_lon, feature.properties.reported_lat];
        const original = observed || {...feature, geometry: {...feature.geometry, coordinates: coordinates.every(Number.isFinite) ? coordinates : feature.geometry.coordinates}};
        context.selectedTraffic = {pack: id, key: trafficKey(feature), feature: original};
        updateTrafficTrack(context);
        map.panTo?.(feature.geometry.coordinates.slice(0, 2), {duration: motionPreference?.matches ? 0 : 300});
        void loadTrafficRoute(context, context.selectedTraffic);
        if (id === "flights") void loadAircraftProfile(context, feature.properties?.identity);
      }
    };
    context.onRemove = () => detachMap(map);
    map.on("style.load", context.onStyle);
    map.on("load", context.onStyle);
    map.on("moveend", context.onMove);
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
    clearTimeout(context.timer); invalidate(context); context.popup?.remove(); context.credit?.remove();
    clearTrafficTrack(map);
    releaseTraffic(context);
    for (const [event, handler] of [["style.load", context.onStyle], ["load", context.onStyle], ["moveend", context.onMove], ["idle", context.onIdle], ["error", context.onError], ["click", context.onClick], ["remove", context.onRemove]]) map.off(event, handler);
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

  buildPanel();
  if (button) { button.setAttribute("aria-expanded", "false"); if (panel?.id) button.setAttribute("aria-controls", panel.id); }
  listen(button, "click", () => setOpen(Boolean(panel?.hidden || panel?.classList.contains("hidden"))));
  listen(panel?.ownerDocument, "keydown", event => { if (event.key === "Escape" && panel && !panel.hidden && !panel.classList.contains("hidden")) { event.stopPropagation(); setOpen(false); } });
  if (typeof window !== "undefined") listen(window, "online", () => { if (enabled.has("elevation")) void refresh(); });
  const interval = setInterval(() => { for (const context of maps.values()) if (isVisible(context) && !context.requests.size) schedule(context, 0); }, 60000);
  const trafficInterval = setInterval(() => {
    if (!TRAFFIC_IDS.some(id => enabled.has(id))) return;
    for (const context of maps.values()) {
      if (!isVisible(context)) { releaseTraffic(context); continue; }
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
  return {ready, attachMap, refresh, setEnabled, setFirmsDays, setGdacsTypes, setTrafficFilters, updateCredentials, getState: () => ({packs: currentPacks(), settings: {firms_days: firmsDays, gdacs_types: [...gdacsTypes], traffic_filters: trafficFilters}, results: Object.fromEntries(results)}), destroy() {
    destroyed = true; ++catalogueGeneration; clearInterval(interval); clearInterval(trafficInterval);
    for (const timer of autosaveTimers.values()) clearTimeout(timer);
    trafficAnimator.destroy();
    for (const context of maps.values()) { if (hasParsedStyle(context)) for (const id of IDS) removeIntelLayers(context.map, id); detachMap(context.map); }
    for (const cleanup of listeners) cleanup();
  }};
}
