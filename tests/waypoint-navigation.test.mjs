import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {createContext, runInContext} from "node:vm";
import {distanceMeters} from "../src/retium/static/location-filter.js";
import {numberedWaypointSequence} from "../src/retium/static/map-labels.js";
import {calculatedRouteUrl, parseCalculatedRoute, routeNeedsRefresh} from "../src/retium/static/route-navigation.js";

const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
const controller = app.slice(app.indexOf("function renderWaypointNavigationLine("), app.indexOf("function renderNavigationSharing("));
const sequence = Array.from({length: 6}, (_, i) => ({id: String(i + 1), label: `Waypoint ${i + 1}`, target: [14.5 + i * .005, 35.9]}));
const position = ([longitude, latitude], accuracy = 5) => ({coords: {longitude, latitude, accuracy}});

test("waypoint navigation follows each numbered stop and requests fresh route legs as it progresses", async () => {
  const nodes = new Map(), requests = [], arrivals = [], sharedTargets = [], driving = [], sharingWrites = [];
  let rendered;
  const context = createContext({
    waypointNavigation: null, waypointNavigationWatchId: null, nativeLocation: null,
    intelPacks: {setDrivingRoads: enabled => driving.push(enabled)},
    WAYPOINT_ARRIVAL_RADIUS_METERS: 35, WAYPOINT_MAX_ARRIVAL_ACCURACY_METERS: 75,
    navigator: {geolocation: {watchPosition() {}, clearWatch() {}}},
    localStorage: {getItem: () => null},
    fieldMap: {getSource: () => ({setData: data => { rendered = data; }}), fitBounds() {}},
    $: id => {
      if (!nodes.has(id)) nodes.set(id, {dataset: {}, classList: {toggle() {}, remove() {}, add() {}}});
      return nodes.get(id);
    },
    calculatedRouteUrl, parseCalculatedRoute, routeNeedsRefresh, distanceMeters, numberedWaypointSequence, AbortController,
    setTimeout, clearTimeout, ROUTING_MODE_KEY: "routing",
    calculateDeviceRoute(origin, target, profile, {signal}) {
      return new Promise(resolve => requests.push({origin, target, profile, signal, resolve: () => resolve({
        code: "Ok", source: "offline", routes: [{geometry: {type: "LineString", coordinates: [origin, target]}}],
      })}));
    },
    renderNavigationSharing() {}, syncNativeLocationDemand() {}, toast() {},
    sharedNavigation: {write: async method => sharingWrites.push(method)},
    publishNavigation: navigation => {
      sharedTargets.push(navigation.id);
      navigation.sharing = true;
      navigation.lastSharedAt = Date.now();
      navigation.lastSharedOrigin = navigation.currentPoint;
      navigation.lastSharedMode = "direct";
    },
    routeInstruction: () => "CONTINUE", bearingDegrees: () => 0, cardinalBearing: () => "N",
    formatNavigationDistance: String, formatRouteDuration: String, matchMedia: () => ({matches: true}),
  });
  runInContext(controller, context);
  context.reportWaypointArrival = () => arrivals.push(context.waypointNavigation.id);
  const last = sequence.at(-1);
  const first = sequence[0];
  context.startNavigation(first, first.target, null, "waypoint", "route", null, "walking", sequence);
  assert.deepEqual(sharedTargets, ["1"], "the starting waypoint is published before the first GPS fix");
  assert.equal(nodes.get("waypointNavigationLabel").textContent, "Waypoint 1 · 1/6");
  assert.deepEqual(rendered.features[0].geometry.coordinates.map(point => point[0]).join(","), sequence.map(stop => stop.target[0]).join(","));
  context.updateWaypointNavigation(position([14.48, 35.9]));
  context.updateWaypointNavigation(position(sequence[0].target, 100));
  assert.equal(context.waypointNavigation.id, "1");
  for (let i = 0; i < sequence.length - 1; i++) {
    const current = requests.at(-1);
    assert.equal(current.profile, "walking");
    assert.deepEqual(current.target, sequence[i].target);
    context.updateWaypointNavigation(position(sequence[i].target));
    assert.equal(current.signal.aborted, true);
    current.resolve();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(context.waypointNavigation.routeLoading, true, "superseded results must leave the next leg alone");
    assert.equal(context.waypointNavigation.routeCoordinates, null);
    assert.equal(context.waypointNavigation.id, sequence[i + 1].id);
  }
  requests.at(-1).resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(nodes.get("waypointNavigationLabel").textContent, "Waypoint 6 · 6/6");
  assert.deepEqual(sharedTargets, ["1", "2", "3", "4", "5", "6"]);
  assert.deepEqual(arrivals, []);
  const seventh = {id: "7", label: "Waypoint 7", target: [14.53, 35.9]};
  context.updateNavigationWaypoints(context.waypointNavigation, [...sequence, seventh].map(stop => ({
    ...stop, lon: stop.target[0], lat: stop.target[1],
  })));
  context.updateWaypointNavigation(position(last.target));
  assert.equal(nodes.get("waypointNavigationLabel").textContent, "Waypoint 7 · 7/7");
  assert.equal(sharedTargets.at(-1), "7");
  assert.deepEqual(arrivals, []);

  requests.at(-1).resolve();
  await new Promise(resolve => setImmediate(resolve));
  context.waypointNavigation.sharing = false;
  context.startNavigation(last, last.target, null, "waypoint", "direct");
  assert.equal(sharedTargets.at(-1), "6");
  assert.equal(context.waypointNavigation.id, "6");
  assert.equal(nodes.get("waypointNavigationLabel").textContent, "Waypoint 6");

  const closeStops = sequence.slice(0, 3).map(stop => ({...stop, target: first.target}));
  context.startNavigation(first, first.target, null, "waypoint", "direct", null, "driving", closeStops);
  const fix = position(first.target);
  context.updateWaypointNavigation(fix);
  assert.equal(context.waypointNavigation.id, "2");
  context.updateWaypointNavigation(fix);
  assert.equal(context.waypointNavigation.id, "2", "reusing an arrival fix must not skip the next waypoint");
  context.updateWaypointNavigation(position(first.target));
  assert.equal(context.waypointNavigation.id, "3");

  context.startNavigation(first, first.target, null, "waypoint", "route", null, "driving");
  assert.equal(driving.at(-1), true);
  assert.equal(sharedTargets.at(-1), "1");
  context.startNavigation(last, last.target, null, "waypoint", "route", null, "walking");
  assert.equal(driving.at(-1), false);
  context.startNavigation(first, first.target, null, "waypoint", "route", null, "driving");
  const previousWrites = sharingWrites.length;
  context.stopWaypointNavigation();
  assert.equal(driving.at(-1), false);
  assert.deepEqual(sharingWrites.slice(previousWrites), ["DELETE"]);
});
