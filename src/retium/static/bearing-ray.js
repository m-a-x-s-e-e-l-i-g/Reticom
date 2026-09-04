export function bearingRayEndPixel(start, heading, viewportWidth, viewportHeight, reach = 2.5) {
  const degrees = Number(heading);
  const width = Number(viewportWidth);
  const height = Number(viewportHeight);
  if (!start || !Number.isFinite(start.x) || !Number.isFinite(start.y)) return null;
  if (!Number.isFinite(degrees) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  const radians = ((degrees % 360) + 360) % 360 * Math.PI / 180;
  const distance = Math.hypot(width, height) * reach;
  return {
    x: start.x + Math.sin(radians) * distance,
    y: start.y - Math.cos(radians) * distance,
  };
}

export function bearingRayFeature(origin, endpoint, heading) {
  if (!Array.isArray(origin) || !Array.isArray(endpoint) || origin.length < 2 || endpoint.length < 2) return null;
  if (![...origin.slice(0, 2), ...endpoint.slice(0, 2), Number(heading)].every(Number.isFinite)) return null;
  return {
    type: "Feature",
    geometry: {type: "LineString", coordinates: [origin.slice(0, 2), endpoint.slice(0, 2)]},
    properties: {kind: "local-bearing", heading: Number(heading)},
  };
}
