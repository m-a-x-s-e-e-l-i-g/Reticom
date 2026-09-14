// Public global satellite land-surface temperature rasters from NASA GIBS.
// GIBS supplies standards-based Web Mercator tiles and keeps the upstream
// date selection out of Reticom's signed team data.
export const THERMAL_MIN_ZOOM = 1;
export const THERMAL_MAX_ZOOM = 7;
export const THERMAL_BOUNDS = Object.freeze([-180, -85.051129, 180, 85.051129]);
export const THERMAL_WMTS_ROOT = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";

export const THERMAL_MODES = Object.freeze({
  terra_day: "MODIS Terra · daytime",
  terra_night: "MODIS Terra · nighttime",
  aqua_day: "MODIS Aqua · daytime",
  aqua_night: "MODIS Aqua · nighttime",
  viirs_day: "VIIRS NOAA-20 · daytime",
  viirs_night: "VIIRS NOAA-20 · nighttime",
});

const THERMAL_LAYERS = Object.freeze({
  terra_day: "MODIS_Terra_Land_Surface_Temp_Day",
  terra_night: "MODIS_Terra_Land_Surface_Temp_Night",
  aqua_day: "MODIS_Aqua_Land_Surface_Temp_Day",
  aqua_night: "MODIS_Aqua_Land_Surface_Temp_Night",
  viirs_day: "VIIRS_NOAA20_Land_Surface_Temp_Day",
  viirs_night: "VIIRS_NOAA20_Land_Surface_Temp_Night",
});

export function normalizeThermalMode(value) {
  return Object.hasOwn(THERMAL_LAYERS, value) ? value : "terra_day";
}

export function thermalModeLabel(value) {
  return THERMAL_MODES[normalizeThermalMode(value)];
}

export function thermalLayerId(value = "terra_day") {
  return THERMAL_LAYERS[normalizeThermalMode(value)];
}

export function thermalDate(now = Date.now()) {
  // GIBS products can lag the current UTC day. Yesterday is reliably within
  // the rolling availability window while still keeping the view current.
  return new Date(Number(now) - 86400000).toISOString().slice(0, 10);
}

export function thermalTileUrl(mode = "terra_day") {
  // GoogleMapsCompatible_Level7 uses normal XYZ row/column ordering for
  // MapLibre. The explicit date avoids a provider-specific default-date 404;
  // it rolls forward automatically each day.
  return `${THERMAL_WMTS_ROOT}/${thermalLayerId(mode)}/default/${thermalDate()}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png`;
}

export function thermalSource(source, mode = "terra_day") {
  return {
    type: "raster", tiles: [thermalTileUrl(mode)], tileSize: 256,
    minzoom: THERMAL_MIN_ZOOM, maxzoom: THERMAL_MAX_ZOOM, bounds: THERMAL_BOUNDS,
  };
}

export function thermalLayers(source) {
  return [{
    id: `${source}-raster`, type: "raster", source, minzoom: THERMAL_MIN_ZOOM,
    paint: {"raster-opacity": 0.68, "raster-fade-duration": 0},
  }];
}
