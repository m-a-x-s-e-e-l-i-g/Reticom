// Device-local public traffic. Classification is context, never friend/foe.
export const TRAFFIC_IDS = ["flights", "vessels"];
export const TRAFFIC_TYPES = {
  flights: {light: "Light aircraft", small: "Small aircraft", medium: "Medium aircraft", heavy: "Heavy aircraft", helicopter: "Helicopters", glider: "Gliders", balloon: "Balloons / airships", drone: "UAV (reported)", ground: "Ground vehicles", other: "Other", unknown: "Unknown type"},
  vessels: {cargo: "Cargo", tanker: "Tankers", passenger: "Passenger ships", fishing: "Fishing", sailing: "Sailing", pleasure: "Pleasure craft", tug: "Tugs / towing", service: "Service / rescue / law enforcement", high_speed: "High-speed craft", military_restricted: "Military / restricted (AIS 35)", other: "Other", unknown: "Unknown type"},
};
export function normalizeTrafficFilters(value = {}) {
  return Object.fromEntries(TRAFFIC_IDS.map(id => {
    const item = value?.[id] || {};
    return [id, {affiliation: ["all", "military", "commercial", "unknown"].includes(item.affiliation) ? item.affiliation : "all",
      type: Object.hasOwn(TRAFFIC_TYPES[id], item.type) ? item.type : "all", query: String(item.query || "").slice(0, 64).trim()}];
  }));
}
export function trafficDisplayData(id, data, filters, now = Date.now() / 1000) {
  const selected = normalizeTrafficFilters({[id]: filters})[id], query = selected.query.toLowerCase();
  const features = (data?.features || []).filter(feature => {
    const p = feature.properties || {}, age = now - Number(p.position_time);
    return feature.geometry?.type === "Point" && p.traffic_pack === id && Number.isFinite(age) && age >= -30 && age <= (id === "flights" ? 120 : 600)
      && (selected.affiliation === "all" || p.affiliation === selected.affiliation)
      && (selected.type === "all" || p.traffic_type === selected.type)
      && (!query || [p.title, p.callsign, p.identity, p.registration, p.type_code].some(value => String(value || "").toLowerCase().includes(query)));
  }).map(feature => ({...feature, properties: {...feature.properties,
    traffic_icon: feature.properties.heading == null ? "unknown" : id === "vessels" ? "ship" : feature.properties.traffic_type === "helicopter" ? "helicopter" : "plane",
    stale: now - Number(feature.properties.position_time) > (id === "flights" ? 45 : 120),
    age_seconds: Math.max(0, Math.floor(now - Number(feature.properties.position_time))),
  }}));
  return {...data, type: "FeatureCollection", features, total_received: data?.features?.length || 0};
}

const PATHS = {
  plane: "M12 2 14 9 22 14 22 17 14 14 14 19 17 21 17 23 12 21 7 23 7 21 10 19 10 14 2 17 2 14 10 9Z",
  helicopter: "M12 5 15 10 15 16 13 18 13 22 11 22 11 18 9 16 9 10Z M3 5H21 M12 2V8 M6 11V18 M18 11V18",
  ship: "M12 2 18 9 18 21 6 21 6 9Z M9 12H15 M9 16H15",
  unknown: "M12 3 21 12 12 21 3 12Z M12 7V13 M12 16V17",
};
export function installTrafficIcons(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined") return;
  for (const [kind, path] of Object.entries(PATHS)) {
    const id = `public-traffic-${kind}`;
    if (map.hasImage(id)) continue;
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 48;
    const ctx = canvas.getContext("2d"); if (!ctx) continue;
    ctx.scale(2, 2); ctx.lineJoin = "round"; ctx.lineCap = "round";
    const shape = new Path2D(path);
    ctx.lineWidth = 3; ctx.strokeStyle = "#0c120e"; ctx.stroke(shape);
    ctx.fillStyle = kind === "ship" ? "#a5b8ac" : "#c2b58d"; ctx.fill(shape);
    ctx.lineWidth = 1; ctx.strokeStyle = ctx.fillStyle; ctx.stroke(shape);
    map.addImage(id, ctx.getImageData(0, 0, 48, 48), {pixelRatio: 2});
  }
}
export function trafficLayers(source) {
  const opacity = ["case", ["==", ["get", "stale"], true], .4, 1];
  return [
    {id: `${source}-points`, type: "circle", source, paint: {"circle-radius": 14, "circle-opacity": 0}},
    {id: `${source}-icons`, type: "symbol", source, layout: {"icon-image": ["concat", "public-traffic-", ["get", "traffic_icon"]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 6, .38, 7, .52, 9, .72, 12, 1], "icon-rotate": ["coalesce", ["get", "heading"], 0], "icon-rotation-alignment": "map", "icon-allow-overlap": true}, paint: {"icon-opacity": opacity}},
    {id: `${source}-labels`, type: "symbol", source, minzoom: 9,
      layout: {"text-field": ["get", "title"], "text-font": ["Noto Sans Regular"], "text-size": 10, "text-anchor": "top", "text-offset": [0, 1.6], "text-max-width": 12},
      paint: {"text-color": "#c5c4ad", "text-halo-color": "#0c120e", "text-halo-width": 1.5, "text-opacity": opacity}},
  ];
}
