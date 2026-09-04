import assert from "node:assert/strict";
import test from "node:test";

import {
  calculatedRouteUrl,
  formatRouteDuration,
  parseCalculatedRoute,
  routeInstruction,
  routeNeedsRefresh,
} from "../src/retium/static/route-navigation.js";

test("builds an in-app OSRM request using GeoJSON road geometry", () => {
  assert.equal(
    calculatedRouteUrl([4.29, 51.49], [4.31, 51.5]),
    "https://router.project-osrm.org/route/v1/driving/4.29,51.49;4.31,51.5?alternatives=false&steps=true&geometries=geojson&overview=full",
  );
  assert.equal(calculatedRouteUrl([200, 51], [4, 52]), null);
});

test("validates and extracts route geometry and metrics", () => {
  assert.deepEqual(parseCalculatedRoute({
    code: "Ok",
    routes: [{
      distance: 1234,
      duration: 321,
      geometry: {type: "LineString", coordinates: [[4.29, 51.49], [4.3, 51.5]]},
      legs: [{steps: [{distance: 20, name: "Main Road", maneuver: {type: "depart"}}]}],
    }],
  }), {
    coordinates: [[4.29, 51.49], [4.3, 51.5]],
    distance: 1234,
    duration: 321,
    steps: [{distance: 20, name: "Main Road", maneuver: {type: "depart"}}],
  });
  assert.throws(() => parseCalculatedRoute({code: "NoRoute", routes: []}), /No road route/);
});

test("formats route duration and the next useful instruction", () => {
  assert.equal(formatRouteDuration(74), "1 MIN");
  assert.equal(formatRouteDuration(3900), "1 H 5 M");
  assert.equal(routeInstruction([{distance: 20, name: "Wouwsestraat", maneuver: {type: "depart"}}]), "START · Wouwsestraat");
  assert.equal(routeInstruction([{distance: 20, name: "Dorpsweg", maneuver: {type: "turn", modifier: "slight_right"}}]), "SLIGHT RIGHT · Dorpsweg");
});

test("recalculates only after meaningful movement and a quiet interval", () => {
  const distanceBetween = (left, right) => Math.abs(right[0] - left[0]) * 1000;
  assert.equal(routeNeedsRefresh(null, [4, 52], 15000, 0, distanceBetween), true);
  assert.equal(routeNeedsRefresh([4, 52], [4.05, 52], 20000, 10000, distanceBetween), false);
  assert.equal(routeNeedsRefresh([4, 52], [4.05, 52], 30000, 10000, distanceBetween), false);
  assert.equal(routeNeedsRefresh([4, 52], [4.09, 52], 30000, 10000, distanceBetween), true);
});
