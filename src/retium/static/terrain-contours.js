// Generate contours locally from the existing cached Terrarium DEM, in a worker.
// Intervals are metres; tighter intervals do not imply greater survey accuracy.
export const CONTOUR_THRESHOLDS = Object.freeze({
  9: [200, 1000], 11: [100, 500], 12: [50, 250],
  13: [20, 100], 14: [10, 50], 15: [5, 25],
});
export const CONTOUR_MIN_ZOOM = 9;
export const CONTOUR_MAX_ZOOM = 15;
export const MALTA_CONTOUR_MAX_ZOOM = 18;
const MALTA_THRESHOLDS = {...CONTOUR_THRESHOLDS, 16: [2, 10]};

export function contourInterval(zoom, malta = false) {
  const thresholds = malta ? MALTA_THRESHOLDS : CONTOUR_THRESHOLDS;
  const level = Object.keys(thresholds).map(Number).reverse().find(level => zoom >= level);
  return level === undefined ? null : thresholds[level][0];
}

const prepared = new Map();
export function prepareContourTiles(malta = false) {
  if (!prepared.has(malta)) prepared.set(malta, Promise.all([
    import("./vendor/maplibre-contour/index.mjs"),
    import("./vendor/maplibre-gl/maplibre-gl.mjs?v=6.6.0"),
  ]).then(([{default: contours}, maplibre]) => {
    const dem = new contours.DemSource({
      url: new URL(`/api/intel-packs/elevation/tiles/{z}/{x}/{y}.png${malta ? "?malta=true" : ""}`, location.href).href.replaceAll("%7B", "{").replaceAll("%7D", "}"),
      id: malta ? "reticom-malta-contours" : "reticom-contours", encoding: "terrarium", maxzoom: (malta ? MALTA_CONTOUR_MAX_ZOOM : CONTOUR_MAX_ZOOM) - 1,
      worker: true, cacheSize: 64, timeoutMs: 30000,
    });
    dem.setupMaplibre(maplibre);
    return dem.contourProtocolUrl({
      thresholds: malta ? MALTA_THRESHOLDS : CONTOUR_THRESHOLDS, multiplier: 1, overzoom: 1,
      contourLayer: "contours", elevationKey: "ele", levelKey: "level",
    });
  }).catch(error => { prepared.delete(malta); throw error; }));
  return prepared.get(malta);
}

export function contourLayers(source, {daylight = false} = {}) {
  const layers = [
    {id: `${source}-lines`, type: "line", source, "source-layer": "contours", minzoom: CONTOUR_MIN_ZOOM,
      paint: {"line-color": daylight ? "#8f6342" : "#000000", "line-opacity": daylight ? ["case", [">", ["get", "level"], 0], .74, .46] : 1,
        "line-width": ["case", [">", ["get", "level"], 0], 1.25, .65]}},
    {id: `${source}-labels`, type: "symbol", source, "source-layer": "contours", minzoom: CONTOUR_MIN_ZOOM,
      filter: [">", ["get", "level"], 0],
      layout: {"symbol-placement": "line", "symbol-spacing": 220,
        "text-field": ["concat", ["to-string", ["get", "ele"]], " m"],
        "text-font": ["Noto Sans Regular"], "text-size": 11, "text-padding": 3,
        "text-max-angle": 35, "text-allow-overlap": false},
      paint: {"text-color": daylight ? "#62452f" : "#cbc7aa", "text-halo-color": daylight ? "#f5f1e5" : "#10150f", "text-halo-width": 1.5}},
  ];
  // At close range, flat country may contain only 5/10 m contours and no major
  // contour at all. Label those too, while retaining collision avoidance.
  layers.push({...layers[1], id: `${source}-minor-labels`, minzoom: 14,
    filter: ["==", ["get", "level"], 0],
    layout: {...layers[1].layout, "symbol-spacing": 320, "text-size": 10, "text-padding": 8}});
  return layers;
}
