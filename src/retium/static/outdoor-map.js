// A hiking style on our existing, cacheable OpenMapTiles data. No new tile host.
export const ROAD_COLOR = "#45483f";
export const PATH_FILTER = ["match", ["get", "class"], ["path", "track"], true, false];
export const DAYLIGHT_HIKING_NAME = "Reticom Daylight Hiking";
export const HIKING_RELIEF = "hiking-relief";
export const hikingReliefTone = style => style?.name === DAYLIGHT_HIKING_NAME ? "daylight" : style?.name === "Reticom Hiking" ? "night" : null;
export const MAP_STYLES = [
  {id: "hiking", label: "Hiking", short: "HIKE"},
  {id: "satellite", label: "Satellite", short: "SAT", imagery: true},
  {id: "satellite-hybrid", label: "Satellite hybrid", short: "HYBRID", imagery: true},
  {id: "hiking-light", label: "Daylight hiking", short: "DAY"},
];
// Migrate the former plain-dark default; satellite remains an explicit choice.
const availableMapStyles = online => MAP_STYLES.filter(style => online || !style.imagery);
export const preferredMapStyle = (stored, online = true) => availableMapStyles(online).find(style => style.id === stored)?.id ?? "hiking";
export function nextMapStyle(current, requested, online = true) {
  if (typeof requested === "string") return preferredMapStyle(requested, online);
  const styles = availableMapStyles(online);
  return styles[(styles.findIndex(style => style.id === current) + 1) % styles.length].id;
}

export function updateMapStyleControl(button, credit, mode, {compact = false, online = true} = {}) {
  const next = MAP_STYLES.find(style => style.id === nextMapStyle(mode, undefined, online));
  if (button) {
    button.textContent = compact ? next.short : next.label;
    button.setAttribute("aria-label", `Switch to ${next.label.toLowerCase()} map`);
  }
  if (credit) {
    const imagery = MAP_STYLES.find(style => style.id === mode)?.imagery;
    const terrain = imagery ? '<a href="https://www.esri.com/" target="_blank" rel="noreferrer">Imagery © Esri</a>'
      : '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noreferrer">Mapzen terrain</a>';
    credit.innerHTML = `${terrain} · <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">${compact ? "OFM" : "OpenFreeMap"}</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OSM</a>`;
  }
}
export function roadAndPathLayers() {
  const transport = {type: "line", source: "offline", "source-layer": "transportation"};
  const roads = ["!", structuredClone(PATH_FILTER)];
  return [
    {...transport, id: "offline-road-casing", filter: roads, paint: {"line-color": "#080a08", "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.7, 10, 2.2, 14, 5]}},
    {...transport, id: "offline-roads", filter: structuredClone(roads), paint: {"line-color": ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 10, 1, 14, 2.6]}},
    {...transport, id: "offline-paths", filter: structuredClone(PATH_FILTER), paint: {"line-color": ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 10, 1, 14, 2.6], "line-dasharray": [3, 2]}},
  ];
}

const DAYLIGHT_PAPER = "#f5f1e5";
const DAYLIGHT_TRAIL = "#3f624c";
const DAYLIGHT_PAINT = {
  "offline-background": {"background-color": DAYLIGHT_PAPER},
  "offline-landcover": {"fill-color": ["match", ["get", "class"], "wood", "#c6d6b4", "grass", "#dfe6c8",
    "farmland", "#ebe7ce", "sand", "#ede0c7", ["ice", "snow"], "#ecefea", "#e5e7d4"]},
  "offline-landuse": {"fill-color": ["match", ["get", "class"], ["residential", "commercial", "industrial"], "#e6ded2",
    ["farmland", "farmyard"], "#eae5cb", "#e8e6d5"], "fill-opacity": .54},
  "offline-park": {"fill-color": "#bdd1ae", "fill-opacity": .42},
  "offline-water": {"fill-color": "#b3d3db"},
  "offline-waterway": {"line-color": "#588da4"},
  "offline-buildings": {"fill-color": "#ada79a", "fill-outline-color": "#827b70"},
  "offline-road-casing": {"line-color": ["match", ["get", "class"], ["motorway", "trunk"], "#9d6553", "#8a8173"]},
  "offline-roads": {"line-color": ["match", ["get", "class"], ["motorway", "trunk"], "#d39b7e", ["primary", "secondary"], "#f3dfb9", "#faf6ec"], "line-opacity": 1},
  "offline-boundaries": {"line-color": "#706452"},
  "offline-road-labels": {"text-color": "#51463b", "text-halo-color": DAYLIGHT_PAPER},
  "offline-place-labels": {"text-color": "#2b352f", "text-halo-color": DAYLIGHT_PAPER},
};

export function hikingMapStyle(base, {daylight = false, terrainTiles} = {}) {
  const style = structuredClone(base);
  style.name = daylight ? DAYLIGHT_HIKING_NAME : "Reticom Hiking";
  // The hiking path layer replaces the standard one; never draw a solid
  // transportation stroke underneath its dash gaps.
  style.layers = style.layers.filter(layer => layer.id !== "offline-paths");
  if (terrainTiles) {
    style.sources[HIKING_RELIEF] = {type: "raster-dem", tiles: [terrainTiles], encoding: "terrarium", tileSize: 256, minzoom: 7, maxzoom: 14};
    const waterIndex = style.layers.findIndex(layer => layer.id === "offline-water");
    style.layers.splice(waterIndex < 0 ? style.layers.length : waterIndex, 0, {
      id: HIKING_RELIEF, type: "hillshade", source: HIKING_RELIEF, minzoom: 7,
      paint: {
        "hillshade-illumination-direction": 315, "hillshade-illumination-anchor": "map",
        "hillshade-exaggeration": daylight ? .36 : .21,
        "hillshade-shadow-color": daylight ? "#61503b" : "#080d0a",
        "hillshade-highlight-color": daylight ? "#faf4e3" : "#697760",
        "hillshade-accent-color": daylight ? "#78654c" : "#172018",
      },
    });
  }
  const labelIndex = style.layers.findIndex(layer => layer.type === "symbol");
  const before = labelIndex < 0 ? style.layers.length : labelIndex;
  const paths = structuredClone(PATH_FILTER);
  const labels = {"text-font": ["Noto Sans Regular"], "text-size": 11};
  const paint = {"text-color": daylight ? "#304c38" : "#b5b99b", "text-halo-color": daylight ? DAYLIGHT_PAPER : "#10160f", "text-halo-width": 1.5};
  if (daylight) for (const layer of style.layers) if (DAYLIGHT_PAINT[layer.id]) Object.assign(layer.paint, structuredClone(DAYLIGHT_PAINT[layer.id]));
  style.layers.splice(before, 0,
    {id: "hiking-paths", type: "line", source: "offline", "source-layer": "transportation",
      filter: paths, paint: {"line-color": daylight ? DAYLIGHT_TRAIL : ROAD_COLOR, "line-opacity": 0.82, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 16, 2.5], "line-dasharray": [3, 2]}},
    {id: "hiking-path-names", type: "symbol", source: "offline", "source-layer": "transportation_name", minzoom: 13,
      filter: paths, layout: {...labels, "symbol-placement": "line", "text-field": ["coalesce", ["get", "name"], ["get", "ref"], ""]}, paint},
    {id: "hiking-peaks", type: "symbol", source: "offline", "source-layer": "mountain_peak", minzoom: 10,
      layout: {...labels, "text-field": ["concat", "△ ", ["coalesce", ["get", "name"], ""], " ", ["to-string", ["get", "ele"]], " m"]}, paint},
    {id: "hiking-parks", type: "symbol", source: "offline", "source-layer": "park", minzoom: 11,
      layout: {...labels, "text-field": ["coalesce", ["get", "name"], ""], "text-size": 12}, paint},
  );
  return style;
}

export function satelliteWithTrails(satellite, hiking, {hybrid = false} = {}) {
  const style = structuredClone(satellite);
  style.sources.offline = structuredClone(hiking.sources.offline);
  style.glyphs = hiking.glyphs;
  const overlays = new Set(["hiking-paths", "hiking-path-names"]);
  if (hybrid) {
    style.name = "Reticom Satellite Hybrid";
    for (const id of ["offline-road-casing", "offline-roads", "offline-place-labels", "offline-road-labels", "hiking-peaks", "hiking-parks"]) overlays.add(id);
  }
  for (const layer of structuredClone(hiking.layers.filter(layer => overlays.has(layer.id)))) {
    if (hybrid && layer.type === "symbol") Object.assign(layer.paint, {"text-color": "#f2ede0", "text-halo-color": "#202b25", "text-halo-width": 2});
    if (hybrid && layer.id === "offline-roads") layer.paint["line-color"] = ["match", ["get", "class"], ["motorway", "trunk"], "#f2cc8d", "#e0d9c5"];
    if (hybrid && layer.id === "hiking-paths") layer.paint["line-color"] = "#d5eba9";
    style.layers.push(layer);
  }
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
