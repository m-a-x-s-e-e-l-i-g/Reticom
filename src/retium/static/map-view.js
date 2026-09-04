export function mapMarkerIds(events) {
  return new Set((events || [])
    .filter((event) => event?.type === "marker.created" && typeof event.id === "string" && event.id)
    .map((event) => event.id));
}

export function hasAddedMapMarker(previousIds, nextIds, mapHasRendered) {
  if (!mapHasRendered) return false;
  return [...nextIds].some((id) => !previousIds.has(id));
}

export function shouldInitiallyFrameMap(hasFramedData, coordinateCount) {
  return !hasFramedData && Number(coordinateCount) > 0;
}

export function captureMapCamera(map) {
  const center = map.getCenter();
  return {
    center: [Number(center.lng), Number(center.lat)],
    zoom: Number(map.getZoom()),
    bearing: Number(map.getBearing?.() || 0),
    pitch: Number(map.getPitch?.() || 0),
  };
}

export function restoreMapCamera(map, camera) {
  if (!map || !camera) return;
  map.jumpTo({
    center: camera.center,
    zoom: camera.zoom,
    bearing: camera.bearing,
    pitch: camera.pitch,
  });
}
