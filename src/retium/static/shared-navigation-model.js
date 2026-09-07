// Shared plans are signed state, not a live GPS track. The authenticated sender
// owns one current plan; a newer stop remains authoritative even after replay.
export const MAX_SHARED_ROUTE_POINTS = 4000;
export const MAX_SHARED_ROUTE_BYTES = 12000;
const MAX_REVISION = 1_000_000_000;
const IDENTITY = /^[a-f0-9]{32}$/;
const ROUTE_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const PALETTE = ["#899a78", "#8cabc0", "#b8a16d", "#a393af", "#79a59a", "#b59e91"];
const validPoint = (point) => Array.isArray(point) && point.length === 2
  && Number.isFinite(point[0]) && Math.abs(point[0]) <= 180
  && Number.isFinite(point[1]) && Math.abs(point[1]) <= 90;

export function encodeRouteGeometry(coordinates) {
  if (!Array.isArray(coordinates) || coordinates.length < 2 || coordinates.length > MAX_SHARED_ROUTE_POINTS
      || !coordinates.every(validPoint)) throw new Error("Invalid shared route coordinates");
  let encoded = "";
  const previous = [0, 0];
  for (const [lon, lat] of coordinates) {
    for (const [axis, value] of [lat, lon].entries()) {
      const scaled = Math.round(value * 100000);
      const delta = scaled - previous[axis];
      previous[axis] = scaled;
      let part = delta < 0 ? -delta * 2 - 1 : delta * 2;
      while (part >= 32) {
        encoded += String.fromCharCode((part % 32) + 95);
        part = Math.floor(part / 32);
      }
      encoded += String.fromCharCode(part + 63);
    }
  }
  if (encoded.length > MAX_SHARED_ROUTE_BYTES) throw new Error("Shared route geometry is too large");
  return encoded;
}

export function decodeRouteGeometry(encoded) {
  if (typeof encoded !== "string" || encoded.length < 2 || encoded.length > MAX_SHARED_ROUTE_BYTES) {
    throw new Error("Invalid shared route geometry");
  }
  let offset = 0;
  const previous = [0, 0], coordinates = [];
  while (offset < encoded.length) {
    for (let axis = 0; axis < 2; axis++) {
      let value = 0, shift = 0, part;
      do {
        if (offset >= encoded.length || shift > 30) throw new Error("Invalid shared route geometry");
        part = encoded.charCodeAt(offset++) - 63;
        if (part < 0 || part > 63) throw new Error("Invalid shared route geometry");
        // Arithmetic avoids signed 32-bit bitwise overflow on malformed input.
        value += (part % 32) * 2 ** shift;
        shift += 5;
      } while (part >= 32);
      previous[axis] += value % 2 ? -(Math.floor(value / 2) + 1) : value / 2;
    }
    const point = [previous[1] / 100000, previous[0] / 100000];
    if (!validPoint(point) || coordinates.length >= MAX_SHARED_ROUTE_POINTS) throw new Error("Invalid shared route geometry");
    coordinates.push(point);
  }
  if (coordinates.length < 2 || encodeRouteGeometry(coordinates) !== encoded) throw new Error("Invalid shared route geometry");
  return coordinates;
}

function normalizePlan(item) {
  if (!item || typeof item !== "object") return null;
  const rawEvent = item.type === "navigation.updated" || item.type === "navigation.stopped";
  if (!rawEvent && (item.type || typeof item.active !== "boolean")) return null;
  // Never let a sender_hash inside a raw payload replace envelope identity.
  const sender = item.network?.sender_hash || (!rawEvent ? item.sender_hash : "");
  if (!IDENTITY.test(sender || "") || !ROUTE_ID.test(item.route_id || "")
      || !Number.isInteger(item.revision) || item.revision < 1 || item.revision > MAX_REVISION
      || typeof item.id !== "string" || !item.id
      || (item.network?.verified === false && item.network?.queued !== true)) return null;
  const active = rawEvent ? item.type === "navigation.updated" : item.active;
  const plan = {...item, sender_hash: sender, active, coordinates: []};
  plan.queued = item.network?.queued === true || item.network?.delivery_status === "queued" || item.queued === true;
  plan.updated_at = Number.isFinite(item.created_at) ? item.created_at : 0;
  if (!active) return plan;
  if (!["direct", "route"].includes(item.mode) || !validPoint(item.origin) || !validPoint(item.target)
      || typeof item.label !== "string" || !item.label.trim() || item.label.trim().length > 80) return null;
  try {
    if (rawEvent) plan.coordinates = decodeRouteGeometry(item.geometry);
    else {
      if (!Array.isArray(item.coordinates) || item.coordinates.length < 2 || item.coordinates.length > MAX_SHARED_ROUTE_POINTS
          || !item.coordinates.every(validPoint)) return null;
      plan.coordinates = item.coordinates.map((point) => [...point]);
    }
  } catch { return null; }
  if (item.mode === "direct" && (plan.coordinates.length !== 2
      || plan.coordinates.some((point, index) => point.some((value, axis) => Math.abs(value - [item.origin, item.target][index][axis]) > .0000051)))) return null;
  plan.origin = [...item.origin];
  plan.target = [...item.target];
  plan.label = item.label.trim();
  return plan;
}

function comparePlans(left, right) {
  if (left.revision !== right.revision) return left.revision - right.revision;
  if (left.active !== right.active) return left.active ? -1 : 1;
  if (left.id !== right.id) return left.id > right.id ? 1 : -1;
  // The same event may be present as both a local queue item and its receipt.
  return Number(right.queued) - Number(left.queued);
}

export function latestNavigationPlans(items, {includeStopped = false} = {}) {
  const latest = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const plan = normalizePlan(item);
    if (!plan) continue;
    const previous = latest.get(plan.sender_hash);
    if (!previous || comparePlans(plan, previous) > 0) latest.set(plan.sender_hash, plan);
  }
  return [...latest.values()].filter((plan) => includeStopped || plan.active)
    .sort((left, right) => left.sender_hash.localeCompare(right.sender_hash));
}

export function navigationColor(sender) {
  let hash = 0;
  for (const character of String(sender)) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

export function sharedNavigationFeatures(items, {ownIdentity = "", colorForSender, nowSeconds = Date.now() / 1000, staleAfterSeconds = 0} = {}) {
  const features = [];
  for (const plan of latestNavigationPlans(items)) {
    const own = plan.sender_hash === ownIdentity;
    const chosenColor = colorForSender?.(plan.sender_hash, plan) || plan.color;
    const color = /^#[a-f0-9]{6}$/i.test(chosenColor || "") ? chosenColor : navigationColor(plan.sender_hash);
    const stale = staleAfterSeconds > 0 && nowSeconds - plan.updated_at > staleAfterSeconds;
    const callsign = String(plan.callsign || "Operator");
    const properties = {
      senderHash: plan.sender_hash, routeId: plan.route_id, revision: plan.revision,
      eventId: plan.id, mode: plan.mode, targetKind: plan.target_kind, targetId: plan.target_id,
      label: plan.label, callsign, caption: `${callsign} → ${plan.label}`,
      distanceM: Number(plan.distance_m) || 0, durationS: Number(plan.duration_s) || 0,
      updatedAt: plan.updated_at, queued: plan.queued, own, stale, color,
      opacity: stale ? .35 : own ? .5 : .8,
      description: `${callsign} → ${plan.label} · ${plan.mode === "route" ? "shared road route" : "shared straight line"}${plan.queued ? " · queued" : ""}`,
    };
    const id = `${plan.sender_hash}:${plan.route_id}`;
    features.push({type: "Feature", id: `${id}:route`, geometry: {type: "LineString", coordinates: plan.coordinates},
      properties: {...properties, kind: "shared-navigation-route"}});
    features.push({type: "Feature", id: `${id}:target`, geometry: {type: "Point", coordinates: plan.target},
      properties: {...properties, kind: "shared-navigation-target", symbol: "NAV"}});
  }
  return {type: "FeatureCollection", features};
}
