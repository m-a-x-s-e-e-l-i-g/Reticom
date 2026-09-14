import test from "node:test";
import assert from "node:assert/strict";
import {ENVIRONMENT_BOUNDS, ENVIRONMENT_MAX_ZOOM, ENVIRONMENT_MIN_ZOOM, ENVIRONMENT_RASTER_IDS, environmentLayers, environmentSource, environmentTileUrl} from "../src/retium/static/environment-layers.js";

test("public environment rasters expose the three selected global sources", () => {
  assert.deepEqual([...ENVIRONMENT_RASTER_IDS], ["bathymetry", "worldcover", "nightlights"]);
  assert.match(environmentTileUrl("bathymetry"), /wms\.gebco\.net\/mapserv/);
  assert.match(environmentTileUrl("bathymetry"), /GEBCO_LATEST/);
  assert.match(environmentTileUrl("worldcover"), /wmts\.terrascope\.be/);
  assert.match(environmentTileUrl("worldcover"), /esa-worldcover-map-10m-2021-v2_map/);
  assert.match(environmentTileUrl("nightlights"), /VIIRS_Black_Marble/);
});

test("environment rasters stay bounded and respect provider zoom limits", () => {
  for (const id of ENVIRONMENT_RASTER_IDS) {
    const source = environmentSource(`public-intel-${id}`, id);
    assert.equal(source.type, "raster");
    assert.deepEqual(source.bounds, ENVIRONMENT_BOUNDS[id]);
    assert.equal(source.minzoom, ENVIRONMENT_MIN_ZOOM[id]);
    assert.equal(source.maxzoom, ENVIRONMENT_MAX_ZOOM[id]);
    assert.deepEqual(environmentLayers(`public-intel-${id}`, id).map(layer => layer.id), [`public-intel-${id}-raster`]);
  }
});
