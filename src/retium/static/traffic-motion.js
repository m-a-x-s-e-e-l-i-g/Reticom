// Display-only dead reckoning. Source reports/timestamps are never rewritten.
import {TRAFFIC_CACHE_SECONDS} from "./traffic-cache.js?v=20260909-3";
export const PREDICTION_SECONDS = {flights: 30, vessels: 60};
const RAD = Math.PI / 180, EARTH = 6371008.8;
const finite = value => typeof value === "number" && Number.isFinite(value);
const wrap = value => ((value + 180) % 360 + 360) % 360 - 180;
const validPoint = point => Array.isArray(point) && finite(point[0]) && finite(point[1]) && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90;
const angle = value => finite(value) && value >= 0 && value < 360;

export function projectTraffic(point, bearing, meters) {
  const lat = point[1] * RAD, lon = point[0] * RAD, heading = bearing * RAD, distance = meters / EARTH;
  const nextLat = Math.asin(Math.max(-1, Math.min(1, Math.sin(lat) * Math.cos(distance) + Math.cos(lat) * Math.sin(distance) * Math.cos(heading))));
  const nextLon = lon + Math.atan2(Math.sin(heading) * Math.sin(distance) * Math.cos(lat), Math.cos(distance) - Math.sin(lat) * Math.sin(nextLat));
  return [wrap(nextLon / RAD), nextLat / RAD];
}

function prediction(id, feature, now, animate) {
  const p = feature.properties, age = now - p.position_time;
  // AIS bow heading is not course over ground (e.g. currents or reversing).
  const course = id === "vessels" ? p.course : (p.course ?? p.heading);
  const moving = animate && finite(age) && age >= 0 && angle(course) && finite(p.speed_knots)
    && p.speed_knots > (id === "vessels" ? .5 : 0) && p.speed_knots <= (id === "vessels" ? 102.2 : 2000)
    && !(id === "vessels" && [1, 5].includes(p.navigation_status));
  const seconds = moving ? Math.min(age, PREDICTION_SECONDS[id]) : 0;
  return {point: seconds ? projectTraffic(feature.geometry.coordinates, course, p.speed_knots * 1852 / 3600 * seconds) : [...feature.geometry.coordinates],
    seconds, active: moving && age < PREDICTION_SECONDS[id], heading: p.heading};
}

export class TrafficMotion {
  constructor(id) { this.id = id; this.tracks = new Map(); }
  clear() { this.tracks.clear(); }
  pose(track, now, animate) {
    const pose = prediction(this.id, track.feature, now, animate);
    const t = Math.max(0, Math.min(1, (now - track.received) / 1));
    const remaining = animate ? 1 - t * t * (3 - 2 * t) : 0;
    if (track.offset && remaining > 0) {
      pose.point = [wrap(pose.point[0] + track.offset[0] * remaining), pose.point[1] + track.offset[1] * remaining];
      if (finite(track.rotation) && angle(pose.heading)) pose.heading = (pose.heading + track.rotation * remaining + 360) % 360;
      pose.active = true;
      pose.correcting = true;
    }
    return pose;
  }
  sample(data, now, animate = true) {
    let active = false;
    const seen = new Set(), features = [];
    for (const incoming of data.features || []) {
      if (!validPoint(incoming.geometry?.coordinates) || !finite(incoming.properties?.position_time)) continue;
      const key = incoming.id ?? incoming.properties.identity;
      if (key == null) continue;
      seen.add(key);
      let track = this.tracks.get(key);
      const p = incoming.properties;
      const signature = JSON.stringify([p.position_time, incoming.geometry.coordinates, p.course, p.heading, p.speed_knots, p.navigation_status]);
      if (!track || p.position_time >= track.feature.properties.position_time) {
        if (!track || track.signature !== signature) {
          const prior = track && animate ? this.pose(track, now, true) : null;
          const target = prediction(this.id, incoming, now, animate);
          const offset = prior ? [wrap(prior.point[0] - target.point[0]), prior.point[1] - target.point[1]] : null;
          const errorMeters = offset ? Math.hypot(offset[0] * Math.cos(target.point[1] * RAD), offset[1]) * RAD * EARTH : Infinity;
          track = {feature: incoming, signature, received: now,
            // Never animate a teleport or reacquisition across a whole region.
            offset: errorMeters <= (this.id === "flights" ? 2500 : 300) ? offset : null,
            rotation: prior && angle(prior.heading) && angle(p.heading) ? wrap(prior.heading - p.heading) : null};
          this.tracks.set(key, track);
        } else track.feature = incoming; // Metadata changes do not restart interpolation.
      }
      if (!animate) track.offset = null;
      const pose = this.pose(track, now, animate);
      active ||= pose.active;
      const original = track.feature, age = now - original.properties.position_time;
      if (age > TRAFFIC_CACHE_SECONDS && !original.properties.traffic_selected) continue;
      features.push({...original, geometry: {...original.geometry, coordinates: pose.point}, properties: {...original.properties,
        heading: pose.heading, stale: age > (this.id === "flights" ? 45 : 120),
        reported_lon: original.geometry.coordinates[0], reported_lat: original.geometry.coordinates[1],
        prediction_seconds: pose.seconds, display_estimated: pose.seconds > 0 || Boolean(pose.correcting),
        motion_status: pose.correcting ? "Correcting to new report" : pose.seconds ? pose.active ? "Estimated between reports" : "Estimate held · waiting for update" : "Reported position",
      }});
    }
    for (const key of this.tracks.keys()) if (!seen.has(key)) this.tracks.delete(key);
    return {data: {...data, features}, active};
  }
}

// One bounded UI loop, separate from provider polling. Idle/hidden maps stop it.
export function createTrafficAnimator({paint, visible, requestFrame = globalThis.requestAnimationFrame?.bind(globalThis), cancelFrame = globalThis.cancelAnimationFrame?.bind(globalThis), clock = () => Date.now() / 1000}) {
  let handle = null, last = -Infinity, stopped = false;
  const frame = timestamp => {
    handle = null;
    if (stopped || !visible()) return;
    let active = true;
    if (timestamp - last >= 1000 / 30) { last = timestamp; active = paint(clock()); }
    if (active) handle = requestFrame(frame);
  };
  return {
    start() { if (!stopped && handle === null && requestFrame && visible()) { last = -Infinity; handle = requestFrame(frame); } },
    stop() { if (handle !== null) cancelFrame?.(handle); handle = null; },
    destroy() { stopped = true; this.stop(); },
  };
}
