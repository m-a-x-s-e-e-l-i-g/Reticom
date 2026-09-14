import test from "node:test";
import assert from "node:assert/strict";
import {THERMAL_BOUNDS, THERMAL_COMBINED_PASSES, THERMAL_MAX_ZOOM, THERMAL_MIN_ZOOM, THERMAL_MODES, normalizeThermalMode, thermalFallbackModes, thermalLayerId, thermalLayers, thermalModeLabel, thermalSource, thermalTileUrl} from "../src/retium/static/thermal-layer.js";

test("public thermal modes expose NASA GIBS land-surface temperature layers", () => {
  assert.equal(normalizeThermalMode("combined"), "combined");
  assert.deepEqual(thermalFallbackModes("combined"), [...THERMAL_COMBINED_PASSES]);
  assert.equal(thermalModeLabel("combined"), THERMAL_MODES.combined);
  assert.equal(normalizeThermalMode("terra_day"), "terra_day");
  assert.equal(normalizeThermalMode("invalid"), "combined");
  assert.equal(thermalModeLabel("aqua_night"), THERMAL_MODES.aqua_night);
  assert.equal(thermalLayerId("viirs_day"), "VIIRS_NOAA20_Land_Surface_Temp_Day");
  assert.equal(thermalLayerId("combined"), "VIIRS_NOAA20_Land_Surface_Temp_Day");
  assert.match(thermalTileUrl("combined"), /VIIRS_NOAA20_Land_Surface_Temp_Day/);
  assert.match(thermalTileUrl("terra_day"), /gibs\.earthdata\.nasa\.gov\/wmts\/epsg3857\/best\/MODIS_Terra_Land_Surface_Temp_Day/);
  assert.match(thermalTileUrl("terra_day"), /GoogleMapsCompatible_Level7\/\{z\}\/\{y\}\/\{x\}\.png$/);
});

test("thermal raster stays global, bounded and capped at the GIBS native tile matrix", () => {
  const source = thermalSource("public-intel-thermal", "terra_night");
  assert.equal(source.type, "raster");
  assert.deepEqual(source.bounds, THERMAL_BOUNDS);
  assert.equal(source.minzoom, THERMAL_MIN_ZOOM);
  assert.equal(source.maxzoom, THERMAL_MAX_ZOOM);
  assert.match(source.tiles[0], /MODIS_Terra_Land_Surface_Temp_Night/);
  assert.deepEqual(thermalLayers("public-intel-thermal"), [{
    id: "public-intel-thermal-raster", type: "raster", source: "public-intel-thermal", minzoom: THERMAL_MIN_ZOOM,
    paint: {"raster-opacity": 0.68, "raster-fade-duration": 0},
  }]);
});

test("combined thermal mode remains a single selected source", () => {
  const source = thermalSource("public-intel-thermal", thermalFallbackModes("combined")[0]);
  assert.match(source.tiles[0], /VIIRS_NOAA20_Land_Surface_Temp_Day/);
  assert.equal(thermalLayers("public-intel-thermal").length, 1);
});
