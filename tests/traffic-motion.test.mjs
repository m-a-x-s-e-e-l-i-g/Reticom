import test from "node:test";
import assert from "node:assert/strict";
import {TrafficMotion, projectTraffic, createTrafficAnimator} from "../src/retium/static/traffic-motion.js";
import {intelFeatureHtml, createIntelPacks} from "../src/retium/static/intel-packs.js";

const feature = (props = {}, coordinates = [0, 0]) => ({type: "Feature", id: "a", geometry: {type: "Point", coordinates},
  properties: {position_time: 1000, speed_knots: 360, heading: 90, traffic_pack: "flights", ...props}});
const data = f => ({type: "FeatureCollection", features: [f]});
const position = result => result.data.features[0].geometry.coordinates;
const close = (a, b, tolerance = 1e-8) => assert.ok(Math.abs(a - b) < tolerance, `${a} != ${b}`);

test("speed in knots advances by real distance, preserving original coordinates and timestamps", () => {
  const model = new TrafficMotion("flights"), report = feature();
  model.sample(data(report), 1000);
  const result = model.sample(data(report), 1010);
  close(position(result)[0], 1852 / 6371008.8 * 180 / Math.PI);
  close(position(result)[1], 0);
  assert.deepEqual(report.geometry.coordinates, [0, 0]);
  assert.equal(result.data.features[0].properties.position_time, 1000);
  assert.equal(result.data.features[0].properties.display_estimated, true);
  assert.equal(result.active, true);
});

test("prediction freezes at its horizon, then expiry removes the target", () => {
  for (const [id, horizon, expiry] of [["flights", 30, 120], ["vessels", 60, 600]]) {
    const model = new TrafficMotion(id), report = data(feature({speed_knots: 20, course: 90}));
    const held = model.sample(report, 1000 + horizon);
    assert.equal(held.active, false);
    assert.deepEqual(position(model.sample(report, 1001 + horizon)), position(held));
    assert.match(held.data.features[0].properties.motion_status, /held/);
    assert.equal(model.sample(report, 1001 + expiry).data.features.length, 0);
  }
});

test("boats move along course over ground, not their bow heading; stopped/anchored boats do not drift", () => {
  const model = new TrafficMotion("vessels");
  const result = model.sample(data(feature({course: 0, heading: 90, speed_knots: 10})), 1010);
  close(position(result)[0], 0);
  assert.ok(position(result)[1] > 0);
  assert.equal(result.data.features[0].properties.heading, 90);
  for (const props of [{course: null}, {course: 360}, {speed_knots: 0}, {speed_knots: .2}, {navigation_status: 1}, {navigation_status: 5}, {speed_knots: 102.3}]) {
    const stopped = new TrafficMotion("vessels").sample(data(feature({course: 90, speed_knots: 10, ...props})), 1010);
    assert.deepEqual(position(stopped), [0, 0]); assert.equal(stopped.active, false);
  }
});

test("invalid or absent motion data never becomes northbound motion", () => {
  for (const props of [{heading: null}, {heading: NaN}, {heading: 360}, {speed_knots: null}, {speed_knots: -1}, {speed_knots: Infinity}, {speed_knots: true}, {position_time: 1020}]) {
    const result = new TrafficMotion("flights").sample(data(feature(props)), 1010);
    assert.deepEqual(position(result), [0, 0]); assert.equal(result.active, false);
  }
  assert.equal(new TrafficMotion("flights").sample(data(feature({}, [NaN, 0])), 1010).data.features.length, 0);
});

test("new reports blend from the current displayed position and finish at the moving estimate", () => {
  const model = new TrafficMotion("flights"), old = data(feature());
  model.sample(old, 1000);
  const before = position(model.sample(old, 1010));
  const fresh = data(feature({position_time: 1010, heading: 100}, [.02, .001]));
  const immediate = position(model.sample(fresh, 1010));
  close(immediate[0], before[0]); close(immediate[1], before[1]);
  assert.equal(model.sample(fresh, 1010.5).active, true);
  const expected = projectTraffic([.02,.001], 100, 185.2);
  const finished = position(model.sample(fresh, 1011));
  close(finished[0], expected[0]); close(finished[1], expected[1]);
});

test("rotation and interpolation cross north and the date line by the short path", () => {
  const model = new TrafficMotion("flights");
  model.sample(data(feature({heading: 350, speed_knots: 0}, [179.999, 0])), 1000);
  const fresh = data(feature({position_time: 1001, heading: 10, speed_knots: 0}, [-179.999, 0]));
  model.sample(fresh, 1001);
  const halfway = model.sample(fresh, 1001.5);
  close(halfway.data.features[0].properties.heading, 0);
  assert.ok(Math.abs(position(halfway)[0]) > 179.9);
  const projected = projectTraffic([179.999, 80], 90, 1000);
  assert.ok(projected[0] < -179 && projected.every(Number.isFinite));
});

test("old reports cannot rewind a target and repeated cached reports cannot restart correction", () => {
  const model = new TrafficMotion("flights");
  const old = data(feature()), fresh = data(feature({position_time: 1010}, [.02, 0]));
  model.sample(old, 1000); model.sample(fresh, 1010);
  model.sample(fresh, 1010.5);
  const result = model.sample(old, 1012);
  assert.equal(result.data.features[0].properties.position_time, 1010);
  assert.equal(result.data.features[0].properties.motion_status, "Estimated between reports");
});

test("large reacquisition jumps are not animated across the map; removed targets release state", () => {
  const model = new TrafficMotion("flights");
  model.sample(data(feature()), 1000);
  assert.deepEqual(position(model.sample(data(feature({position_time: 1010}, [40, 20])), 1010)), [40, 20]);
  model.sample({features: []}, 1011); assert.equal(model.tracks.size, 0);
});

test("reduced motion returns reported positions without prediction or smoothing", () => {
  const model = new TrafficMotion("flights"), report = data(feature());
  model.sample(report, 1010);
  const result = model.sample(report, 1011, false);
  assert.deepEqual(position(result), [0, 0]); assert.equal(result.active, false);
  assert.equal(result.data.features[0].properties.display_estimated, false);
});

test("animation is frame-limited and stops when hidden, idle or destroyed", () => {
  let callback, paints = 0, visible = true, active = true, cancelled = 0;
  const animator = createTrafficAnimator({paint: () => { paints++; return active; }, visible: () => visible,
    requestFrame: fn => { callback = fn; return 1; }, cancelFrame: () => { cancelled++; callback = null; }, clock: () => 1000});
  const step = time => { const fn = callback; callback = null; fn(time); };
  animator.start(); step(0); step(10); step(34); assert.equal(paints, 2);
  visible = false; step(68); assert.equal(callback, null); assert.equal(paints, 2);
  visible = true; animator.start(); active = false; step(100); assert.equal(callback, null);
  active = true; animator.start(); animator.destroy(); assert.equal(cancelled, 1);
  animator.start(); assert.equal(callback, null);
});

test("traffic popup explicitly identifies estimated or held positions", () => {
  const shown = new TrafficMotion("flights").sample(data(feature()), 1040).data.features[0];
  const html = intelFeatureHtml(shown, {id: "flights", source: "Public test source"}, {});
  assert.match(html, /Estimate held/); assert.match(html, /30 seconds beyond report/);
});

test("map integration animates without new fetches, and detaching cancels its frame", async () => {
  const originalRequest = globalThis.requestAnimationFrame, originalCancel = globalThis.cancelAnimationFrame, originalNow = Date.now;
  let frame = null, clock = 1000000;
  globalThis.requestAnimationFrame = callback => { frame = callback; return 1; };
  globalThis.cancelAnimationFrame = () => { frame = null; };
  Date.now = () => clock;
  const sources = new Map(), layers = new Map(), events = new Map(), requests = [];
  const map = {getStyle: () => ({layers: [...layers.values()]}), getZoom: () => 12,
    getBounds: () => ({west: -1, south: -1, east: 1, north: 1}),
    getSource: id => sources.get(id), addSource: (id, value) => sources.set(id, {...value, setData(data) { this.data = data; }}), removeSource: id => sources.delete(id),
    getLayer: id => layers.get(id), addLayer: layer => layers.set(layer.id, layer), removeLayer: id => layers.delete(id),
    on: (name, fn) => events.set(name, fn), off: name => events.delete(name),
    jumpTo: () => assert.fail("Animation must not move the map"), fitBounds: () => assert.fail("Animation must not reframe the map")};
  let controller;
  const report = feature();
  try {
    controller = createIntelPacks({fetchImpl: async url => { requests.push(url); return {ok: true, json: async () => url === "/api/intel-packs"
      ? {packs: [{id: "flights", configured: true, enabled: true}], settings: {enabled: ["flights"]}}
      : {...data(report), status: "fresh"}}; }});
    await controller.ready; controller.attachMap(map);
    await new Promise(resolve => setTimeout(resolve, 30));
    const count = requests.length;
    assert.ok(frame);
    clock += 500; frame(0);
    const first = sources.get("public-intel-flights").data.features[0].geometry.coordinates[0];
    clock += 500; frame(500);
    assert.ok(sources.get("public-intel-flights").data.features[0].geometry.coordinates[0] > first);
    assert.equal(requests.length, count);
    assert.deepEqual(report.geometry.coordinates, [0, 0]);
    events.get("remove")(); assert.equal(frame, null);
  } finally {
    controller?.destroy(); Date.now = originalNow;
    globalThis.requestAnimationFrame = originalRequest; globalThis.cancelAnimationFrame = originalCancel;
  }
});
