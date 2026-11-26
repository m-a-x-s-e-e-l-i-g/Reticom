// Device-local public traffic. Classification is context, never friend/foe.
import {TRAFFIC_CACHE_SECONDS} from "./traffic-cache.js?v=20260909-3";
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

const AIRCRAFT_SHAPES = {light: "prop", small: "small-jet", medium: "airliner", heavy: "widebody", helicopter: "helicopter",
  glider: "glider", balloon: "balloon", drone: "drone", ground: "ground"};
const VESSEL_SHAPES = {cargo: "cargo", tanker: "tanker", passenger: "ferry", fishing: "fishing", sailing: "sailboat",
  pleasure: "yacht", tug: "tug", service: "service", high_speed: "fast-boat", military_restricted: "patrol"};
const MILITARY_COLOR = "#d5a4ff";

function aircraftShape(props) {
  const type = String(props.type_code || props.aircraft_type_code || "").toUpperCase();
  if (/^(C130|C30J|A400|C160|C17|C5M|C5|IL76|AN12|AN22|AN72|A124|A225)$/.test(type)) return "transport";
  if (/^(F4|F5|F14|F15|F16|F18|F18H|F18S|F22|F35|F35B|F35C|EUFI|RFAL|JAS39|GRIP|MIR2|MIG2|SU27|SU30|SU35|SU57)$/.test(type)) return "fighter";
  if (/^(AT4[356]|AT7[2356]|DH8[ABCD]|SF34|F50|BE(20|30|9L)|B350|PA(30|31|34|44))$/.test(type)) return "twin-prop";
  if (/^(PA(18|24|25|28|32|38|46)|P28[A-Z]|P32[A-Z]|C1[578][0-9]|SR2[02]|BE(33|35|36)|PC12|TBM[789])$/.test(type)) return "prop";
  if (/^(B73[1-9]|B3[789X]M|B75[23]|A31[89]|A32[0-3]|BCS[13]|E1[79][05]|E2[79]0)$/.test(type)) return "airliner";
  if (/^(B74[1-8]|B76[234]|B77[23LWF]|B78[89X]|A3[3458][0-9])$/.test(type)) return "widebody";
  if (props.category === "A6") return "fighter";
  return AIRCRAFT_SHAPES[props.traffic_type] || "plane";
}

function vesselScale(length) {
  if (!Number.isFinite(length) || length <= 0) return 1;
  if (length < 30) return .78;
  if (length >= 150) return 1.24;
  return 1;
}

export function trafficDisplayData(id, data, filters, now = Date.now() / 1000) {
  const selected = normalizeTrafficFilters({[id]: filters})[id], query = selected.query.toLowerCase();
  const features = (data?.features || []).filter(feature => {
    const p = feature.properties || {}, age = now - Number(p.position_time);
    return feature.geometry?.type === "Point" && p.traffic_pack === id && Number.isFinite(age) && age >= -30 && (age <= TRAFFIC_CACHE_SECONDS || p.traffic_selected === true)
      && (selected.affiliation === "all" || p.affiliation === selected.affiliation)
      && (selected.type === "all" || p.traffic_type === selected.type)
      && (!query || [p.title, p.callsign, p.identity, p.registration, p.type_code].some(value => String(value || "").toLowerCase().includes(query)));
  }).map(feature => {
    const p = feature.properties;
    const shape = id === "vessels" ? `sea-${VESSEL_SHAPES[p.traffic_type] || "ship"}` : aircraftShape(p);
    const color = p.affiliation === "military" ? "-military" : "";
    const heading = Number.isFinite(p.heading) ? "" : "-no-heading";
    return {...feature, properties: {...p,
      traffic_icon: `${shape}${color}${heading}`,
      traffic_icon_scale: id === "vessels" ? vesselScale(p.length_m) : 1,
      stale: now - Number(p.position_time) > (id === "flights" ? 45 : 120),
      age_seconds: Math.max(0, Math.floor(now - Number(p.position_time))),
    }};
  });
  return {...data, type: "FeatureCollection", features, total_received: data?.features?.length || 0};
}

const PATHS = {
  plane: "M12 2 14 9 22 14 22 17 14 14 14 19 17 21 17 23 12 21 7 23 7 21 10 19 10 14 2 17 2 14 10 9Z",
  prop: "M11 4H13L14 10 22 11V14L14 13V18L17 20V22L12 21 7 22V20L10 18V13L2 14V11L10 10Z M8 3H16",
  "twin-prop": "M12 2Q14 3 14 7V9L22 11V14L14 13V19L17 21V22L12 21 7 22V21L10 19V13L2 14V11L10 9V7Q10 3 12 2Z M6 7V12 M18 7V12 M4 7H8 M16 7H20",
  "small-jet": "M12 2Q14 3 14 7V10L20 15V17L14 15V18L18 19V22L12 20 6 22V19L10 18V15L4 17V15L10 10V7Q10 3 12 2Z",
  airliner: "M12 2Q14 3 14 7V9L22 15V17L14 14V19L17 21V22L12 21 7 22V21L10 19V14L2 17V15L10 9V7Q10 3 12 2Z M6 11V15 M18 11V15",
  widebody: "M12 1Q15 2 15 7V9L22 15V18L15 15V19L19 21V23L12 21 5 23V21L9 19V15L2 18V15L9 9V7Q9 2 12 1Z M6 11V15 M18 11V15",
  transport: "M12 2Q15 2 15 6V9H22V13L15 14V18L19 20V22L12 21 5 22V20L9 18V14L2 13V9H9V6Q9 2 12 2Z M4 7V12 M7 7V12 M17 7V12 M20 7V12",
  fighter: "M12 2 14 8 21 17 21 20 15 18 15 21 12 20 9 21 9 18 3 20 3 17 10 8Z",
  helicopter: "M12 5 15 10 15 16 13 18 13 22 11 22 11 18 9 16 9 10Z M3 5H21 M12 2V8 M6 11V18 M18 11V18",
  glider: "M12 3 13 10 23 12V14L13 13V19L16 21 12 20 8 21 11 19V13L1 14V12L11 10Z",
  balloon: "M12 3C4 3 3 11 8 15L10 18H14L16 15C21 11 20 3 12 3Z M10 20H14V22H10Z",
  drone: "M12 6 15 11 21 14V17L14 15 13 20H11L10 15 3 17V14L9 11Z M8 5H16",
  ground: "M7 6H17V18H7Z M4 8V11 M20 8V11 M4 13V16 M20 13V16",
  "sea-ship": "M12 2 18 9V21H6V9Z",
  "sea-cargo": "M12 2 19 8V21H5V8Z M8 9H16V11.5H8Z M8 13H16V15.5H8Z M8 17H16V19.5H8Z",
  "sea-tanker": "M12 2 19 7V19L16 22H8L5 19V7Z M12 7A2.5 2.5 0 1 0 12 12A2.5 2.5 0 1 0 12 7Z M12 14A2.5 2.5 0 1 0 12 19A2.5 2.5 0 1 0 12 14Z",
  "sea-ferry": "M12 2C16 3 20 8 20 13V20L17 22H7L4 20V13C4 8 8 3 12 2Z M7 11H17V13H7Z M7 16H17V18H7Z",
  "sea-sailboat": "M11 3 3 16H11Z M13 7V16H19Z M3 19H21L17 22H7Z",
  "sea-yacht": "M12 3 17 10 16 20 12 22 8 20 7 10Z M10 13H14",
  "sea-fishing": "M12 3 17 9V20L12 22 7 20V9Z M3 10 7 13 M21 10 17 13 M10 17H14",
  "sea-tug": "M12 5C7 5 5 9 5 13V18Q5 21 8 21H16Q19 21 19 18V13C19 9 17 5 12 5Z M9 11H15V16H9Z",
  "sea-service": "M12 3 18 10V20H6V10Z M12 11V17 M9 14H15",
  "sea-fast-boat": "M12 2 19 20H5Z M12 9V17",
  "sea-patrol": "M12 2 17 8 16 18 18 21H6L8 18 7 8Z M10 9H14 M10 15H14",
};
export function installTrafficIcons(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined") return;
  for (const [kind, path] of Object.entries(PATHS)) {
    let shape;
    for (const military of [false, true]) for (const unoriented of [false, true]) {
      const id = `public-traffic-${kind}${military ? "-military" : ""}${unoriented ? "-no-heading" : ""}`;
      if (map.hasImage(id)) continue;
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = 48;
      const ctx = canvas.getContext("2d"); if (!ctx) continue;
      shape ??= new Path2D(path);
      const color = military ? MILITARY_COLOR : kind.startsWith("sea-") ? "#a5c8c3" : "#d9c79b";
      ctx.scale(2, 2); ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.lineWidth = 2.5; ctx.strokeStyle = "#0c120e"; ctx.stroke(shape);
      ctx.fillStyle = color; ctx.fill(shape, "evenodd");
      ctx.lineWidth = 1; ctx.strokeStyle = color; ctx.stroke(shape);
      if (unoriented) {
        ctx.beginPath(); ctx.arc(21, 21, 2, 0, 2 * Math.PI);
        ctx.fillStyle = "#0c120e"; ctx.fill(); ctx.stroke();
      }
      map.addImage(id, ctx.getImageData(0, 0, 48, 48), {pixelRatio: 2});
    }
  }
}
const TRANSIT_ICONS = {
  bus: "M5 17V7Q5 4 8 4H16Q19 4 19 7V17Z M5 11H19 M8 15H9 M15 15H16 M7 17V20 M17 17V20",
  train: "M6 16V8Q6 4 12 3Q18 4 18 8V16L15 18H9Z M6 10H18 M9 14H10 M14 14H15 M9 18L6 22 M15 18L18 22 M8 21H16",
  metro: "M5 19V10A7 7 0 0 1 19 10V19 M8 16V9H16V16Z M8 12H16 M10 18H14",
  tram: "M6 18V8H18V18Z M6 13H18 M9 16H10 M14 16H15 M12 8L15 4H9 M9 18L7 21 M15 18L17 21",
  ferry: "M4 14L12 11 20 14 17 18H7Z M7 12V7H17V12 M11 7V4H14 M4 21Q6 18 8 21Q10 18 12 21Q14 18 16 21Q18 18 20 21",
  vehicle: "M8 7H12V17H16 M8 7A2 2 0 1 1 4 7A2 2 0 1 1 8 7 M20 17A2 2 0 1 1 16 17A2 2 0 1 1 20 17",
};

export function installTransitIcons(map) {
  if (!map.addImage || !globalThis.document || typeof Path2D === "undefined") return;
  for (const [mode, path] of Object.entries(TRANSIT_ICONS)) {
    const shape = new Path2D(path);
    for (const estimated of [false, true]) for (const selected of [false, true]) {
      const id = `public-transit-${mode}${estimated ? "-estimate" : ""}${selected ? "-selected" : ""}`;
      if (map.hasImage(id)) continue;
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = 48;
      const ctx = canvas.getContext("2d"); if (!ctx) continue;
      ctx.scale(2, 2); ctx.lineJoin = "round"; ctx.lineCap = "round";
      // Draw the badge and glyph together so style rendering cannot show a bare badge.
      ctx.beginPath(); ctx.arc(12, 12, 10.75, 0, 2 * Math.PI);
      ctx.fillStyle = estimated ? "#17383a" : "#75ddd0"; ctx.fill();
      ctx.strokeStyle = selected ? "#e4faff" : "#75ddd0"; ctx.lineWidth = selected ? 2 : 1.3; ctx.stroke();
      ctx.translate(2, 2); ctx.scale(5 / 6, 5 / 6);
      ctx.strokeStyle = estimated ? "#b6fff2" : "#102a2d"; ctx.lineWidth = 1.8; ctx.stroke(shape);
      map.addImage(id, ctx.getImageData(0, 0, 48, 48), {pixelRatio: 2});
    }
  }
}

export function trafficLayers(source) {
  const opacity = ["case", ["==", ["get", "stale"], true], .4, 1];
  const selected = ["==", ["get", "traffic_selected"], true];
  const military = ["==", ["get", "affiliation"], "military"];
  const size = (normal, active = normal) => ["*", ["case", selected, active, normal], ["coalesce", ["get", "traffic_icon_scale"], 1]];
  return [
    {id: `${source}-points`, type: "circle", source, paint: {"circle-radius": 14, "circle-opacity": 0,
      "circle-stroke-color": ["case", military, MILITARY_COLOR, "#c2b58d"], "circle-stroke-width": ["case", selected, 2, 0], "circle-stroke-opacity": .85}},
    {id: `${source}-icons`, type: "symbol", source, layout: {"icon-image": ["concat", "public-traffic-", ["get", "traffic_icon"]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 6, size(.38, .85), 7, size(.52, .85), 9, size(.72, .85), 12, size(1)], "icon-rotate": ["coalesce", ["get", "heading"], 0], "icon-rotation-alignment": "map", "icon-allow-overlap": true}, paint: {"icon-opacity": opacity}},
    {id: `${source}-labels`, type: "symbol", source, minzoom: 9,
      layout: {"text-field": ["get", "title"], "text-font": ["Noto Sans Regular"], "text-size": 10, "text-anchor": "top", "text-offset": [0, 1.6], "text-max-width": 12},
      paint: {"text-color": ["case", military, MILITARY_COLOR, "#c5c4ad"], "text-halo-color": "#0c120e", "text-halo-width": 1.5, "text-opacity": opacity}},
  ];
}
