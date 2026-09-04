const MAX_MERCATOR_LATITUDE = 85.05112878;

function tileX(longitude, zoom) {
  const scale = 2 ** zoom;
  return Math.min(scale - 1, Math.max(0, Math.floor((longitude + 180) / 360 * scale)));
}

function tileY(latitude, zoom) {
  const clamped = Math.min(MAX_MERCATOR_LATITUDE, Math.max(-MAX_MERCATOR_LATITUDE, latitude));
  const radians = clamped * Math.PI / 180;
  const scale = 2 ** zoom;
  return Math.min(scale - 1, Math.max(0, Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * scale)));
}

export function tileCountForBounds(bounds, maxZoom, stopAfter = Number.POSITIVE_INFINITY) {
  if (!bounds || !Number.isInteger(maxZoom) || maxZoom < 0) return 0;
  const {west, south, east, north} = bounds;
  if (![west, south, east, north].every(Number.isFinite) || west >= east || south >= north) return 0;
  let count = 0;
  for (let zoom = 0; zoom <= maxZoom; zoom += 1) {
    count += (tileX(east, zoom) - tileX(west, zoom) + 1) * (tileY(south, zoom) - tileY(north, zoom) + 1);
    if (count > stopAfter) return count;
  }
  return count;
}

export function formatMapBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${Math.round(value / 1024)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(value < 10 * 1024 ** 2 ? 1 : 0)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

export function packProgress(pack) {
  if (!pack?.tile_count) return 0;
  return Math.min(100, Math.round((Number(pack.downloaded_tiles) || 0) / pack.tile_count * 100));
}
