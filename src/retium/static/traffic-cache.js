// Keep observations across viewport changes; timestamps remain provider times.
export const TRAFFIC_CACHE_SECONDS = 30 * 60;
export const TRAFFIC_CACHE_TARGETS = 5000;
const key = feature => String(feature?.properties?.identity || feature?.id || "");

export function retainTraffic(previous, incoming, now = Date.now() / 1000) {
  const targets = new Map();
  for (const feature of [...(previous?.features || []), ...(incoming?.features || [])]) {
    const identity = key(feature), time = feature.properties?.position_time;
    if (!identity || typeof time !== "number" || !Number.isFinite(time) || time < now - TRAFFIC_CACHE_SECONDS || time > now + 30) continue;
    if (!targets.has(identity) || time >= targets.get(identity).properties.position_time) targets.set(identity, feature);
  }
  const features = [...targets.values()]
    .sort((a, b) => b.properties.position_time - a.properties.position_time).slice(0, TRAFFIC_CACHE_TARGETS);
  return {...incoming, features,
    status: features.length && ["waiting", "error", "zoom_in"].includes(incoming.status) ? "stale" : incoming.status};
}
