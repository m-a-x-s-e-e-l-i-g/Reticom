// Retain visited public areas while other regions load. Provider coverage and
// explicit deletions still matter: a complete newer view replaces its area.
export const INTEL_CACHE_FEATURES = 10000, INTEL_CACHE_POSITIONS = 250000;
function extent(coordinates, bounds = [Infinity, Infinity, -Infinity, -Infinity]) {
  if (!Array.isArray(coordinates)) return bounds;
  if (typeof coordinates[0] === "number" && typeof coordinates[1] === "number") {
    bounds[0] = Math.min(bounds[0], coordinates[0]); bounds[1] = Math.min(bounds[1], coordinates[1]);
    bounds[2] = Math.max(bounds[2], coordinates[0]); bounds[3] = Math.max(bounds[3], coordinates[1]);
  } else for (const part of coordinates) extent(part, bounds);
  return bounds;
}
function positions(coordinates) {
  if (!Array.isArray(coordinates)) return 0;
  return typeof coordinates[0] === "number" ? 1 : coordinates.reduce((sum, part) => sum + positions(part), 0);
}
export function retainIntel(previous, incoming, boxes = []) {
  const complete = ["fresh", "cached"].includes(incoming.status) && incoming.coverage_complete !== false && !incoming.truncated && !incoming.capped;
  const features = [], seen = new Set(); let count = 0, limited = false;
  for (const [items, old] of [[incoming.features || [], false], [previous?.features || [], true]]) {
    for (const feature of items) {
      if (old && complete) {
        const [w, s, e, n] = extent(feature.geometry?.coordinates);
        if (boxes.some(b => b.west <= w && b.south <= s && b.east >= e && b.north >= n)) continue;
      }
      const key = feature.id ?? feature.properties?.id ?? JSON.stringify(feature);
      if (seen.has(key)) continue;
      const size = positions(feature.geometry?.coordinates);
      if (features.length >= INTEL_CACHE_FEATURES || count + size > INTEL_CACHE_POSITIONS) { limited = true; continue; }
      seen.add(key); features.push(feature); count += size;
    }
  }
  return {...incoming, features, retained_cache: true, cache_limited: limited,
    status: features.length && ["error", "waiting", "zoom_in"].includes(incoming.status) ? "stale" : incoming.status};
}
