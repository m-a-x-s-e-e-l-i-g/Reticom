// Generate contours locally from the existing cached Terrarium DEM, in a worker.
// Intervals are metres; tighter intervals do not imply greater survey accuracy.
export const CONTOUR_THRESHOLDS = Object.freeze({
  9: [200, 1000], 11: [100, 500], 12: [50, 250],
  13: [20, 100], 14: [10, 50], 15: [5, 25],
});
export const CONTOUR_MIN_ZOOM = 9;
export const CONTOUR_MAX_ZOOM = 15;

export function contourInterval(zoom) {
  const level = Object.keys(CONTOUR_THRESHOLDS).map(Number).reverse().find(level => zoom >= level);
  return level === undefined ? null : CONTOUR_THRESHOLDS[level][0];
}

let prepared;
export function prepareContourTiles() {
  if (!prepared) prepared = Promise.all([
    import("./vendor/maplibre-contour/index.mjs"),
    import("./vendor/maplibre-gl/maplibre-gl.mjs?v=6.6.0"),
  ]).then(([{default: contours}, maplibre]) => {
    const dem = new contours.DemSource({
      url: new URL("/api/intel-packs/elevation/tiles/{z}/{x}/{y}.png", location.href).href.replaceAll("%7B", "{").replaceAll("%7D", "}"),
      id: "reticom-contours", encoding: "terrarium", maxzoom: 14,
      worker: true, cacheSize: 64, timeoutMs: 30000,
    });
    dem.setupMaplibre(maplibre);
    return dem.contourProtocolUrl({
      thresholds: CONTOUR_THRESHOLDS, multiplier: 1, overzoom: 1,
      contourLayer: "contours", elevationKey: "ele", levelKey: "level",
    });
  }).catch(error => { prepared = undefined; throw error; });
  return prepared;
}

export function contourLayers(source) {
  const layers = [
    {id: `${source}-lines`, type: "line", source, "source-layer": "contours", minzoom: CONTOUR_MIN_ZOOM,
      paint: {"line-color": "#000000", "line-opacity": 1,
        "line-width": ["case", [">", ["get", "level"], 0], 1.25, .65]}},
    {id: `${source}-labels`, type: "symbol", source, "source-layer": "contours", minzoom: CONTOUR_MIN_ZOOM,
      filter: [">", ["get", "level"], 0],
      layout: {"symbol-placement": "line", "symbol-spacing": 220,
        "text-field": ["concat", ["to-string", ["get", "ele"]], " m"],
        "text-font": ["Noto Sans Regular"], "text-size": 11, "text-padding": 3,
        "text-max-angle": 35, "text-allow-overlap": false},
      paint: {"text-color": "#cbc7aa", "text-halo-color": "#10150f", "text-halo-width": 1.5}},
  ];
  // At close range, flat country may contain only 5/10 m contours and no major
  // contour at all. Label those too, while retaining collision avoidance.
  layers.push({...layers[1], id: `${source}-minor-labels`, minzoom: 14,
    filter: ["==", ["get", "level"], 0],
    layout: {...layers[1].layout, "symbol-spacing": 320, "text-size": 10, "text-padding": 8}});
  return layers;
}
