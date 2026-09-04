const EARTH_RADIUS_METERS = 6371000;

export const MAX_AUTOMATIC_ACCURACY_METERS = 100;
export const MAX_DISPLAY_ACCURACY_METERS = 150;

export function locationTime(event) {
  return Number(event.network?.received_at || event.created_at || 0);
}

export function locationAccuracy(event) {
  const accuracy = Number(event.accuracy);
  return Number.isFinite(accuracy) && accuracy > 0 ? accuracy : MAX_AUTOMATIC_ACCURACY_METERS;
}

export function distanceMeters(first, second) {
  const radians = Math.PI / 180;
  const lat1 = first[1] * radians;
  const lat2 = second[1] * radians;
  const deltaLat = (second[1] - first[1]) * radians;
  const deltaLon = (second[0] - first[0]) * radians;
  const haversine = Math.sin(deltaLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLon / 2) ** 2;
  return EARTH_RADIUS_METERS * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

function point(event) {
  return [Number(event.lon), Number(event.lat)];
}

function plausibleDistance(origin, candidate, maximumSpeed) {
  const elapsed = Math.max(1, locationTime(candidate) - locationTime(origin));
  const uncertainty = 2 * (locationAccuracy(origin) + locationAccuracy(candidate));
  return Math.max(75, uncertainty, maximumSpeed * elapsed);
}

function isPlausible(origin, candidate, maximumSpeed) {
  return distanceMeters(point(origin), point(candidate)) <= plausibleDistance(origin, candidate, maximumSpeed);
}

function smoothAccepted(accepted) {
  return accepted.map((event, index) => {
    const currentTime = locationTime(event);
    const window = accepted
      .slice(Math.max(0, index - 2), index + 1)
      .filter((candidate) => currentTime - locationTime(candidate) <= 90);
    let latitude = 0;
    let longitude = 0;
    let totalWeight = 0;
    window.forEach((candidate, windowIndex) => {
      const accuracy = Math.max(5, Math.min(MAX_DISPLAY_ACCURACY_METERS, locationAccuracy(candidate)));
      const recency = 2 ** (windowIndex - (window.length - 1));
      const weight = recency / (accuracy ** 2);
      latitude += candidate.lat * weight;
      longitude += candidate.lon * weight;
      totalWeight += weight;
    });
    return {
      ...event,
      raw_lat: event.lat,
      raw_lon: event.lon,
      lat: latitude / totalWeight,
      lon: longitude / totalWeight,
    };
  });
}

/**
 * Produces map-safe positions for one operator. Poor fixes are ignored once a
 * useful anchor exists. A physically implausible relocation must be confirmed
 * by the next fix, which removes the common single-fix GPS teleport. Accepted
 * points are then averaged using reported accuracy and recency.
 */
export function displayPositionSeries(events) {
  const positions = events
    .filter((event) => event.type === "position.updated" && Number.isFinite(event.lat) && Number.isFinite(event.lon))
    .sort((a, b) => locationTime(a) - locationTime(b));
  const accepted = [];
  let anchor = null;
  let pendingRelocation = null;

  positions.forEach((event) => {
    if (anchor && locationAccuracy(event) > MAX_DISPLAY_ACCURACY_METERS) return;
    if (!anchor) {
      if (locationAccuracy(event) > MAX_DISPLAY_ACCURACY_METERS) return;
      accepted.push(event);
      anchor = event;
      return;
    }
    if (isPlausible(anchor, event, 35)) {
      accepted.push(event);
      anchor = event;
      pendingRelocation = null;
      return;
    }
    if (pendingRelocation && isPlausible(pendingRelocation, event, 250)) {
      // The second consistent fix confirms genuine rapid movement. We omit the
      // unconfirmed point so the line never draws through a possible teleport.
      accepted.push(event);
      anchor = event;
      pendingRelocation = null;
      return;
    }
    pendingRelocation = event;
  });

  return smoothAccepted(accepted);
}
