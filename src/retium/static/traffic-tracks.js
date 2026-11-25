// Recent observed fixes only. Never record the display's predicted positions.
export const TRACK_SECONDS = 15 * 60, TRACK_POINTS = 90, TRACK_TARGETS = 500;
export const TRACK_SOURCE = "public-traffic-track";
export const TRACK_ESTIMATE_SOURCE = "public-traffic-track-estimate";
const estimateFrames = new WeakMap();
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

function turnHandle(points, index) {
  const point = points[index], before = points[index - 1], after = points[index + 1];
  if (!before || !after) {
    const from = before || point, to = after || point;
    return [(to[0] - from[0]) / 3, (to[1] - from[1]) / 3];
  }
  const scale = Math.max(.01, Math.cos(point[1] * rad));
  const ax = (point[0] - before[0]) * scale, ay = point[1] - before[1];
  const bx = (after[0] - point[0]) * scale, by = after[1] - point[1];
  const incoming = Math.hypot(ax, ay), outgoing = Math.hypot(bx, by);
  // A short leg limits both handles. Near reversals have no reliable turn direction.
  if (!incoming || !outgoing || (ax * bx + ay * by) / (incoming * outgoing) < -.75) return [0, 0];
  const x = ax / incoming + bx / outgoing, y = ay / incoming + by / outgoing;
  const length = Math.min(incoming, outgoing) / (3 * Math.hypot(x, y));
  return [x * length / scale, y * length];
}

function smoothLine(points) {
  if (points.length < 3) return points;
  const handles = points.map((_, index) => turnHandle(points, index));
  const line = [points[0]];
  for (let index = 1; index < points.length; index++) {
    const a = points[index - 1], b = points[index], start = handles[index - 1], end = handles[index];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const bends = handle => Math.abs(dx * handle[1] - dy * handle[0]) > (dx * dx + dy * dy) * .0001;
    if (bends(start) || bends(end)) {
      // Keep the entire curve on this side of the date line, including its control points.
      const clamp = (value, axis) => Math.max(axis === 0 ? -180 : -90, Math.min(axis === 0 ? 180 : 90, value));
      const c = a.map((value, axis) => clamp(value + start[axis], axis));
      const d = b.map((value, axis) => clamp(value - end[axis], axis));
      for (const t of [.25, .5, .75]) {
        const u = 1 - t;
        line.push(a.map((value, axis) => u ** 3 * value + 3 * u * u * t * c[axis] + 3 * u * t * t * d[axis] + t ** 3 * b[axis]));
      }
    }
    line.push(b);
  }
  return line;
}

// Split gaps and date-line crossings instead of drawing a line across the world.
function segments(points) {
  const lines = []; let line = [];
  const finish = () => { if (line.length > 1) lines.push(smoothLine(line)); line = []; };
  for (const fix of points) {
    if (fix.breakBefore) finish();
    const point = fix.point, previous = line.at(-1);
    if (previous && Math.abs(point[0] - previous[0]) > 180) {
      const lon = previous[0] + wrap(point[0] - previous[0]);
      const edge = previous[0] > 0 ? 180 : -180;
      const lat = lon === previous[0] ? point[1]
        : previous[1] + (point[1] - previous[1]) * (edge - previous[0]) / (lon - previous[0]);
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
  route(key, now = Date.now()/1000, history = null) {
    this.prune(now);
    let points = this.tracks.get(String(key)) || [];
    if (history?.points) {
      const merged = new Map();
      for (const fix of [...history.points.slice(-12000), ...points]) {
        if (finite(fix.time) && fix.time >= now - 86400 && fix.time <= now + 30
            && Array.isArray(fix.point) && finite(fix.point[0]) && finite(fix.point[1])
            && Math.abs(fix.point[0]) <= 180 && Math.abs(fix.point[1]) <= 90) {
          merged.set(fix.time, merged.get(fix.time)?.breakBefore ? {...fix, breakBefore: true} : fix);
        }
      }
      points = [...merged.values()].sort((a,b) => a.time-b.time).map((fix,index,array) => {
        const previous = array[index-1], gap = previous ? fix.time-previous.time : 0;
        return {...fix, breakBefore: fix.breakBefore || previous && (gap > (this.pack === "flights" ? 120 : 600)
          || distance(previous.point,fix.point) > gap*(this.pack === "flights" ? 1100 : 60)+100)};
      });
    }
    const lines = segments(points);
    return {count: points.length, data: {type: "FeatureCollection", features: lines.length ? [{type: "Feature", properties: {traffic_pack: this.pack}, geometry: {type: "MultiLineString", coordinates: lines}}] : []},
      label: history ? `${points.length} fixes · ${history.note || "Available observed track"}` : lines.length ? `${points.length} fixes · recent observed track (up to 15 min)` : "Waiting for consecutive position reports",
    };
  }
}

export function trafficRouteBounds(data, currentPoint = null) {
  const points = (data?.features || []).flatMap(f => f.geometry.coordinates.flat());
  if (points.length < 2) return null;
  if (Array.isArray(currentPoint) && currentPoint.length >= 2 && currentPoint.every(finite)) points.push(currentPoint);
  const longitude = points.map(p => (p[0]+360)%360).sort((a,b)=>a-b);
  let gap = -1, start = 0;
  for (let i=0;i<longitude.length;i++) {
    const next = i+1 === longitude.length ? longitude[0]+360 : longitude[i+1];
    if (next-longitude[i] > gap) { gap=next-longitude[i]; start=next%360; }
  }
  const west = start > 180 ? start-360 : start;
  return [[west,Math.min(...points.map(p=>p[1]))],[west+360-gap,Math.max(...points.map(p=>p[1]))]];
}

export function showTrafficTrack(map, data) {
  if (!map.getSource(TRACK_SOURCE)) map.addSource(TRACK_SOURCE, {type: "geojson", data});
  else map.getSource(TRACK_SOURCE).setData(data);
  if (!map.getLayer(TRACK_SOURCE)) {
    const before = map.getStyle()?.layers?.find(layer => layer.type === "symbol")?.id;
    map.addLayer({id: TRACK_SOURCE, source: TRACK_SOURCE, type: "line", layout: {"line-join": "round", "line-cap": "round"},
      paint: {"line-color": ["case", ["==", ["get", "traffic_pack"], "vessels"], "#a5b8ac", "#c2b58d"], "line-width": 2.5, "line-opacity": .85, "line-dasharray": [3, 2]}}, before);
  }
}

export function showTrafficEstimate(map, feature) {
  const props = feature?.properties, point = feature?.geometry?.coordinates;
  const origin = [props?.reported_lon, props?.reported_lat];
  const estimated = props?.display_estimated && Array.isArray(point) && point.length >= 2
    && [...origin, ...point.slice(0, 2)].every(finite)
    && Math.abs(origin[0]) <= 180 && Math.abs(point[0]) <= 180
    && Math.abs(origin[1]) <= 90 && Math.abs(point[1]) <= 90
    && (point[0] !== origin[0] || point[1] !== origin[1]);
  const source = map.getSource(TRACK_ESTIMATE_SOURCE);
  if (!estimated) {
    if (source) {
      if (map.getLayer(TRACK_ESTIMATE_SOURCE)) map.removeLayer(TRACK_ESTIMATE_SOURCE);
      map.removeSource(TRACK_ESTIMATE_SOURCE);
    }
    return;
  }
  const frame = `${props.traffic_pack}:${origin}:${point[0]},${point[1]}`;
  if (source && estimateFrames.get(source) === frame && map.getLayer(TRACK_ESTIMATE_SOURCE)) return;
  const data = {type: "FeatureCollection", features: [{type: "Feature", properties: {traffic_pack: props.traffic_pack},
    geometry: {type: "MultiLineString", coordinates: segments([{point: origin}, {point: point.slice(0, 2)}])}}]};
  if (source) source.setData(data);
  else map.addSource(TRACK_ESTIMATE_SOURCE, {type: "geojson", data});
  estimateFrames.set(source || map.getSource(TRACK_ESTIMATE_SOURCE), frame);
  if (!map.getLayer(TRACK_ESTIMATE_SOURCE)) {
    const before = map.getStyle()?.layers?.find(layer => layer.type === "symbol")?.id;
    map.addLayer({id: TRACK_ESTIMATE_SOURCE, source: TRACK_ESTIMATE_SOURCE, type: "line",
      layout: {"line-cap": "round"},
      paint: {"line-color": ["case", ["==", ["get", "traffic_pack"], "vessels"], "#a5b8ac", "#c2b58d"],
        "line-width": 2, "line-opacity": .65, "line-dasharray": [1, 2]}}, before);
  }
}

export function clearTrafficTrack(map) {
  for (const id of [TRACK_SOURCE, TRACK_ESTIMATE_SOURCE]) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }
}
