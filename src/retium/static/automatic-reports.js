const EARTH_RADIUS_METERS = 6371000;

export const AUTOMATIC_REPORT_MAX_DISTANCE_METERS = 5000;
export const AUTOMATIC_REPORT_POSITION_MAX_AGE_SECONDS = 10 * 60;

const BEARINGS = {
  n: 0, north: 0, ne: 45, northeast: 45,
  e: 90, east: 90, se: 135, southeast: 135,
  s: 180, south: 180, sw: 225, southwest: 225,
  w: 270, west: 270, nw: 315, northwest: 315,
};

const DIRECTION_PATTERN = "north[\\s-]?east|south[\\s-]?east|south[\\s-]?west|north[\\s-]?west|northeast|southeast|southwest|northwest|north|south|east|west|ne|se|sw|nw|n|s|e|w";
const NUMBER_WORDS = {one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, fifteen: 15, twenty: 20, thirty: 30};

const REPORTS = [
  {type: "road-blocked", match: /\b(?:road\s+(?:is\s+)?blocked|blocked\s+road)\b/, label: "ROAD BLOCKED", markerType: "obstacle", symbol: "OBS", color: "#c46855", ttl: 6 * 3600, shape: "road-block"},
  {type: "casualty", match: /\b(?:casualty|casualties)(?:\s+(?:at|on))?\b/, label: "CASUALTY", markerType: "medical", symbol: "MED", color: "#d5d1bd", ttl: 2 * 3600, state: "REPORTED"},
  {type: "medevac", match: /\b(?:need\s+(?:an\s+)?evacuation|request(?:ing)?\s+(?:an\s+)?evacuation|medevac)\b/, label: "MEDEVAC", markerType: "medical evacuation", symbol: "EVAC", color: "#d5d1bd", ttl: 2 * 3600, state: "PICKUP REQUESTED"},
  {type: "evac-point", match: /\b(?:(?:evac(?:uation)?|extract(?:ion)?)\s+(?:point|zone)|pickup\s+(?:point|zone))\b|^evac$/, label: "EVAC POINT", markerType: "evacuation point", symbol: "EVAC", color: "#d5d1bd", ttl: 4 * 3600, state: "DESIGNATED"},
  {type: "medical-point", match: /\b(?:medical|medic|aid)\s+(?:point|station)\b/, label: "MEDICAL POINT", markerType: "medical point", symbol: "MED", color: "#d5d1bd", ttl: 8 * 3600, state: "DESIGNATED"},
  {type: "rally-point", match: /\brally\s+point\b/, label: "RALLY POINT", markerType: "rally point", symbol: "RP", color: "#a79669", ttl: 4 * 3600},
  {type: "checkpoint", match: /\bcheckpoint(?:\s+(?:established|set|active))?\b/, label: "CHECKPOINT", markerType: "checkpoint", symbol: "CP", color: "#899a78", ttl: 8 * 3600, state: "ESTABLISHED"},
  {type: "landing-zone", match: /\b(?:landing\s+zone|lz)\b.*\bclear\b|\bclear\b.*\b(?:landing\s+zone|lz)\b/, label: "LANDING ZONE", markerType: "landing zone", symbol: "LZ", color: "#899a78", ttl: 2 * 3600, state: "CLEAR"},
  {type: "landing-zone", match: /\b(?:landing\s+zone|lz)\b/, label: "LANDING ZONE", markerType: "landing zone", symbol: "LZ", color: "#899a78", ttl: 2 * 3600, state: "REPORTED"},
  {type: "vehicle-disabled", match: /\bvehicle\s+(?:is\s+)?disabled\b/, label: "VEHICLE DISABLED", markerType: "disabled vehicle", symbol: "VEH", color: "#c79558", ttl: 4 * 3600},
  {type: "unit-moving", match: /\b(?:unit|we|i)\s+(?:is\s+|are\s+|am\s+)?moving\b/, label: "UNIT MOVING", markerType: "movement heading", symbol: "MOV", color: "#718d80", ttl: 20 * 60, shape: "heading", defaultDistance: 180},
  {type: "possible-movement", match: /\bpossible\s+movement\b/, label: "POSSIBLE MOVEMENT", markerType: "uncertain contact", symbol: "?", color: "#c79558", ttl: 30 * 60, shape: "uncertainty", defaultDistance: 300},
  {type: "drone-spotted", match: /\b(?:drone|uas|uav)\s+(?:spotted|observed|seen)\b/, label: "AERIAL OBSERVATION", markerType: "drone observation", symbol: "UAV", color: "#c46855", ttl: 30 * 60, shape: "direction", defaultDistance: 300},
  {type: "fire-smoke", match: /\b(?:fire\s+(?:or|and)\s+smoke|fire|smoke)\b/, label: "FIRE / SMOKE", markerType: "hazard", symbol: "FIRE", color: "#c46855", ttl: 60 * 60, shape: "hazard-area", defaultDistance: 180},
  {type: "radio-dead-zone", match: /\b(?:radio|comms?|communications?)\s+(?:dead\s+zone|blackspot|black\s+spot)\b/, label: "RADIO DEAD ZONE", markerType: "communications warning", symbol: "COM", color: "#9d7892", ttl: 6 * 3600, shape: "warning-area"},
  {type: "supply-cache", match: /\b(?:supply|ammo|ammunition)\s+cache\b/, label: "SUPPLY CACHE", markerType: "logistics", symbol: "SUP", color: "#a79669", ttl: 12 * 3600},
  {type: "supply-point", match: /\b(?:supply|ammo|ammunition)\s+point\b/, label: "SUPPLY POINT", markerType: "logistics", symbol: "SUP", color: "#a79669", ttl: 12 * 3600},
  {type: "water", match: /\bwater\s+(?:is\s+)?available\b/, label: "WATER AVAILABLE", markerType: "sustainment", symbol: "H2O", color: "#668995", ttl: 12 * 3600},
  {type: "water-point", match: /\b(?:water|watering)\s+point\b/, label: "WATER POINT", markerType: "sustainment", symbol: "H2O", color: "#668995", ttl: 12 * 3600},
  {type: "route-compromised", match: /\broute\s+([a-z0-9][a-z0-9 -]{0,24}?)\s+(?:is\s+)?compromised\b/, label: "ROUTE COMPROMISED", markerType: "route warning", symbol: "RTE", color: "#c46855", ttl: 4 * 3600, shape: "route-warning"},
  {type: "bridge-damaged", match: /\bbridge\s+(?:is\s+)?damaged\b/, label: "BRIDGE DAMAGED", markerType: "damaged infrastructure", symbol: "BRG", color: "#c46855", ttl: 8 * 3600},
  {type: "search-area", match: /\bsearch\s+(?:this|the)\s+area\b/, label: "SEARCH AREA", markerType: "search sectors", symbol: "SAR", color: "#a79669", ttl: 4 * 3600, shape: "search-area"},
  {type: "last-seen", match: /\blast\s+seen\s+(?:here|at\s+(?:this|my)\s+(?:position|location))\b/, label: "LAST KNOWN POSITION", markerType: "last known position", symbol: "LKP", color: "#c79558", ttl: 30 * 60, shape: "last-known"},
  {type: "hold-line", match: /\bhold\s+(?:north|south|east|west|northeast|northwest|southeast|southwest|ne|nw|se|sw)\s+of\s+(?:this\s+|the\s+)?road\b/, label: "HOLD LINE", markerType: "temporary boundary", symbol: "HOLD", color: "#a79669", ttl: 2 * 3600, shape: "phase-line", defaultDistance: 100},
  {type: "contact", match: /\bcontact\b/, label: "CONTACT", markerType: "contact estimate", symbol: "ENY", color: "#c46855", ttl: 30 * 60, shape: "contact"},
];

function normalize(message) {
  return String(message || "").trim().toLowerCase().replace(/[.,!?;:]+/g, " ").replace(/\s+/g, " ");
}

function extractDirection(message) {
  const match = message.match(new RegExp(`\\b(${DIRECTION_PATTERN})\\b`, "i"));
  if (!match) return {direction: null, bearing: null};
  const direction = match[1].replace(/[\s-]/g, "").toLowerCase();
  return {direction, bearing: BEARINGS[direction]};
}

function extractDistance(message) {
  const match = message.match(/\b(\d{1,4}(?:\.\d+)?)\s*(kilometers?|kilometres?|kms?|meters?|metres?|m)\b/i);
  if (!match) return null;
  let distance = Number(match[1]);
  if (match[2].toLowerCase().startsWith("k")) distance *= 1000;
  return Number.isFinite(distance) && distance >= 1 && distance <= AUTOMATIC_REPORT_MAX_DISTANCE_METERS ? distance : null;
}

function extractAgoSeconds(message) {
  const match = message.match(/\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty)\s+(minutes?|mins?|hours?|hrs?)\s+ago\b/);
  if (!match) return 0;
  const amount = Number(match[1]) || NUMBER_WORDS[match[1]] || 0;
  return amount * (match[2].startsWith("h") ? 3600 : 60);
}

export function parseAutomaticReport(message) {
  const normalized = normalize(message);
  if (!normalized) return null;
  const cancellation = normalized.match(/\bcancel\s+(?:my\s+|the\s+)?last\s+((?:enemy\s+)?contact|report|marker)\b/);
  if (cancellation) {
    return {action: "cancel-last", type: "cancel-last", targetType: cancellation[1].includes("contact") ? "contact" : null};
  }
  const definition = REPORTS.find((item) => item.match.test(normalized));
  if (!definition) return null;
  const {direction, bearing} = extractDirection(normalized);
  const explicitDistance = extractDistance(normalized);
  if (definition.type === "contact" && (!Number.isFinite(bearing) || explicitDistance === null)) return null;
  if (["unit-moving", "possible-movement", "drone-spotted", "hold-line"].includes(definition.type) && !Number.isFinite(bearing)) return null;
  const match = normalized.match(definition.match);
  const routeName = definition.type === "route-compromised" ? String(match?.[1] || "").trim().toUpperCase() : "";
  const landmarkMatch = normalized.match(/\b(?:at|by)\s+the\s+([a-z][a-z0-9 -]{1,32})$/);
  const distance = explicitDistance ?? definition.defaultDistance ?? 0;
  const directionLabel = direction ? direction.toUpperCase() : "";
  const distanceLabel = distance >= 1000 ? `${distance / 1000} KM` : distance ? `${distance} M` : "";
  const contactLabel = definition.type === "contact" ? `CONTACT ${directionLabel} · ${distanceLabel}` : "";
  return {
    action: "create",
    type: definition.type,
    label: routeName ? `ROUTE ${routeName} COMPROMISED` : contactLabel || definition.label,
    markerType: definition.markerType,
    symbol: definition.symbol,
    color: definition.color,
    ttlSeconds: definition.ttl,
    shape: definition.shape || "point",
    state: definition.type === "checkpoint" && !/\b(?:established|set|active)\b/.test(normalized)
      ? "DESIGNATED"
      : definition.state || "",
    direction,
    bearing,
    distance,
    approximate: Boolean(
      (distance && explicitDistance === null)
      || landmarkMatch
      || ["road-blocked", "route-compromised", "bridge-damaged"].includes(definition.type),
    ),
    landmark: landmarkMatch ? landmarkMatch[1].toUpperCase() : "",
    routeName,
    occurredAgoSeconds: definition.type === "last-seen" ? extractAgoSeconds(normalized) : 0,
  };
}

export function projectPoint(origin, bearing, distance) {
  const angularDistance = distance / EARTH_RADIUS_METERS;
  const bearingRadians = bearing * Math.PI / 180;
  const latitude = origin[1] * Math.PI / 180;
  const longitude = origin[0] * Math.PI / 180;
  const targetLatitude = Math.asin(Math.sin(latitude) * Math.cos(angularDistance) + Math.cos(latitude) * Math.sin(angularDistance) * Math.cos(bearingRadians));
  const targetLongitude = longitude + Math.atan2(
    Math.sin(bearingRadians) * Math.sin(angularDistance) * Math.cos(latitude),
    Math.cos(angularDistance) - Math.sin(latitude) * Math.sin(targetLatitude),
  );
  return [targetLongitude * 180 / Math.PI, targetLatitude * 180 / Math.PI];
}

function circle(origin, radius, steps = 32) {
  const ring = [];
  for (let index = 0; index <= steps; index += 1) ring.push(projectPoint(origin, index * 360 / steps, radius));
  return {type: "Polygon", coordinates: [ring]};
}

function cone(origin, bearing, distance) {
  const ring = [origin];
  for (let offset = -28; offset <= 28; offset += 7) ring.push(projectPoint(origin, bearing + offset, distance));
  ring.push(origin);
  return {type: "Polygon", coordinates: [ring]};
}

function lineAcross(center, bearing, halfWidth) {
  return [projectPoint(center, bearing - 90, halfWidth), projectPoint(center, bearing + 90, halfWidth)];
}

function arrowGeometry(points) {
  const start = points[0];
  const end = points.at(-1);
  const latitudeScale = Math.max(0.2, Math.cos(((start[1] + end[1]) / 2) * Math.PI / 180));
  const dx = (end[0] - start[0]) * latitudeScale;
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (!length) return {type: "LineString", coordinates: points};
  const head = length * 0.2;
  const ux = dx / length;
  const uy = dy / length;
  const baseX = end[0] * latitudeScale - ux * head;
  const baseY = end[1] - uy * head;
  const wing = head * 0.55;
  return {type: "MultiLineString", coordinates: [
    points,
    [end, [(baseX - uy * wing) / latitudeScale, baseY + ux * wing]],
    [end, [(baseX + uy * wing) / latitudeScale, baseY - ux * wing]],
  ]};
}

export function buildAutomaticReportFeatures(report, origin, meta = {}) {
  const bearing = Number.isFinite(report.bearing) ? report.bearing : 0;
  const target = report.distance ? projectPoint(origin, bearing, report.distance) : origin;
  const observedAt = Number(meta.createdAt || 0) - Number(report.occurredAgoSeconds || 0);
  const observationAge = Math.max(0, Number(meta.nowSeconds ?? Date.now() / 1000) - observedAt);
  const fadeOpacity = report.type === "last-seen"
    ? Math.max(0.14, Math.min(0.78, 0.78 * (1 - observationAge / report.ttlSeconds)))
    : 0.94;
  const base = {
    id: meta.id || "",
    reportId: meta.id || "",
    callsign: meta.callsign || "",
    senderHash: meta.senderHash || "",
    reportMessage: meta.message || "",
    reportedAt: Number(meta.createdAt || 0),
    observedAt,
    label: report.label,
    markerType: report.markerType,
    mapKind: "automatic-report",
    removable: Boolean(meta.removable),
    automatic: true,
    reportType: report.type,
    symbol: report.symbol,
    color: report.color,
    state: report.state,
    approximate: report.approximate,
    landmark: report.landmark,
    routeName: report.routeName,
    fadeOpacity,
  };
  const features = [];
  const add = (kind, geometry, extra = {}) => features.push({type: "Feature", geometry, properties: {...base, kind, ...extra}});

  if (report.shape === "contact") {
    add("route", arrowGeometry([origin, target]), {lineStyle: "danger"});
    add("contact", {type: "Point", coordinates: target});
    return features;
  }
  if (report.shape === "road-block") {
    add("auto-line", {type: "LineString", coordinates: lineAcross(target, bearing, 55)}, {lineStyle: "blocked"});
  } else if (report.shape === "heading" || report.shape === "direction") {
    add("auto-line", arrowGeometry([origin, target]), {lineStyle: report.shape === "heading" ? "movement" : "danger"});
  } else if (report.shape === "uncertainty") {
    add("auto-area", cone(origin, bearing, report.distance || 300), {areaStyle: "uncertain"});
  } else if (report.shape === "hazard-area") {
    add("auto-area", circle(target, 75), {areaStyle: "danger"});
  } else if (report.shape === "warning-area") {
    add("auto-area", circle(target, 120), {areaStyle: "warning"});
  } else if (report.shape === "route-warning") {
    add("auto-line", {type: "LineString", coordinates: lineAcross(origin, 0, 250)}, {lineStyle: "blocked"});
  } else if (report.shape === "search-area") {
    const north = projectPoint(origin, 0, 180);
    const east = projectPoint(origin, 90, 180);
    const south = projectPoint(origin, 180, 180);
    const west = projectPoint(origin, 270, 180);
    add("auto-area", circle(origin, 180, 4), {areaStyle: "search"});
    add("auto-line", {type: "MultiLineString", coordinates: [[north, south], [east, west]]}, {lineStyle: "sector"});
  } else if (report.shape === "phase-line") {
    add("auto-line", {type: "LineString", coordinates: lineAcross(target, bearing, 250)}, {lineStyle: "phase"});
  }

  if (report.shape !== "uncertainty") {
    add("auto-point", {type: "Point", coordinates: report.shape === "heading" ? origin : target});
  }
  return features;
}

export function activeAutomaticReports(items, nowSeconds = Date.now() / 1000) {
  const activeBySender = new Map();
  [...items].sort((a, b) => Number(a.createdAt) - Number(b.createdAt)).forEach((item) => {
    const report = item.report || parseAutomaticReport(item.message);
    if (!report) return;
    const key = item.senderHash || item.callsign || "unknown";
    if (!activeBySender.has(key)) activeBySender.set(key, []);
    if (report.action === "cancel-last") {
      const reports = activeBySender.get(key);
      const index = reports.findLastIndex(item => !report.targetType || item.report.type === report.targetType);
      if (index !== -1) reports.splice(index, 1);
      return;
    }
    const effectiveTime = Number(item.createdAt) - Number(report.occurredAgoSeconds || 0);
    if (nowSeconds - effectiveTime <= report.ttlSeconds) activeBySender.get(key).push({...item, report});
  });
  // Keep dismissed reports in the cancellation stack, then hide them. Otherwise
  // an old spoken cancellation could accidentally consume an earlier contact.
  return [...activeBySender.values()].flat().filter(item => !item.event?.automatic_report_dismissed);
}
