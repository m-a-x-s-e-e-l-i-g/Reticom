// A hiking style on our existing, cacheable OpenMapTiles data. No new tile host.
export const ROAD_COLOR = "#45483f";
export const PATH_FILTER = ["match", ["get", "class"], ["path", "track"], true, false];
// Migrate the former plain-dark default; satellite remains an explicit choice.
export const preferredMapStyle = (stored, online = true) => stored === "satellite" && online ? "satellite" : "hiking";
export const nextMapStyle = (current, requested) => typeof requested === "string" ? preferredMapStyle(requested) : current === "satellite" ? "hiking" : "satellite";
export function roadAndPathLayers() {
  const transport = {type: "line", source: "offline", "source-layer": "transportation"};
  const roads = ["!", structuredClone(PATH_FILTER)];
  return [
    {...transport, id: "offline-road-casing", filter: roads, paint: {"line-color": "#080a08", "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.7, 10, 2.2, 14, 5]}},
    {...transport, id: "offline-roads", filter: structuredClone(roads), paint: {"line-color": ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 10, 1, 14, 2.6]}},
    {...transport, id: "offline-paths", filter: structuredClone(PATH_FILTER), paint: {"line-color": ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 10, 1, 14, 2.6], "line-dasharray": [3, 2]}},
  ];
}

export function hikingMapStyle(base) {
  const style = structuredClone(base);
  style.name = "Reticom Hiking";
  // The hiking path layer replaces the standard one; never draw a solid
  // transportation stroke underneath its dash gaps.
  style.layers = style.layers.filter(layer => layer.id !== "offline-paths");
  const labelIndex = style.layers.findIndex(layer => layer.type === "symbol");
  const before = labelIndex < 0 ? style.layers.length : labelIndex;
  const paths = structuredClone(PATH_FILTER);
  const labels = {"text-font": ["Noto Sans Regular"], "text-size": 11};
  const paint = {"text-color": "#b5b99b", "text-halo-color": "#10160f", "text-halo-width": 1.5};
  style.layers.splice(before, 0,
    {id: "hiking-paths", type: "line", source: "offline", "source-layer": "transportation",
      filter: paths, paint: {"line-color": ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 16, 2.5], "line-dasharray": [3, 2]}},
    {id: "hiking-path-names", type: "symbol", source: "offline", "source-layer": "transportation_name", minzoom: 13,
      filter: paths, layout: {...labels, "symbol-placement": "line", "text-field": ["coalesce", ["get", "name"], ["get", "ref"], ""]}, paint},
    {id: "hiking-peaks", type: "symbol", source: "offline", "source-layer": "mountain_peak", minzoom: 10,
      layout: {...labels, "text-field": ["concat", "△ ", ["coalesce", ["get", "name"], ""], " ", ["to-string", ["get", "ele"]], " m"]}, paint},
    {id: "hiking-parks", type: "symbol", source: "offline", "source-layer": "park", minzoom: 11,
      layout: {...labels, "text-field": ["coalesce", ["get", "name"], ""], "text-size": 12}, paint},
  );
  return style;
}

export function satelliteWithTrails(satellite, hiking) {
  const style = structuredClone(satellite);
  style.sources.offline = structuredClone(hiking.sources.offline);
  style.glyphs = hiking.glyphs;
  style.layers.push(...structuredClone(hiking.layers.filter(layer => ["hiking-paths", "hiking-path-names"].includes(layer.id))));
  return style;
}

export function recenterOnFix(map, position) {
  const lat = position?.coords?.latitude, lon = position?.coords?.longitude;
  if (!map || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return false;
  map.stop?.();
  // No fitBounds, zoom, bearing or pitch: this action only changes the center.
  map.jumpTo({center: [lon, lat]});
  return true;
}
