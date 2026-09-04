import assert from "node:assert/strict";
import test from "node:test";

import {formatMapBytes, packProgress, tileCountForBounds} from "../src/retium/static/map-offline.js";

test("counts the world tile at zoom zero", () => {
  assert.equal(tileCountForBounds({west: -1, south: -1, east: 1, north: 1}, 0), 1);
});

test("higher detail includes the full lower-zoom pyramid", () => {
  const low = tileCountForBounds({west: 4.7, south: 51.5, east: 4.8, north: 51.6}, 10);
  const high = tileCountForBounds({west: 4.7, south: 51.5, east: 4.8, north: 51.6}, 14);
  assert.ok(high > low);
});

test("formats storage and progress for the map panel", () => {
  assert.equal(formatMapBytes(1_572_864), "1.5 MB");
  assert.equal(packProgress({downloaded_tiles: 25, tile_count: 100}), 25);
});
