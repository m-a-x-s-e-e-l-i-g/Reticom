export function normalizeHeading(value) {
  const heading = Number(value);
  if (!Number.isFinite(heading)) return null;
  return ((heading % 360) + 360) % 360;
}

export function compassCardinal(value) {
  const heading = normalizeHeading(value);
  if (heading === null) return "—";
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return names[Math.round(heading / 45) % names.length];
}

export function compassTicks(value, span = 120, step = 5) {
  const heading = normalizeHeading(value);
  if (heading === null) return [];
  const first = Math.ceil((heading - span / 2) / step) * step;
  const ticks = [];
  for (let raw = first; raw <= heading + span / 2; raw += step) {
    const degrees = normalizeHeading(raw);
    const major = degrees % 15 === 0;
    const cardinal = degrees % 45 === 0;
    ticks.push({
      degrees,
      left: ((raw - heading + span / 2) / span) * 100,
      major,
      label: major ? (cardinal ? compassCardinal(degrees) : String(degrees).padStart(3, "0")) : "",
    });
  }
  return ticks;
}

export function compassTargetIndicator(headingValue, bearingValue, span = 120, edgePadding = 3) {
  const heading = normalizeHeading(headingValue);
  const bearing = normalizeHeading(bearingValue);
  const visibleSpan = Number(span);
  const padding = Number(edgePadding);
  if (heading === null || bearing === null || !Number.isFinite(visibleSpan) || visibleSpan <= 0) return null;
  const offset = ((bearing - heading + 540) % 360) - 180;
  const unclamped = 50 + (offset / visibleSpan) * 100;
  const safePadding = Number.isFinite(padding) ? Math.max(0, Math.min(49, padding)) : 0;
  const left = Math.max(safePadding, Math.min(100 - safePadding, unclamped));
  return {
    left,
    offset,
    offscreen: Math.abs(offset) > visibleSpan / 2,
    side: offset < 0 ? "left" : offset > 0 ? "right" : "center",
  };
}
