// Public global environment rasters. These are optional map context only:
// they never enter the signed team feed or offline mission data.
export const ENVIRONMENT_MIN_ZOOM = Object.freeze({bathymetry: 1, nightlights: 1});
export const ENVIRONMENT_MAX_ZOOM = Object.freeze({bathymetry: 9, nightlights: 8});
export const ENVIRONMENT_BOUNDS = Object.freeze({
  bathymetry: Object.freeze([-180, -85.051129, 180, 85.051129]),
  nightlights: Object.freeze([-180, -85.051129, 180, 85.051129]),
});
export const ENVIRONMENT_RASTER_IDS = Object.freeze(["bathymetry", "nightlights"]);

const GEBCO_WMS_ROOT = "https://wms.gebco.net/mapserv";
const NIGHTLIGHTS_WMTS_ROOT = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";

export const ENVIRONMENT_RASTERS = Object.freeze({
  bathymetry: Object.freeze({
    title: "Global bathymetry",
    source: "GEBCO 2026",
    source_url: "https://www.gebco.net/data-products/gebco-web-services/web-map-service",
    attribution: "GEBCO",
    attribution_url: "https://www.gebco.net/",
    opacity: 0.58,
  }),
  nightlights: Object.freeze({
    title: "Night lights",
    source: "NASA VIIRS Black Marble · 2016",
    source_url: "https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/science-domain/nighttime-lights",
    attribution: "NASA Black Marble / VIIRS",
    attribution_url: "https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/science-domain/nighttime-lights",
    opacity: 0.78,
  }),
});

function wmsTileUrl(layer) {
  const query = new URLSearchParams({
    service: "WMS", request: "GetMap", version: "1.3.0", layers: layer,
    styles: "", format: "image/png", transparent: "true", width: "256", height: "256",
    crs: "EPSG:3857", bbox: "{bbox-epsg-3857}",
  });
  return `${GEBCO_WMS_ROOT}?${query}`.replaceAll("%7B", "{").replaceAll("%7D", "}");
}

export function environmentTileUrl(id) {
  if (id === "bathymetry") return wmsTileUrl("GEBCO_LATEST");
  if (id === "nightlights") return `${NIGHTLIGHTS_WMTS_ROOT}/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png`;
  throw new RangeError("Unknown environment raster");
}

export function environmentSource(source, id) {
  return {
    type: "raster", tiles: [environmentTileUrl(id)], tileSize: 256,
    minzoom: ENVIRONMENT_MIN_ZOOM[id], maxzoom: ENVIRONMENT_MAX_ZOOM[id],
    bounds: ENVIRONMENT_BOUNDS[id],
  };
}

export function environmentLayers(source, id) {
  return [{
    id: `${source}-raster`, type: "raster", source,
    minzoom: ENVIRONMENT_MIN_ZOOM[id],
    ...(id === "nightlights" ? {maxzoom: ENVIRONMENT_MAX_ZOOM[id]} : {}),
    paint: {"raster-opacity": ENVIRONMENT_RASTERS[id].opacity, "raster-fade-duration": 0},
  }];
}
