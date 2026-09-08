const listeners = new Set();
export function onLandmarkResolved(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Start lookups without holding up map rendering. Subsequent map refreshes pick
// up the result. Keep successful locations stable for the same reference fix.
export function createLandmarkResolver(fetcher = (...args) => fetch(...args), now = () => Date.now(), onResolved = () => {}) {
  const cache = new Map();
  return (report, origin) => {
    if (!report.landmark) return null;
    const key = JSON.stringify([report.landmark, origin]);
    const existing = cache.get(key);
    if (existing && (existing.pending || existing.location || existing.retryAt > now())) return existing.location;
    const entry = {pending: true, location: null, retryAt: 0};
    cache.set(key, entry);
    if (cache.size > 256) cache.delete(cache.keys().next().value);
    const params = new URLSearchParams({q: report.landmark, lon: origin[0], lat: origin[1]});
    Promise.resolve().then(() => fetcher(`/api/map/landmark?${params}`, {signal: AbortSignal.timeout(20000)}))
      .then(async response => {
        if (!response.ok) throw new Error("Landmark lookup unavailable");
        const {landmark} = await response.json();
        const point = landmark?.coordinates;
        if (Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
            && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90) entry.location = landmark;
      })
      .catch(() => {})
      .finally(() => { entry.pending = false; entry.retryAt = now() + 60000; if (entry.location) onResolved(entry.location); });
    return null;
  };
}

export const resolveReportLandmark = createLandmarkResolver(undefined, undefined, () => { for (const listener of listeners) listener(); });
