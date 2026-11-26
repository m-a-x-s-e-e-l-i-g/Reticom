export function mapLocationInfo({lat, lon}, mgrs = null) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90) return null;
  const longitude = Math.abs(lon) <= 180 ? lon : ((lon + 180) % 360 + 360) % 360 - 180;
  const gps = `${lat.toFixed(6)}, ${longitude.toFixed(6)}`;
  const query = new URLSearchParams({api: "1", query: gps.replace(" ", "")});
  return {gps, mapsUrl: `https://www.google.com/maps/search/?${query}`,
    mgrs: mgrs ? mgrs.forward([longitude, lat], 5) : ""};
}
