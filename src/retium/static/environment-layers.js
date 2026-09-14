// Public global environment rasters. These are optional map context only:
// they never enter the signed team feed or offline mission data.
export const ENVIRONMENT_MIN_ZOOM = Object.freeze({bathymetry: 1, worldcover: 6, nightlights: 1});
export const ENVIRONMENT_MAX_ZOOM = Object.freeze({bathymetry: 9, worldcover: 14, nightlights: 8});
export const ENVIRONMENT_BOUNDS = Object.freeze({
  bathymetry: Object.freeze([-180, -85.051129, 180, 85.051129]),
  worldcover: Object.freeze([-180, -60, 180, 83]),
  nightlights: Object.freeze([-180, -85.051129, 180, 85.051129]),
});
export const ENVIRONMENT_RASTER_IDS = Object.freeze(["bathymetry", "worldcover", "nightlights"]);

const GEBCO_WMS_ROOT = "https://wms.gebco.net/mapserv";
const WORLDCOVER_WMTS_ROOT = "https://wmts.terrascope.be/";
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
  worldcover: Object.freeze({
    title: "Land cover · ESA WorldCover",
    source: "ESA WorldCover 2021",
    source_url: "https://esa-worldcover.org/en/data-access",
    attribution: "ESA WorldCover · Contains modified Copernicus Sentinel data",
    attribution_url: "https://esa-worldcover.org/en/data-access",
    opacity: 0.62,
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

function raster(id) {
  return ENVIRONMENT_RASTERS[id] || ENVIRONMENT_RASTERS.bathymetry;
}

function wmsTileUrl(layer) {
  const query = new URLSearchParams({
    service: "WMS", request: "GetMap", version: "1.3.0", layers: layer,
    styles: "", format: "image/png", transparent: "true", width: "256", height: "256",
    crs: "EPSG:3857", bbox: "{bbox-epsg-3857}",
  });
  return `${GEBCO_WMS_ROOT}?${query}`.replaceAll("%7B", "{").replaceAll("%7D", "}");
}

function worldCoverTileUrl() {
  const query = new URLSearchParams({
    SERVICE: "WMTS", REQUEST: "GetTile", VERSION: "1.0.0",
    LAYER: "esa-worldcover-map-10m-2021-v2_map", STYLE: "default", FORMAT: "image/png",
    TILEMATRIXSET: "EPSG:3857", TILEMATRIX: "{z}", TILEROW: "{y}", TILECOL: "{x}",
    TIME: "2021-01-01", assets: "Map", colormap_name: "worldcover",
  });
  return `${WORLDCOVER_WMTS_ROOT}?${query}`.replaceAll("%7B", "{").replaceAll("%7D", "}");
}

export function environmentTileUrl(id) {
  if (id === "bathymetry") return wmsTileUrl("GEBCO_LATEST");
  if (id === "worldcover") return worldCoverTileUrl();
  return `${NIGHTLIGHTS_WMTS_ROOT}/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png`;
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
    minzoom: ENVIRONMENT_MIN_ZOOM[id], maxzoom: ENVIRONMENT_MAX_ZOOM[id],
    paint: {"raster-opacity": raster(id).opacity, "raster-fade-duration": 0},
  }];
}
