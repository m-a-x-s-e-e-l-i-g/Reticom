import test from "node:test";
import assert from "node:assert/strict";
import {calculateDeviceRoute} from "../src/retium/static/offline-routing.js";

const origin = [4, 51], target = [4.1, 51.1];
const response = (payload, ok = true) => ({ok, json: async () => payload});

test("uses local routing without making any external request", async () => {
  const calls = [];
  const result = await calculateDeviceRoute(origin, target, "walking", {
    fetcher: async (url, options) => { calls.push(url); assert.equal(JSON.parse(options.body).profile, "walking"); return response({code: "Ok", source: "offline"}); },
    beforeOnline: () => assert.fail("must not reserve online request"),
  });
  assert.equal(result.source, "offline");
  assert.deepEqual(calls, ["/api/offline-routing/route"]);
});

for (const code of ["outside_coverage", "engine_unavailable"]) {
  test(`online fallback only when allowed: ${code}`, async () => {
    const calls = [];
    const fetcher = async url => { calls.push(url); return calls.length === 1 ? response({code, detail: "Missing region"}, false) : response({code: "Ok"}); };
    assert.equal((await calculateDeviceRoute(origin, target, "walking", {fetcher})).source, "online");
    assert.match(calls[1], /^https:\/\/routing.openstreetmap.de\/routed-foot/);
    calls.length = 0;
    await assert.rejects(calculateDeviceRoute(origin, target, "walking", {fetcher, offlineOnly: true}), /Missing region/);
    assert.equal(calls.length, 1);
  });
}

for (const code of ["no_route", "engine_error", "invalid_coordinates"]) {
  test(`does not leak coordinates after local ${code}`, async () => {
    let calls = 0;
    await assert.rejects(calculateDeviceRoute(origin, target, "driving", {
      fetcher: async () => { calls++; return response({code, detail: "Cannot calculate"}, false); },
    }), /Cannot calculate/);
    assert.equal(calls, 1);
  });
}

test("cancelling before fallback prevents an online request", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(calculateDeviceRoute(origin, target, "driving", {
    signal: controller.signal,
    fetcher: async () => { calls++; return response({code: "outside_coverage"}, false); },
    beforeOnline: async () => controller.abort(),
  }), {name: "AbortError"});
  assert.equal(calls, 1);
});
