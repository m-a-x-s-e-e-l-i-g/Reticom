const DEFAULT_ROUTE_SERVICE = "https://router.project-osrm.org";

function validPoint(point) {
  return Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(Number(point[0]))
    && Number.isFinite(Number(point[1]))
    && Number(point[0]) >= -180
    && Number(point[0]) <= 180
    && Number(point[1]) >= -90
    && Number(point[1]) <= 90;
}

export function calculatedRouteUrl(origin, target, service = DEFAULT_ROUTE_SERVICE) {
  if (!validPoint(origin) || !validPoint(target)) return null;
  const base = String(service || "").replace(/\/+$/, "");
  if (!base) return null;
  const coordinates = `${Number(origin[0])},${Number(origin[1])};${Number(target[0])},${Number(target[1])}`;
  return `${base}/route/v1/driving/${coordinates}?alternatives=false&steps=true&geometries=geojson&overview=full`;
}

export function parseCalculatedRoute(payload) {
  if (payload?.code !== "Ok" || !Array.isArray(payload.routes) || !payload.routes.length) {
    throw new Error(payload?.code === "NoRoute" ? "No road route found" : "Route service unavailable");
  }
  const route = payload.routes[0];
  const coordinates = route?.geometry?.type === "LineString" ? route.geometry.coordinates : null;
  if (!Array.isArray(coordinates) || coordinates.length < 2 || !coordinates.every(validPoint)) {
    throw new Error("Route service returned invalid geometry");
  }
  return {
    coordinates: coordinates.map((point) => [Number(point[0]), Number(point[1])]),
    distance: Number(route.distance) || 0,
    duration: Number(route.duration) || 0,
    steps: (route.legs || []).flatMap((leg) => Array.isArray(leg.steps) ? leg.steps : []),
  };
}

export function formatRouteDuration(seconds) {
  const minutes = Math.max(1, Math.round(Number(seconds) / 60));
  if (!Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `${minutes} MIN`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} H ${remainder} M` : `${hours} H`;
}

export function routeInstruction(steps) {
  const step = (steps || []).find((item) => Number(item?.distance) >= 5) || (steps || [])[0];
  if (!step) return "FOLLOW ROUTE";
  const type = String(step.maneuver?.type || "continue").replaceAll("_", " ").toUpperCase();
  const modifier = String(step.maneuver?.modifier || "").replaceAll("_", " ").toUpperCase();
  const road = String(step.name || "").trim();
  if (type === "DEPART") return road ? `START · ${road}` : "START ROUTE";
  if (type === "ARRIVE") return "DESTINATION AHEAD";
  return [modifier || type, road].filter(Boolean).join(" · ");
}

export function routeNeedsRefresh(routeOrigin, currentPoint, now, lastAttempt, distanceBetween, thresholdMeters = 80, minimumIntervalMs = 15000) {
  if (!validPoint(currentPoint)) return false;
  if (!validPoint(routeOrigin)) return Number(now) - Number(lastAttempt || 0) >= minimumIntervalMs;
  if (Number(now) - Number(lastAttempt || 0) < minimumIntervalMs) return false;
  return distanceBetween(routeOrigin, currentPoint) >= thresholdMeters;
}
