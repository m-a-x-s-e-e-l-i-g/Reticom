import assert from "node:assert/strict";
import test from "node:test";
import {decodeRouteGeometry, encodeRouteGeometry, latestNavigationPlans, navigationColor, sharedNavigationFeatures}
  from "../src/retium/static/shared-navigation-model.js";

const sender = "a".repeat(32), other = "b".repeat(32);
const routeId = "a53e58ef-578b-4541-a545-7bd76f97d0fb";
const anotherRoute = "27ae04d3-f2df-41c6-a4c1-b4c513e05820";
const points = [[4.29123, 51.49123], [4.29456, 51.49567], [4.30123, 51.50123]];
function route(changes = {}) {
  return {id: "event-a", type: "navigation.updated", callsign: "Alpha", created_at: 1700000000,
    route_id: routeId, revision: 1, label: "Rally A", mode: "route", origin: points[0], target: points.at(-1),
    target_kind: "waypoint", target_id: "waypoint-a", geometry: encodeRouteGeometry(points),
    distance_m: 1400, duration_s: 180, network: {sender_hash: sender, verified: true}, ...changes};
}
function stopped(changes = {}) {
  return {id: "event-stop", type: "navigation.stopped", callsign: "Alpha", created_at: 1700000001,
    route_id: routeId, revision: 2, network: {sender_hash: sender, verified: true}, ...changes};
}

test("polyline geometry matches the standard latitude-first wire format and retains every point", () => {
  const example = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]];
  assert.equal(encodeRouteGeometry(example), "_p~iF~ps|U_ulLnnqC_mqNvxq`@");
  assert.deepEqual(decodeRouteGeometry("_p~iF~ps|U_ulLnnqC_mqNvxq`@"), example);
  assert.deepEqual(decodeRouteGeometry(encodeRouteGeometry(points)), points);
  const poles = [[-180, -90], [180, 90], [-180, -90]];
  assert.deepEqual(decodeRouteGeometry(encodeRouteGeometry(poles)), poles);
});

test("malformed, non-canonical, unbounded and incomplete polylines cannot enter the map", () => {
  for (const value of [null, "", "?", "??", "?????", "_???", "~~~~~~~????", "0000", "\u00e9???", "?".repeat(12001), "?".repeat(8002)]) {
    assert.throws(() => decodeRouteGeometry(value), /shared route/i);
  }
  assert.throws(() => encodeRouteGeometry(Array.from({length: 4001}, () => [4, 52])), /shared route/i);
  assert.throws(() => encodeRouteGeometry([[181, 52], [4, 52]]), /shared route/i);
  assert.throws(() => encodeRouteGeometry([["4", 52], [4, 52]]), /shared route/i);
  assert.deepEqual(latestNavigationPlans([route({geometry: "_???"})]), []);
});

test("raw signed events and expanded API plans produce the identical road geometry", () => {
  const raw = route();
  const {geometry, type, ...api} = raw;
  const expanded = {...api, sender_hash: sender, active: true, coordinates: points};
  const plans = latestNavigationPlans([raw]);
  assert.deepEqual(plans[0].coordinates, points);
  assert.equal(plans[0].updated_at, raw.created_at);
  assert.deepEqual(sharedNavigationFeatures([raw]), sharedNavigationFeatures([expanded]));
  assert.deepEqual(points, [[4.29123, 51.49123], [4.29456, 51.49567], [4.30123, 51.50123]]);
});

test("revision and stop tombstones dominate arrival order, timestamps and route identifiers", () => {
  const update = route({created_at: 1900000000}), stop = stopped({created_at: 1});
  assert.deepEqual(latestNavigationPlans([update, stop]), []);
  assert.deepEqual(latestNavigationPlans([stop, update]), []);
  assert.equal(latestNavigationPlans([update, stop], {includeStopped: true})[0].active, false);
  assert.deepEqual(latestNavigationPlans([route({revision: 2}), stop]), []);
  // A restart creates another route ID. Older stops must not remove this plan.
  const restarted = route({route_id: anotherRoute, revision: 3, id: "event-restart"});
  assert.equal(latestNavigationPlans([restarted, stop])[0].route_id, anotherRoute);
  assert.equal(latestNavigationPlans([stop, restarted])[0].route_id, anotherRoute);
});

test("another operator cannot cancel a route by copying its route ID or payload sender", () => {
  const foreignStop = stopped({revision: 100, sender_hash: sender, network: {sender_hash: other, verified: true}});
  const plans = latestNavigationPlans([route(), foreignStop]);
  assert.equal(plans.length, 1);
  assert.equal(plans[0].sender_hash, sender);
  assert.equal(latestNavigationPlans([route({network: {}, sender_hash: sender})]).length, 0);
  assert.equal(latestNavigationPlans([route({network: {sender_hash: sender, verified: false}})]).length, 0);
});

test("queued local plans are visible, and a receipt replaces the queued presentation", () => {
  const queued = route({network: {sender_hash: sender, verified: false, queued: true}});
  assert.equal(latestNavigationPlans([queued])[0].queued, true);
  assert.equal(sharedNavigationFeatures([queued]).features[0].properties.queued, true);
  assert.equal(latestNavigationPlans([queued, route()])[0].queued, false);
  assert.equal(latestNavigationPlans([route(), queued])[0].queued, false);
});

test("one current plan is kept per sender, including distinct plans with the same route ID", () => {
  const otherPlan = route({id: "event-b", callsign: "Bravo", network: {sender_hash: other, verified: true}});
  assert.equal(latestNavigationPlans([route(), otherPlan]).length, 2);
  const features = sharedNavigationFeatures([otherPlan, route()]).features;
  assert.equal(features.length, 4);
  assert.equal(new Set(features.map((feature) => feature.id)).size, 4);
});

test("direct routes must match both declared endpoints; road routes preserve all bends", () => {
  const direct = route({mode: "direct", geometry: encodeRouteGeometry([points[0], points.at(-1)])});
  assert.equal(latestNavigationPlans([direct]).length, 1);
  assert.equal(latestNavigationPlans([route({mode: "direct"})]).length, 0);
  assert.equal(latestNavigationPlans([direct, {...direct, target: [5, 52]}]).length, 1);
  assert.deepEqual(sharedNavigationFeatures([route()]).features[0].geometry.coordinates, points);
  assert.deepEqual(sharedNavigationFeatures([route()]).features[1].geometry.coordinates, points.at(-1));
});

test("invalid revisions and coordinates are ignored without breaking the other operators", () => {
  for (const changes of [{revision: 0}, {revision: 1.2}, {revision: 1000000001}, {origin: [null, 52]},
    {target: [4, 100]}, {label: ""}, {route_id: "not-an-id"}, {mode: "driving"}]) {
    assert.deepEqual(latestNavigationPlans([route(changes)]), []);
  }
  assert.equal(latestNavigationPlans([route(), null, {active: true}]).length, 1);
});

test("map properties identify owner and destination without pretending a saved plan is live GPS", () => {
  const maliciousLabel = "<img src=x onerror=alert(1)>";
  const collection = sharedNavigationFeatures([route({label: maliciousLabel, color: "red;url(x)"})], {ownIdentity: sender});
  const [line, target] = collection.features;
  assert.equal(line.properties.kind, "shared-navigation-route");
  assert.equal(target.properties.kind, "shared-navigation-target");
  assert.equal(target.properties.label, maliciousLabel); // Renderers must use textContent or HTML escaping.
  assert.equal(line.properties.senderHash, sender);
  assert.equal(line.properties.routeId, routeId);
  assert.equal(line.properties.own, true);
  assert.equal(line.properties.color, navigationColor(sender));
  assert.equal(line.properties.stale, false);
  assert.match(line.properties.description, /shared road route/);
  const stale = sharedNavigationFeatures([route()], {nowSeconds: 1700005000, staleAfterSeconds: 3600}).features[0];
  assert.equal(stale.properties.stale, true);
});
