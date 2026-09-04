import assert from "node:assert/strict";
import test from "node:test";

import {
  captureMapCamera,
  hasAddedMapMarker,
  mapMarkerIds,
  restoreMapCamera,
  shouldInitiallyFrameMap,
} from "../src/retium/static/map-view.js";

test("extracts only valid map marker ids", () => {
  assert.deepEqual(
    [...mapMarkerIds([{id: "one", type: "marker.created"}, {id: "position", type: "position.updated"}, {type: "marker.created"}])],
    ["one"],
  );
});

test("detects a marker added after the map has rendered", () => {
  assert.equal(hasAddedMapMarker(new Set(["one"]), new Set(["one", "two"]), true), true);
  assert.equal(hasAddedMapMarker(new Set(["one"]), new Set(["one"]), true), false);
});

test("allows the initial map render to frame existing markers", () => {
  assert.equal(hasAddedMapMarker(new Set(), new Set(["one"]), false), false);
});

test("frames operational data once and preserves the user's view afterwards", () => {
  assert.equal(shouldInitiallyFrameMap(false, 4), true);
  assert.equal(shouldInitiallyFrameMap(false, 0), false);
  assert.equal(shouldInitiallyFrameMap(true, 5), false);
  assert.equal(shouldInitiallyFrameMap(true, 6), false);
});

test("captures every camera value needed across a basemap style change", () => {
  const map = {
    getCenter: () => ({lng: 4.29091, lat: 51.49304}),
    getZoom: () => 14.375,
    getBearing: () => 27,
    getPitch: () => 18,
  };

  assert.deepEqual(captureMapCamera(map), {
    center: [4.29091, 51.49304],
    zoom: 14.375,
    bearing: 27,
    pitch: 18,
  });
});

test("restores the exact camera without fitting the map again", () => {
  let restored = null;
  const camera = {center: [4.29, 51.49], zoom: 15.25, bearing: 0, pitch: 0};
  restoreMapCamera({jumpTo: (options) => { restored = options; }}, camera);
  assert.deepEqual(restored, camera);
});
