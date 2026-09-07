import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {hikingMapStyle, recenterOnFix, roadAndPathLayers, ROAD_COLOR, PATH_FILTER, preferredMapStyle, nextMapStyle, satelliteWithTrails} from "../src/retium/static/outdoor-map.js";
import {installIntelLayers} from "../src/retium/static/intel-packs.js";
import {calculatedRouteUrl} from "../src/retium/static/route-navigation.js";

test("hiking reuses offline tiles and adds path names and peaks below labels", () => {
  const base = {sources: {offline: {tiles: ["/api/map/tiles/{z}/{x}/{y}.pbf"]}}, layers: [{id: "land", type: "fill"}, {id: "names", type: "symbol"}]};
  const hiking = hikingMapStyle(base);
  assert.deepEqual(hiking.sources, base.sources);
  assert.equal(base.layers.length, 2);
  assert.equal(hiking.layers.at(-1).id, "names");
  assert.ok(hiking.layers.some(layer => layer.id === "hiking-paths"));
  assert.ok(hiking.layers.some(layer => layer["source-layer"] === "mountain_peak"));
});

test("hiking is the default and migrates plain dark while respecting online satellite", () => {
  for (const saved of [null, undefined, "", "dark", "hiking", "invalid"]) assert.equal(preferredMapStyle(saved), "hiking");
  assert.equal(preferredMapStyle("satellite"), "satellite");
  assert.equal(preferredMapStyle("satellite", false), "hiking");
  assert.equal(nextMapStyle("hiking", {type: "click"}), "satellite");
  assert.equal(nextMapStyle("satellite", {type: "click"}), "hiking");
  assert.equal(nextMapStyle("hiking", "satellite"), "satellite");
  assert.equal(nextMapStyle("satellite", "hiking"), "hiking");
});

test("satellite keeps dashed tile-based trails without adding solid roads or moving the camera", () => {
  const hiking = hikingMapStyle({glyphs: "cached-glyphs", sources: {offline: {type: "vector", tiles: ["cached-tiles"]}}, layers: roadAndPathLayers()});
  const base = {sources: {satellite: {type: "raster"}}, layers: [{id: "satellite-base", type: "raster"}]};
  const result = satelliteWithTrails(base, hiking);
  assert.equal(base.layers.length, 1);
  assert.deepEqual(result.sources.offline, hiking.sources.offline);
  assert.equal(result.glyphs, "cached-glyphs");
  assert.deepEqual(result.layers.map(layer => layer.id), ["satellite-base", "hiking-paths", "hiking-path-names"]);
  assert.equal(result.layers[1].paint["line-color"], ROAD_COLOR);
  assert.deepEqual(result.layers[1].paint["line-dasharray"], [3, 2]);
  assert.equal(result.layers[1].minzoom, undefined);
});

test("manual fix only pans; zoom, bearing and pitch remain untouched", () => {
  const calls = [];
  const map = {stop() { calls.push("stop"); }, jumpTo(options) { calls.push(options); }};
  assert.equal(recenterOnFix(map, {coords: {latitude: 51.5, longitude: 4.3}}), true);
  assert.deepEqual(calls, ["stop", {center: [4.3, 51.5]}]);
  assert.equal(recenterOnFix(map, {coords: {latitude: NaN, longitude: 4.3}}), false);
  assert.equal(recenterOnFix(map, {coords: {latitude: 100, longitude: 4.3}}), false);
  assert.equal(recenterOnFix(null, {coords: {latitude: 51, longitude: 4}}), false);
  assert.equal(calls.length, 2);
});

test("paths match roads in color but stay dashed without a solid line underneath", () => {
  const layers = roadAndPathLayers();
  const [casing, roads, paths] = layers;
  assert.deepEqual(casing.filter, ["!", PATH_FILTER]);
  assert.deepEqual(roads.filter, ["!", PATH_FILTER]);
  assert.deepEqual(paths.filter, PATH_FILTER);
  assert.equal(paths.paint["line-color"], roads.paint["line-color"]);
  assert.equal(paths.paint["line-opacity"], roads.paint["line-opacity"]);
  assert.deepEqual(paths.paint["line-dasharray"], [3, 2]);
  assert.equal(roads.paint["line-dasharray"], undefined);
  const hiking = hikingMapStyle({layers: [...layers, {id: "names", type: "symbol"}]});
  assert.ok(!hiking.layers.some(layer => layer.id === "offline-paths"));
  const trail = hiking.layers.find(layer => layer.id === "hiking-paths");
  assert.equal(trail.paint["line-color"], ROAD_COLOR);
  assert.deepEqual(trail.paint["line-dasharray"], [3, 2]);
  const added = [];
  installIntelLayers({getStyle: () => ({layers: []}), getSource: () => null, addSource() {}, getLayer: () => null, addLayer: layer => added.push(layer)}, {id: "trails"});
  const overlay = added.find(layer => layer.id === "public-intel-trails-lines");
  assert.equal(overlay.paint["line-color"], ROAD_COLOR);
  assert.deepEqual(overlay.paint["line-dasharray"], [3, 2]);
});

test("walking uses an actual foot routing graph, never the driving server", () => {
  const url = calculatedRouteUrl([8.31, 47.05], [8.32, 47.04], undefined, "walking");
  assert.match(url, /^https:\/\/routing.openstreetmap.de\/routed-foot\/route\/v1\/driving\//);
  assert.match(calculatedRouteUrl([8, 47], [9, 47]), /^https:\/\/router.project-osrm.org\//);
  assert.equal(calculatedRouteUrl([8, 47], [9, 47], undefined, "unknown"), null);
});

test("manual recenter occurs before network send and not on automatic fixes", () => {
  const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
  const manual = app.slice(app.indexOf("function transmitPosition("), app.indexOf('$("mapSharePosition").addEventListener'));
  assert.ok(manual.indexOf("recenterKnownLocation()") < manual.indexOf("navigator.geolocation.getCurrentPosition"), "Locate must pan on its cached device fix before a fresh GPS request");
  assert.ok(manual.indexOf("localMapPositionHasCentered = true") < manual.indexOf("updateLocalMapPosition(position)"));
  assert.ok(manual.indexOf("recenterOnFix(") < manual.indexOf("await send("));
  assert.equal(app.match(/recenterCurrentLocation\(/g).length, 3, "cached and fresh fixes use the same map-ready recenter helper");
  assert.doesNotMatch(app, /currentNavigationMap/);
  assert.match(app, /function renderFieldMap\(events, \{preserveView = localMapPositionHasCentered\}/, "a later feed acknowledgement must not undo the manual camera choice");
  assert.match(app, /getLastKnownLocation/);
  assert.match(app, /maximumAge: 10_000/);
  assert.doesNotMatch(manual, /ACQUIRING FIX/);
});
