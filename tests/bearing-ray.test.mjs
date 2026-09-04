import assert from "node:assert/strict";
import test from "node:test";

import {bearingRayEndPixel, bearingRayFeature} from "../src/retium/static/bearing-ray.js";

test("projects compass headings along the correct screen direction", () => {
  const start = {x: 500, y: 500};
  const north = bearingRayEndPixel(start, 0, 1000, 1000);
  const east = bearingRayEndPixel(start, 90, 1000, 1000);
  const south = bearingRayEndPixel(start, 180, 1000, 1000);
  const west = bearingRayEndPixel(start, 270, 1000, 1000);

  assert.ok(north.y < start.y && Math.abs(north.x - start.x) < 0.001);
  assert.ok(east.x > start.x && Math.abs(east.y - start.y) < 0.001);
  assert.ok(south.y > start.y && Math.abs(south.x - start.x) < 0.001);
  assert.ok(west.x < start.x && Math.abs(west.y - start.y) < 0.001);
});

test("extends beyond the complete visible map", () => {
  const endpoint = bearingRayEndPixel({x: 400, y: 300}, 45, 800, 600);
  assert.ok(Math.hypot(endpoint.x - 400, endpoint.y - 300) > Math.hypot(800, 600) * 2);
});

test("builds a local-only bearing line feature", () => {
  const feature = bearingRayFeature([4.3, 51.5], [4.5, 51.7], 45);
  assert.deepEqual(feature.geometry.coordinates, [[4.3, 51.5], [4.5, 51.7]]);
  assert.equal(feature.properties.heading, 45);
  assert.equal(bearingRayFeature([4.3], [4.5, 51.7], 45), null);
});
