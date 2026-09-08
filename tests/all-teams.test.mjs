import test from 'node:test';
import assert from 'node:assert/strict';
import {allTeamsModel, overviewBounds, teamColor} from '../src/retium/static/all-teams-model.js';
import {encodeRouteGeometry} from '../src/retium/static/shared-navigation-model.js';

const now = 1700000000;
const event = (id, type, values = {}) => ({id, type, callsign: 'Alpha', created_at: now - 10,
  network: {sender_hash: 'same-identity', received_at: now - 10, verified: true}, ...values});
const position = (lat, lon) => event('same-fix', 'position.updated', {lat, lon, accuracy: 5});
const team = (id, events, extra = {}) => ({id, name: id, hosting: true, snapshot_available: true, open_tasks: 0, events, ...extra});

test('same callsign, identity and event IDs in different teams never merge', () => {
  const model = allTeamsModel([team('a', [position(50, 4)]), team('b', [position(52, 6)])], new Set(), now);
  assert.equal(model.features.features.length, 2);
  assert.equal(new Set(model.features.features.map((f) => f.id)).size, 2);
  assert.deepEqual(model.features.features.map((f) => f.geometry.coordinates), [[4, 50], [6, 52]]);
  assert.deepEqual(model.teams.map((t) => t.located), [1, 1]);
});
test('filters apply to both map and feed; routine GPS and private events stay out of feed', () => {
  const events = [position(50, 4), event('text', 'chat.message', {message: 'Hello'}), event('dm', 'private.message', {message: 'Private'})];
  const model = allTeamsModel([team('a', events), team('b', events)], new Set(['b']), now);
  assert.equal(model.features.features.length, 1);
  assert.deepEqual(model.feed.map((item) => [item.teamId, item.text]), [['a', 'Hello']]);
  assert.equal(model.teams.length, 2);
  assert.equal(allTeamsModel([team('a', events)], new Set(['a']), now).features.features.length, 0);
});
test('stopped hosts and old fixes are visibly stale, not active', () => {
  const old = {...position(50, 4), created_at: now - 1000, network: {sender_hash: 'same', received_at: now - 1000}};
  const model = allTeamsModel([team('old', [old]), team('stopped', [position(52, 6)], {hosting: false})], new Set(), now);
  assert.deepEqual(model.teams.map((t) => t.recent), [0, 0]);
  assert.ok(model.features.features.every((feature) => feature.properties.stale));
});
test('automatic reports use only the reporting team positions and cancellations', () => {
  const report = event('report', 'chat.message', {message: 'contact north 100 meters'});
  const cancel = event('cancel', 'chat.message', {message: 'cancel last contact', created_at: now - 2});
  const model = allTeamsModel([team('a', [report, position(50, 4)]), team('b', [cancel, report, position(52, 6)])], new Set(), now);
  const automatic = model.features.features.filter((f) => f.properties.kind === 'automatic');
  assert.ok(automatic.length > 0);
  assert.ok(automatic.every((f) => f.properties.teamId === 'a'));
  const point = automatic.find((f) => f.geometry.type === 'Point');
  assert.ok(point.geometry.coordinates[1] > 50 && point.geometry.coordinates[1] < 50.002);
  assert.equal(allTeamsModel([team('a', [report, position(50, 4)])], new Set(), now + 86400).features.features.filter((f) => f.properties.kind === 'automatic').length, 0);
});
test('cached speech waits for a published AI marker instead of creating a regex marker', () => {
  const model = allTeamsModel([team('a', [event('clip', 'ptt.broadcast', {transcript: 'evac point here'}), position(50, 4)])], new Set(), now);
  assert.ok(!model.features.features.some((f) => f.properties.kind === 'automatic'));
  const interpreted = allTeamsModel([team('a', [event('ai-marker', 'marker.created', {
    source_report_id: 'clip', marker_type: 'evac-point', label: 'Evac point', lat: 50, lon: 4,
  }), position(50, 4)])], new Set(), now);
  assert.ok(interpreted.features.features.some((f) => f.properties.label === 'Evac point' && f.properties.teamId === 'a'));
});
test('invalid coordinates and inaccurate GPS do not create map positions', () => {
  const model = allTeamsModel([team('a', [position(500, 4), {...position(50, 4), accuracy: 500}])], new Set(), now);
  assert.equal(model.features.features.length, 0);
});
test('team colors and feature IDs stay stable when another team is filtered', () => {
  const teams = [team('a', [position(50, 4)]), team('b', [position(52, 6)])];
  const full = allTeamsModel(teams, new Set(), now), filtered = allTeamsModel(teams, new Set(['a']), now);
  assert.equal(full.features.features[1].id, filtered.features.features[0].id);
  assert.equal(full.teams[1].color, teamColor('b'));
});
test('fit bounds uses the short span across the international dateline', () => {
  const features = [{geometry: {coordinates: [[179.5, 40], [-179.5, 41]]}}];
  const bounds = overviewBounds(features);
  assert.equal(bounds[1][0] - bounds[0][0], 1);
  assert.equal(overviewBounds([]), null);
});

const navigationSender = 'c'.repeat(32);
const navigationRouteId = '93aa011a-c850-4b68-b9a6-5f6c375ac63b';
const routePoints = [[4, 50], [4.1, 50.1], [4.2, 50.1]];
const sharedRoute = (values = {}) => event('shared-route', 'navigation.updated', {
  route_id: navigationRouteId, revision: 1, label: 'Rally A', mode: 'route', origin: routePoints[0], target: routePoints.at(-1),
  geometry: encodeRouteGeometry(routePoints), distance_m: 2000, duration_s: 500,
  network: {sender_hash: navigationSender, received_at: now - 10, verified: true}, ...values,
});

test('All Teams shows the complete shared path and destination with team and operator identity', () => {
  const model = allTeamsModel([team('a', [sharedRoute()]), team('b', [sharedRoute()])], new Set(), now);
  const routes = model.features.features.filter((feature) => feature.properties.kind === 'shared-navigation-route');
  const targets = model.features.features.filter((feature) => feature.properties.kind === 'shared-navigation-target');
  assert.equal(routes.length, 2);
  assert.equal(targets.length, 2);
  assert.deepEqual(routes[0].geometry.coordinates, routePoints);
  assert.deepEqual(targets[0].geometry.coordinates, routePoints.at(-1));
  assert.equal(targets[0].properties.symbol, 'NAV');
  assert.equal(targets[0].properties.caption, 'a · Alpha → Rally A');
  assert.equal(targets[0].properties.senderHash, navigationSender);
  assert.equal(routes[1].properties.color, teamColor('b'));
  assert.equal(new Set(model.features.features.map((feature) => feature.id)).size, 4);
  assert.equal(model.feed[0].text, 'Shared road route to Rally A');
});

test('All Teams applies route stops only to their own team, including old replay and hidden teams', () => {
  const stop = event('stopped-route', 'navigation.stopped', {route_id: navigationRouteId, revision: 2,
    network: {sender_hash: navigationSender, received_at: now - 5, verified: true}});
  const teams = [team('a', [stop, sharedRoute()]), team('b', [sharedRoute()]), team('c', [sharedRoute()])];
  const model = allTeamsModel(teams, new Set(['c']), now);
  assert.equal(model.features.features.length, 2);
  assert.ok(model.features.features.every((feature) => feature.properties.teamId === 'b'));
  assert.deepEqual(model.feed.map((item) => [item.teamId, item.text]), [
    ['a', 'Stopped sharing navigation'], ['b', 'Shared road route to Rally A'],
  ]);
});

test('All Teams retains a stopped host route as an explicitly stale last shared plan', () => {
  const model = allTeamsModel([team('a', [sharedRoute()], {hosting: false})], new Set(), now);
  assert.equal(model.features.features.length, 2);
  assert.ok(model.features.features.every((feature) => feature.properties.stale));
  assert.match(model.features.features[0].properties.description, /shared road route/);
});
