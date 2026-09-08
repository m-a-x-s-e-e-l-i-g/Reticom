import assert from "node:assert/strict";
import test from "node:test";
import {createLandmarkResolver} from "../src/retium/static/report-landmarks.js";

const tick = () => new Promise(resolve => setImmediate(resolve));
test("lookup coalesces requests and keeps each reference position independent", async () => {
  const urls = [];
  const resolver = createLandmarkResolver(async url => {
    urls.push(url);
    return {ok: true, json: async () => ({landmark: {coordinates: [4.1, 50.1], name: "Church"}})};
  });
  const report = {landmark: "CHURCH"};
  assert.equal(resolver(report, [4, 50]), null);
  assert.equal(resolver(report, [4, 50]), null);
  await tick();
  assert.equal(urls.length, 1);
  assert.deepEqual(resolver(report, [4, 50]).coordinates, [4.1, 50.1]);
  assert.equal(resolver(report, [6, 52]), null);
  await tick();
  assert.equal(urls.length, 2);
  assert.match(urls[1], /lon=6&lat=52/);
});

test("failed and empty lookups retry later without inventing coordinates", async () => {
  for (const response of [null, {ok: true, json: async () => ({landmark: null})}]) {
    let now = 0, calls = 0;
    const resolver = createLandmarkResolver(async () => {calls++; if (!response) throw Error("Offline"); return response;}, () => now);
    assert.equal(resolver({landmark: "CHURCH"}, [4, 50]), null);
    await tick();
    assert.equal(resolver({landmark: "CHURCH"}, [4, 50]), null);
    assert.equal(calls, 1);
    now = 61000;
    resolver({landmark: "CHURCH"}, [4, 50]);
    await tick();
    assert.equal(calls, 2);
  }
});


test("successful lookup requests a redraw after the location enters the cache", async () => {
  const report = {landmark: "CHURCH"};
  let redraws = 0;
  const resolver = createLandmarkResolver(async () => ({ok: true, json: async () => ({landmark: {coordinates: [4, 50]}})}),
    () => 0, () => { redraws++; assert.deepEqual(resolver(report, [4, 50]).coordinates, [4, 50]); });
  assert.equal(resolver(report, [4, 50]), null);
  await tick();
  assert.equal(redraws, 1);
});
