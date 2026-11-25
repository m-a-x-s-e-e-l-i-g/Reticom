// High-resolution public elevation rasters. Providers are deliberately
// bounded: these are national/regional LiDAR products, not a global DEM.
export const AHN_MIN_ZOOM = 12;
export const AHN_MAX_ZOOM = 21;
export const AHN_BOUNDS = Object.freeze([3.0, 50.65, 7.5, 53.7]);
export const AHN_WMS_URL = "https://service.pdok.nl/rws/ahn/wms/v1_0";
export const AHN_MODES = Object.freeze({dtm: "Bare-earth terrain (DTM)", dsm: "Surface / buildings (DSM)"});

const AHN_PROVIDER = Object.freeze({
  id: "ahn", label: "AHN · Netherlands", bounds: AHN_BOUNDS, minzoom: AHN_MIN_ZOOM,
  maxzoom: AHN_MAX_ZOOM, resolution: "0.5 m", source: "PDOK / AHN",
  source_url: "https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn",
  attribution: "PDOK / AHN", attribution_url: "https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn",
  modes: Object.freeze({dtm: {layer: "dtm_05m", label: AHN_MODES.dtm, wms: AHN_WMS_URL}, dsm: {layer: "dsm_05m", label: AHN_MODES.dsm, wms: AHN_WMS_URL}}),
});

const ENGLAND_PROVIDER = Object.freeze({
  id: "england", label: "Environment Agency · England", bounds: Object.freeze([-7.9, 49.7, 2.8, 56.0]), minzoom: 12,
  maxzoom: 21, resolution: "1 m", source: "Environment Agency / Defra",
  source_url: "https://environment.data.gov.uk/dataset/13787b9a-26a4-4775-8523-806d13af58fc",
  attribution: "Environment Agency / Defra", attribution_url: "https://environment.data.gov.uk/dataset/13787b9a-26a4-4775-8523-806d13af58fc",
  modes: Object.freeze({
    dtm: {layer: "Lidar_Composite_DTM_1m", label: AHN_MODES.dtm, wms: "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wms"},
    dsm: {layer: "Lidar_Composite_LZ_DSM_1m", label: AHN_MODES.dsm, wms: "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-last-return-dsm-1m/wms"},
  }),
});

const USGS_PROVIDER = Object.freeze({
  id: "usgs", label: "USGS 3DEP · United States", bounds: Object.freeze([-171, 18, -64, 72]), minzoom: 10,
  maxzoom: 21, resolution: "1 m where available", source: "USGS 3DEP",
  source_url: "https://www.usgs.gov/3d-elevation-program/about-3dep-products-services",
  attribution: "USGS 3DEP", attribution_url: "https://www.usgs.gov/3d-elevation-program/about-3dep-products-services",
  // The public 3DEP dynamic service is bare-earth only; an explicit DTM
  // fallback keeps the mode selector honest when the map moves to the US.
  modes: Object.freeze({dtm: {layer: "3DEPElevation", label: AHN_MODES.dtm, wms: "https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WMSServer"}}),
});

const MALTA_PROVIDER = Object.freeze({
  id: "malta", label: "Planning Authority · Malta", bounds: Object.freeze([13.92, 35.59, 14.83, 36.30]),
  minzoom: 12, maxzoom: 18, resolution: "1 m · 2018", source: "Malta Planning Authority",
  source_url: "https://portal.data.gov.mt/dataset/digital-terrain-model-2018",
  attribution: "Malta Planning Authority · CC BY 4.0",
  attribution_url: "https://portal.data.gov.mt/dataset/digital-terrain-model-2018",
  modes: Object.freeze({
    dtm: {label: AHN_MODES.dtm, tiles: "/api/intel-packs/lidar/malta/dtm/{z}/{x}/{y}.png"},
    dsm: {label: AHN_MODES.dsm, tiles: "/api/intel-packs/lidar/malta/dsm/{z}/{x}/{y}.png"},
  }),
});

const IGN_WMS_URL = "https://data.geopf.fr/wms-r/wms";
const FRANCE_PROVIDER = Object.freeze({
  id: "france", label: "IGN LiDAR HD · France", bounds: Object.freeze([-5.2, 41.2, 9.7, 51.2]),
  minzoom: 12, maxzoom: 19, resolution: "0.5 m source grid", source: "IGN / Géoplateforme",
  source_url: "https://www.data.gouv.fr/datasets/mnt-lidar-hd",
  attribution: "IGN · LiDAR HD", attribution_url: "https://geoservices.ign.fr/lidarhd",
  modes: Object.freeze({
    dtm: {label: "Bare-earth hillshade (DTM)", layer: "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.SHADOW", wms: IGN_WMS_URL},
    dsm: {label: "Surface hillshade (DSM)", layer: "IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.SHADOW", wms: IGN_WMS_URL},
  }),
});

export const LIDAR_PROVIDERS = Object.freeze({ahn: AHN_PROVIDER, england: ENGLAND_PROVIDER, france: FRANCE_PROVIDER, malta: MALTA_PROVIDER, usgs: USGS_PROVIDER});
export const LIDAR_PROVIDER_ORDER = Object.freeze(Object.keys(LIDAR_PROVIDERS));

export function normalizeAhnMode(value) {
  return Object.hasOwn(AHN_MODES, value) ? value : "dtm";
}

export function ahnModeLabel(value) {
  return AHN_MODES[normalizeAhnMode(value)];
}

function providerMode(provider, value) {
  const modes = provider?.modes || AHN_PROVIDER.modes;
  const requested = normalizeAhnMode(value);
  return Object.hasOwn(modes, requested) ? requested : "dtm";
}

export function lidarModeForProvider(provider, value = "dtm") {
  return providerMode(provider, value);
}

export function lidarModeLabel(provider, value = "dtm") {
  const mode = providerMode(provider, value);
  return provider?.modes?.[mode]?.label || ahnModeLabel(mode);
}

function coordinate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function boundsValues(bounds) {
  const west = coordinate(bounds?.getWest?.() ?? bounds?.west);
  const east = coordinate(bounds?.getEast?.() ?? bounds?.east);
  const south = coordinate(bounds?.getSouth?.() ?? bounds?.south);
  const north = coordinate(bounds?.getNorth?.() ?? bounds?.north);
  return [west, south, east, north].every(value => value !== null) ? {west, south, east, north} : null;
}

function longitudeInBounds(longitude, west, east) {
  if (west <= east) return longitude >= west && longitude <= east;
  return longitude >= west || longitude <= east;
}

export function lidarProviderForBounds(bounds) {
  const values = boundsValues(bounds);
  if (!values || values.south > values.north) return null;
  let {west, east} = values;
  if (east - west > 180) { const wrapped = east; east = west; west = wrapped; }
  const longitude = west <= east ? (west + east) / 2 : ((west + ((east + 360 - west) % 360) / 2 + 540) % 360) - 180;
  const latitude = (values.south + values.north) / 2;
  return LIDAR_PROVIDER_ORDER.map(id => LIDAR_PROVIDERS[id]).find(provider => {
    const [providerWest, providerSouth, providerEast, providerNorth] = provider.bounds;
    return latitude >= providerSouth && latitude <= providerNorth && longitudeInBounds(longitude, providerWest, providerEast);
  }) || null;
}

export function lidarTileUrl(mode = "dtm", provider = AHN_PROVIDER) {
  const actualMode = providerMode(provider, mode);
  const selected = provider.modes[actualMode];
  if (selected.tiles) return selected.tiles;
  const query = new URLSearchParams({
    service: "WMS", request: "GetMap", version: "1.3.0", layers: selected.layer,
    styles: "", format: "image/png", transparent: "true", width: "256", height: "256",
    crs: "EPSG:3857", bbox: "{bbox-epsg-3857}",
  });
  return `${selected.wms}?${query}`.replaceAll("%7B", "{").replaceAll("%7D", "}");
}

export function ahnTileUrl(mode = "dtm") {
  return lidarTileUrl(mode, AHN_PROVIDER);
}

export function lidarSource(source, provider = AHN_PROVIDER, mode = "dtm") {
  return {
    type: "raster", tiles: [lidarTileUrl(mode, provider)], tileSize: 256,
    minzoom: provider.minzoom, maxzoom: provider.maxzoom, bounds: provider.bounds,
    attribution: provider.attribution,
  };
}

export function ahnSource(source, mode = "dtm") {
  return lidarSource(source, AHN_PROVIDER, mode);
}

export function ahnLayers(source) {
  return lidarLayers(source, AHN_PROVIDER);
}

export function lidarLayers(source, provider = AHN_PROVIDER) {
  return [{
    id: `${source}-raster`, type: "raster", source, minzoom: provider.minzoom,
    paint: {"raster-opacity": 0.62, "raster-fade-duration": 0},
  }];
}
