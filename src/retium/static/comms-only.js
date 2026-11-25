const MAX_FIX_AGE_MS = 5 * 60 * 1000;

export function commsPositionEvent(fix, now = Date.now()) {
  if (!fix) return null;
  const {latitude, longitude, accuracy, timestamp} = fix;
  const age = now - timestamp;
  if (![latitude, longitude, accuracy, timestamp].every(Number.isFinite)
      || Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || accuracy <= 0 || accuracy > 100_000
      || age < 0 || age > MAX_FIX_AGE_MS) return null;
  return {type: "position.updated", lat: latitude, lon: longitude, accuracy, created_at: Math.floor(timestamp / 1000)};
}
