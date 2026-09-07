// Code-owned pictograms: the same symbols identify map alerts and filter choices.
export const GDACS_TYPES = [
  {id: "WF", label: "Forest fires", path: "M13 2c1 5-4 5-2 9 2-1 3-3 3-5 4 4 6 7 5 10a7 7 0 0 1-14-1c0-3 2-6 4-8-1 4 0 5 1 5 0-4 3-5 3-10Z M12 14c-2 2-3 4-1 6 3 1 4-2 1-6Z"},
  {id: "FL", label: "Floods", path: "M3 16q2-3 4 0t4 0 4 0 4 0 3 0 M3 21q2-3 4 0t4 0 4 0 4 0 3 0 M6 12V7l6-5 6 5v5 M10 12V8h4v4"},
  {id: "EQ", label: "Earthquakes", path: "M2 12h4l2-5 3 11 3-14 3 8h5 M4 21h6l2-3 2 3h6"},
  {id: "TC", label: "Cyclones", path: "M20 4c-6-3-14 0-15 6-1 5 4 9 8 8 M4 20c6 3 14 0 15-6 1-5-4-9-8-8 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"},
  {id: "DR", label: "Droughts", path: "M12 2c-2 3-5 6-5 9a5 5 0 0 0 9 3 M17 9c0 1 0 2-1 3 M3 3l18 18 M2 22h6l3-3 3 3h8"},
  {id: "VO", label: "Volcanoes", path: "m2 21 7-12h6l7 12H2Z M8 10l4 6 4-6 M9 5 7 2 M12 6V1 M15 5l2-3"},
  {id: "TS", label: "Tsunamis", path: "M2 19c8 0 5-14 14-14 4 0 6 4 4 7-1-3-5-3-5 0 0 4 3 7 7 7 M2 22h20"},
];
const GENERIC = {id: "other", label: "Disaster", path: "m12 3 10 18H2L12 3Z M12 9v5 M12 17v1"};
const TYPES = new Map(GDACS_TYPES.map(type => [type.id, type]));
const LEGACY = {"earthquake": "EQ", "tropical cyclone": "TC", "flood": "FL", "drought": "DR", "forest fire": "WF", "wildfire": "WF", "volcano": "VO", "volcanic eruption": "VO", "tsunami": "TS"};

export function gdacsCode(properties = {}) {
  const code = String(properties.disaster_code || properties.event_type || "").toUpperCase();
  return TYPES.has(code) ? code : LEGACY[String(properties.disaster_type || "").toLowerCase()] || "other";
}

export function gdacsIconSvg(code) {
  const type = TYPES.get(code) || GENERIC;
  return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${type.path}"/></svg>`;
}

export function normalizeGdacsTypes(value, available = GDACS_TYPES) {
  const selected = Array.isArray(value) ? new Set(value) : new Set(available.map(type => type.id));
  return available.filter(type => selected.has(type.id)).map(type => type.id);
}

export function gdacsDisplayData(data, selected = GDACS_TYPES.map(type => type.id)) {
  const allowed = new Set(selected);
  const features = (data?.features || []).filter(feature => {
    const code = gdacsCode(feature.properties);
    // Older local caches may have no taxonomy; never show them when filtered.
    return allowed.has(code) || (code === "other" && allowed.size === GDACS_TYPES.length);
  }).map(feature => ({...feature, properties: {...feature.properties, disaster_code: gdacsCode(feature.properties)}}));
  return {...data, type: "FeatureCollection", features};
}

export function installGdacsIcons(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined") return;
  for (const type of [...GDACS_TYPES, GENERIC]) {
    const id = `public-gdacs-${type.id}`;
    if (map.hasImage(id)) continue;
    const canvas = document.createElement("canvas"); canvas.width = 48; canvas.height = 48;
    const context = canvas.getContext("2d");
    if (!context) continue;
    context.scale(2, 2);
    context.strokeStyle = "#d4d7c7"; context.lineWidth = 1.7;
    context.lineCap = "round"; context.lineJoin = "round";
    context.stroke(new Path2D(type.path));
    map.addImage(id, context.getImageData(0, 0, 48, 48), {pixelRatio: 2});
  }
}

export const GDACS_COLOR = ["match", ["get", "severity"], "red", "#c68278", "orange", "#c5a06c", "green", "#91a47e", "#aaa68c"];
export const GDACS_ICON = ["match", ["get", "disaster_code"], ...GDACS_TYPES.flatMap(type => [type.id, `public-gdacs-${type.id}`]), "public-gdacs-other"];
