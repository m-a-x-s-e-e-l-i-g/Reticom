// Recent observed fixes only. Never record the display's predicted positions.
export const TRACK_SECONDS = 15 * 60, TRACK_POINTS = 90, TRACK_TARGETS = 500;
export const TRACK_SOURCE = "public-traffic-track";
const finite = value => typeof value === "number" && Number.isFinite(value);
// MapLibre's tiled feature id can differ from a string GeoJSON id. The provider
// identity property survives tiling and identifies the same target at click time.
export const trafficKey = feature => String(feature?.properties?.identity ?? feature?.id ?? "");
const wrap = value => ((value + 180) % 360 + 360) % 360 - 180;
const rad = Math.PI / 180;
function distance(a, b) {
  const h = Math.sin((b[1]-a[1])*rad/2)**2 + Math.cos(a[1]*rad)*Math.cos(b[1]*rad)*Math.sin((b[0]-a[0])*rad/2)**2;
  return 12742017.6 * Math.asin(Math.sqrt(Math.min(1, h)));
}

// Split gaps and date-line crossings instead of drawing a line across the world.
function segments(points) {
  const lines = []; let line = [];
  const finish = () => { if (line.length > 1) lines.push(line); line = []; };
  for (const fix of points) {
    if (fix.breakBefore) finish();
    const point = fix.point, previous = line.at(-1);
    if (previous && Math.abs(point[0] - previous[0]) > 180) {
      const lon = previous[0] + wrap(point[0] - previous[0]);
      const edge = lon > 180 ? 180 : -180;
      const lat = previous[1] + (point[1] - previous[1]) * (edge - previous[0]) / (lon - previous[0]);
      line.push([edge, lat]); finish(); line.push([-edge, lat]);
    }
    line.push([...point]);
  }
  finish(); return lines;
}

export class TrafficTracks {
  constructor(pack) { this.pack = pack; this.tracks = new Map(); }
  clear() { this.tracks.clear(); }
  prune(now) {
    for (const [key, points] of this.tracks) {
      while (points.length && points[0].time < now - TRACK_SECONDS) points.shift();
      if (!points.length) this.tracks.delete(key);
    }
  }
  ingest(data, now = Date.now()/1000) {
    this.prune(now);
    for (const feature of data?.features || []) {
      const p = feature.properties || {}, point = feature.geometry?.coordinates, time = p.position_time, key = trafficKey(feature);
      if (!key || p.traffic_pack !== this.pack || feature.geometry?.type !== "Point" || !Array.isArray(point)
        || !finite(point[0]) || !finite(point[1]) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 90
        || !finite(time) || time > now + 30 || now - time > (this.pack === "flights" ? 120 : 600)
        || p.display_estimated || p.prediction_seconds > 0) continue;
      const points = this.tracks.get(key) || [], last = points.at(-1);
      if (last && time <= last.time) continue; // Cached/older reports do not grow history.
      const gap = last ? time - last.time : 0;
      const breakBefore = last && (gap > (this.pack === "flights" ? 120 : 600)
        || distance(last.point, point) > gap * (this.pack === "flights" ? 1100 : 60) + 100);
      points.push({point: point.slice(0, 2), time, breakBefore: Boolean(breakBefore)});
      if (points.length > TRACK_POINTS) points.splice(0, points.length - TRACK_POINTS);
      // Least-recently-reported eviction bounds memory even while panning widely.
      this.tracks.delete(key); this.tracks.set(key, points);
      while (this.tracks.size > TRACK_TARGETS) this.tracks.delete(this.tracks.keys().next().value);
    }
  }
  route(key, now = Date.now()/1000) {
    this.prune(now);
    const points = this.tracks.get(String(key)) || [], lines = segments(points);
    return {count: points.length, data: {type: "FeatureCollection", features: lines.length ? [{type: "Feature", properties: {traffic_pack: this.pack}, geometry: {type: "MultiLineString", coordinates: lines}}] : []},
      label: lines.length ? `${points.length} fixes · recent observed track (up to 15 min)` : "Waiting for consecutive position reports",
    };
  }
}

export function showTrafficTrack(map, data) {
  if (!map.getSource(TRACK_SOURCE)) map.addSource(TRACK_SOURCE, {type: "geojson", data});
  else map.getSource(TRACK_SOURCE).setData(data);
  if (!map.getLayer(TRACK_SOURCE)) {
    const before = map.getStyle()?.layers?.find(layer => layer.type === "symbol")?.id;
    map.addLayer({id: TRACK_SOURCE, source: TRACK_SOURCE, type: "line", layout: {"line-join": "round", "line-cap": "butt"},
      paint: {"line-color": ["case", ["==", ["get", "traffic_pack"], "vessels"], "#a5b8ac", "#c2b58d"], "line-width": 2.5, "line-opacity": .85, "line-dasharray": [3, 2]}}, before);
  }
}
export function clearTrafficTrack(map) {
  if (map.getLayer(TRACK_SOURCE)) map.removeLayer(TRACK_SOURCE);
  if (map.getSource(TRACK_SOURCE)) map.removeSource(TRACK_SOURCE);
}
