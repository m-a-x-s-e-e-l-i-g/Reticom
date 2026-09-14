import test from "node:test";
import assert from "node:assert/strict";
import {AHN_BOUNDS, AHN_MAX_ZOOM, AHN_MIN_ZOOM, AHN_MODES, LIDAR_PROVIDERS, ahnLayers, ahnModeLabel, ahnSource, ahnTileUrl, lidarLayers, lidarModeForProvider, lidarModeLabel, lidarProviderForBounds, lidarSource, lidarTileUrl, normalizeAhnMode} from "../src/retium/static/ahn-layer.js";

test("AHN exposes the current PDOK DTM and DSM WMS layers", () => {
  assert.equal(normalizeAhnMode("dtm"), "dtm");
  assert.equal(normalizeAhnMode("dsm"), "dsm");
  assert.equal(normalizeAhnMode("point-cloud"), "dtm");
  assert.equal(ahnModeLabel("dsm"), AHN_MODES.dsm);
  assert.match(ahnTileUrl("dtm"), /service\.pdok\.nl\/rws\/ahn\/wms\/v1_0/);
  assert.match(ahnTileUrl("dtm"), /layers=dtm_05m/);
  assert.match(ahnTileUrl("dsm"), /layers=dsm_05m/);
  assert.match(ahnTileUrl("dtm"), /bbox=\{bbox-epsg-3857\}/);
});

test("AHN raster source is bounded and starts at close zoom", () => {
  const source = ahnSource("public-intel-ahn", "dsm");
  assert.equal(source.type, "raster");
  assert.deepEqual(source.bounds, AHN_BOUNDS);
  assert.equal(source.minzoom, AHN_MIN_ZOOM);
  assert.equal(source.maxzoom, AHN_MAX_ZOOM);
  assert.match(source.tiles[0], /layers=dsm_05m/);
  assert.deepEqual(ahnLayers("public-intel-ahn"), [{
    id: "public-intel-ahn-raster", type: "raster", source: "public-intel-ahn", minzoom: AHN_MIN_ZOOM,
    paint: {"raster-opacity": 0.62, "raster-fade-duration": 0},
  }]);
});

test("international providers are selected by the map area", () => {
  assert.equal(lidarProviderForBounds({west: 3.9, south: 50.9, east: 4.1, north: 51.1}).id, "ahn");
  assert.equal(lidarProviderForBounds({west: -1.5, south: 51.3, east: -1.2, north: 51.6}).id, "england");
  assert.equal(lidarProviderForBounds({west: -100.2, south: 35.0, east: -99.8, north: 35.3}).id, "usgs");
  assert.equal(lidarProviderForBounds({west: 10, south: 0, east: 11, north: 1}), null);
});

test("international WMS rasters use provider layers and honest mode fallbacks", () => {
  const england = LIDAR_PROVIDERS.england;
  const usgs = LIDAR_PROVIDERS.usgs;
  assert.match(lidarTileUrl("dsm", england), /environment\.data\.gov\.uk\/spatialdata\/.*Lidar_Composite_LZ_DSM_1m/);
  assert.match(lidarTileUrl("dtm", usgs), /elevation\.nationalmap\.gov\/arcgis\/services\/3DEPElevation/);
  assert.match(lidarTileUrl("dsm", usgs), /layers=3DEPElevation/);
  assert.equal(lidarModeForProvider(usgs, "dsm"), "dtm");
  assert.equal(lidarModeLabel(usgs, "dsm"), AHN_MODES.dtm);
  const source = lidarSource("public-intel-ahn", england, "dsm");
  assert.equal(source.minzoom, england.minzoom);
  assert.deepEqual(source.bounds, england.bounds);
  assert.deepEqual(lidarLayers("public-intel-ahn", england), [{
    id: "public-intel-ahn-raster", type: "raster", source: "public-intel-ahn", minzoom: england.minzoom,
    paint: {"raster-opacity": 0.62, "raster-fade-duration": 0},
  }]);
});
