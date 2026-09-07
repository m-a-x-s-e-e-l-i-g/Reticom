import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {createContext, runInContext} from "node:vm";
import {navigationSharePayload, navigationWriter, createSharedNavigation} from "../src/retium/static/shared-navigation.js";

const sender = "a".repeat(32), other = "b".repeat(32);
const routeId = "ef8e1e86-f35b-42e6-a7dd-d0f4325409c8";
const points = [[4, 51], [4.01, 51.01], [4.02, 51.015]];
const distanceBetween = () => 1234;
const navigation = (changes = {}) => ({label: "Rally A", mode: "route", routeOrigin: points[0], currentPoint: [4.1, 51.1],
  target: points.at(-1), targetKind: "waypoint", id: "waypoint-a", routeCoordinates: points, routeDistance: 1234,
  routeDuration: 300, routeSteps: [{distance: 10, name: "High Street", maneuver: {type: "depart"}}], ...changes});
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const response = (payload, ok = true) => ({ok, json: async () => payload});
const plan = (changes = {}) => ({id: "event-a", route_id: routeId, revision: 1, sender_hash: sender, callsign: "Alpha",
  created_at: 1700000000, active: true, label: "Rally A", mode: "route", origin: points[0], target: points.at(-1),
  coordinates: points, distance_m: 1234, duration_s: 300, target_kind: "waypoint", target_id: "waypoint-a",
  network: {sender_hash: sender, verified: true}, ...changes});

test("sharing a calculated route retains every bend and compact directions", () => {
  const payload = navigationSharePayload(navigation(), distanceBetween);
  assert.deepEqual(payload.coordinates, points);
  assert.deepEqual(payload.origin, points[0]);
  assert.equal(payload.distance_m, 1234);
  assert.equal(payload.duration_s, 300);
  assert.deepEqual(payload.directions, [{text: "START · High Street", distance_m: 10}]);
  assert.equal("following" in payload, false);
});

test("sharing a direct line uses your current position only for unpinned navigation", () => {
  const state = navigation({mode: "direct", routeSteps: [], routeCoordinates: null});
  const payload = navigationSharePayload(state, distanceBetween);
  assert.deepEqual(payload.origin, state.currentPoint);
  assert.deepEqual(payload.coordinates, [state.currentPoint, state.target]);
  assert.equal(payload.duration_s, 0);
});

test("following a route preserves its exact geometry and directions rather than calculating a personal replacement", () => {
  const directions = [{text: "Turn left at the bridge", distance_m: 80}];
  const payload = navigationSharePayload(navigation({pinnedRoute: true,
    following: {sender_hash: other, route_id: routeId}, sharedDirections: directions}), distanceBetween);
  assert.deepEqual(payload.coordinates, points);
  assert.deepEqual(payload.origin, points[0]);
  assert.deepEqual(payload.directions, directions);
  assert.equal(payload.target_kind, "shared");
  assert.deepEqual(payload.following, {sender_hash: other, route_id: routeId});
});

test("resuming your own saved direct plan also preserves its old origin and geometry before GPS is available", () => {
  const directPoints = [points[0], points.at(-1)];
  const payload = navigationSharePayload(navigation({mode: "direct", pinnedRoute: true, following: undefined,
    currentPoint: null, routeCoordinates: directPoints, sharedDirections: []}), distanceBetween);
  assert.deepEqual(payload.origin, directPoints[0]);
  assert.deepEqual(payload.coordinates, directPoints);
});

test("unavailable GPS and incomplete or failed route calculation are not shared as a misleading route", () => {
  assert.throws(() => navigationSharePayload(null, distanceBetween), /Start navigation/);
  assert.throws(() => navigationSharePayload(navigation({mode: "direct", currentPoint: null}), distanceBetween), /location/);
  assert.throws(() => navigationSharePayload(navigation({routeCoordinates: null}), distanceBetween), /calculated route/);
  assert.throws(() => navigationSharePayload(navigation({routeLoading: true}), distanceBetween), /calculated route/);
  assert.throws(() => navigationSharePayload(navigation({routeError: "No route"}), distanceBetween), /calculated route/);
});

test("writes serialize PUT before DELETE and capture each operation's team at enqueue", async () => {
  const firstFetch = deferred(), calls = [];
  let team = "alpha";
  const write = navigationWriter({urlFor: path => `${path}?team=${team}`, fetchImpl: async (url, options) => {
    calls.push({url, ...options});
    return calls.length === 1 ? firstFetch.promise : response({stopped: true});
  }});
  const updating = write("PUT", {label: "Rally A"});
  const stopping = write("DELETE");
  team = "bravo";
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.length, 1);
  firstFetch.resolve(response({plan: plan()}));
  await Promise.all([updating, stopping]);
  assert.deepEqual(calls.map(call => [call.url, call.method]), [["/api/navigation?team=alpha", "PUT"], ["/api/navigation?team=alpha", "DELETE"]]);
});

test("a failed PUT does not poison the write queue or block a later stop", async () => {
  const calls = [];
  const write = navigationWriter({fetchImpl: async (_url, options) => {
    calls.push(options.method);
    return options.method === "PUT" ? response({detail: "Route too large"}, false) : response({stopped: true});
  }});
  const updating = write("PUT", {label: "Rally A"}), stopping = write("DELETE");
  await assert.rejects(updating, /Route too large/);
  assert.deepEqual(await stopping, {stopped: true});
  assert.deepEqual(calls, ["PUT", "DELETE"]);
});

test("write payload captures coordinate values at enqueue, not after waiting on another request", async () => {
  const hold = deferred(), calls = [];
  const write = navigationWriter({fetchImpl: async (_url, options) => {
    calls.push(options);
    return calls.length === 1 ? hold.promise : response({});
  }});
  const first = write("DELETE");
  const payload = {target: [4, 51]};
  const second = write("PUT", payload);
  payload.target[0] = 5;
  hold.resolve(response({}));
  await Promise.all([first, second]);
  assert.deepEqual(JSON.parse(calls[1].body).target, [4, 51]);
});

test("an older in-flight GET cannot overwrite a locally acknowledged newer route or stop", async () => {
  const previousDocument = globalThis.document;
  globalThis.document = {getElementById: () => null};
  try {
    const oldRead = deferred();
    const controller = createSharedNavigation({urlFor: path => path, getIdentity: () => sender, getRole: () => "field",
      onFollow() {}, onStopped() {}, toast() {}, fetchImpl: async (_url, options) => {
        if (options.method === "PUT") return response({plan: plan({id: "event-b", revision: 2})});
        if (options.method === "DELETE") return response({plan: plan({id: "event-stop", revision: 3, active: false, coordinates: []})});
        return oldRead.promise;
      }});
    controller.setContext("alpha");
    const pendingRead = controller.refresh();
    await controller.write("PUT", {});
    assert.equal(controller.own().revision, 2);
    await controller.write("DELETE");
    assert.equal(controller.own(), undefined);
    oldRead.resolve(response({plans: [plan()]}));
    await pendingRead;
    assert.equal(controller.own(), undefined);
  } finally { globalThis.document = previousDocument; }
});

test("a team switch clears route state and ignores responses captured for the previous team", async () => {
  const previousDocument = globalThis.document;
  globalThis.document = {getElementById: () => null};
  try {
    const pending = deferred();
    const controller = createSharedNavigation({urlFor: path => path, getIdentity: () => sender, getRole: () => "field",
      onFollow() {}, onStopped() {}, toast() {}, fetchImpl: async () => pending.promise});
    controller.setContext("alpha");
    const writing = controller.write("PUT", {});
    controller.setContext("bravo");
    pending.resolve(response({plan: plan()}));
    await writing;
    assert.equal(controller.own(), undefined);
  } finally { globalThis.document = previousDocument; }
});

test("field navigation is pinned to the shared geometry and visible to Command", () => {
  const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../src/retium/static/index.html", import.meta.url), "utf8");
  assert.match(app, /if \(!navigation \|\| navigation\.pinnedRoute \|\| navigation\.mode !== "route"/);
  assert.match(app, /if \(!waypointNavigation\.pinnedRoute && waypointNavigation\.mode === "route"/);
  assert.match(app, /calculated \|\| waypointNavigation\?\.pinnedRoute[\s\S]*?waypointNavigation\.routeCoordinates/);
  assert.match(app, /routeCoordinates: sharedPlan\.coordinates/);
  assert.match(app, /sharedNavigation\.attachMap\(map, Popup\)/);
  assert.match(app, /"shared-navigation-targets", "shared-navigation-lines"/);
  for (const id of ["commandSharedRoutes", "fieldSharedRoutes", "shareWaypointNavigation", "navigationShareState"]) assert.match(html, new RegExp(`id="${id}"`));
});

function appNavigationHarness(state) {
  const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
  const start = app.indexOf("function renderNavigationSharing() {"), end = app.indexOf("function showMapFeaturePopup(", start);
  assert.ok(start >= 0 && end > start, "use only the bounded shared-navigation function declarations");
  const nodes = new Map(), writes = [], messages = [];
  const context = createContext({
    waypointNavigation: state,
    navigationSharePayload, distanceMeters: distanceBetween,
    $: id => {
      if (!nodes.has(id)) nodes.set(id, {disabled: false, textContent: "", setAttribute() {}, classList: {toggle() {}}});
      return nodes.get(id);
    },
    sharedNavigation: {
      own: () => ({network: {queued: false}}),
      write: (method, body) => {
        const pending = deferred();
        writes.push({method, body: body ? structuredClone(body) : undefined, ...pending});
        return pending.promise;
      },
    },
    toast: (message, error) => messages.push({message, error}),
    renderWaypointNavigationLine() {}, updateWaypointNavigation() {},
    stopWaypointNavigation() { context.waypointNavigation = null; },
  });
  runInContext(app.slice(start, end), context, {filename: "app.js:shared-navigation-functions"});
  return {context, writes, messages};
}

const followingNavigation = (changes = {}) => navigation({pinnedRoute: true,
  following: {sender_hash: other, route_id: routeId}, sourceRevision: 1, sharedDirections: [],
  sharing: true, sharePending: false, shareDirty: false, shareGeneration: 0, shareEpoch: 0, ...changes});
const sourcePlan = (changes = {}) => plan({sender_hash: other, network: {sender_hash: other, verified: true}, ...changes});
const flushAsync = () => new Promise(resolve => setImmediate(resolve));

test("a source update during an in-flight share publishes the newest geometry immediately after its acknowledgement", async () => {
  const state = followingNavigation(), harness = appNavigationHarness(state);
  const publishing = harness.context.publishNavigation(state);
  assert.equal(harness.writes.length, 1);
  const updatedPoints = [points[0], [4.03, 51.01], [4.04, 51.02]];
  harness.context.synchronizeSharedNavigation([sourcePlan({revision: 2, coordinates: updatedPoints, target: updatedPoints.at(-1)})]);
  assert.equal(state.sourceRevision, 2);
  assert.equal(state.shareDirty, true);
  assert.equal(harness.writes.length, 1, "do not publish concurrently while the old update is pending");
  harness.writes[0].resolve({});
  await publishing;
  assert.equal(harness.writes.length, 2, "follow the acknowledgement with the newer route");
  assert.deepEqual(harness.writes[1].body.coordinates, updatedPoints);
  assert.deepEqual(harness.writes[1].body.target, updatedPoints.at(-1));
  assert.equal(state.sharePending, true);
  harness.writes[1].resolve({});
  await flushAsync();
  assert.equal(state.sharePending, false);
  assert.equal(state.shareDirty, false);
  assert.equal(harness.writes.length, 2);
});

test("stop sharing supersedes an older PUT acknowledgement and blocks updates until the DELETE completes", async () => {
  const state = followingNavigation(), harness = appNavigationHarness(state);
  const publishing = harness.context.publishNavigation(state);
  const stopping = harness.context.stopNavigationSharing();
  assert.equal(state.sharing, false);
  assert.equal(state.sharePending, true);
  harness.context.synchronizeSharedNavigation([sourcePlan({revision: 2})]);
  assert.deepEqual(harness.writes.map(item => item.method), ["PUT", "DELETE"]);
  harness.writes[0].resolve({});
  await publishing;
  assert.equal(state.sharing, false, "the earlier PUT cannot restore sharing after the user stopped it");
  assert.equal(state.sharePending, true, "the older PUT cannot clear the newer DELETE pending state");
  assert.equal(harness.writes.length, 2, "do not queue a dirty follow-up PUT after DELETE");
  harness.writes[1].resolve({});
  await stopping;
  await flushAsync();
  assert.equal(state.sharing, false);
  assert.equal(state.sharePending, false);
  assert.deepEqual(harness.writes.map(item => item.method), ["PUT", "DELETE"]);
});

test("a rejected share with a newer dirty source reports once without an automatic retry loop", async () => {
  const state = followingNavigation({sharing: false}), harness = appNavigationHarness(state);
  const publishing = harness.context.publishNavigation(state);
  harness.context.synchronizeSharedNavigation([sourcePlan({revision: 2})]);
  harness.writes[0].reject(new Error("Route exceeds network limit"));
  await publishing;
  await flushAsync();
  assert.equal(state.sharing, false);
  assert.equal(state.sharePending, false);
  assert.equal(harness.writes.length, 1);
  assert.equal(harness.messages.length, 1);
  assert.match(harness.messages[0].message, /Route exceeds network limit/);
});
