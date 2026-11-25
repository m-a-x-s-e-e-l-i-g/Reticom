import assert from "node:assert/strict";
import test from "node:test";
import {commsPositionEvent} from "../src/retium/static/comms-only.js";
import {locationTime} from "../src/retium/static/location-filter.js";

test("radio reports a recent cached fix with its original time", () => {
  const fix = {latitude: 52.1, longitude: 4.31, accuracy: 16, timestamp: 1_000_000};
  const event = commsPositionEvent(fix, 1_300_000);
  assert.deepEqual(event, {type: "position.updated", lat: 52.1, lon: 4.31, accuracy: 16, created_at: 1000});
  assert.equal(locationTime({...event, network: {received_at: 1300}}), 1000);
});

test("radio defaults to voice only for absent, expired, future or invalid fixes", () => {
  const fix = {latitude: 52.1, longitude: 4.31, accuracy: 16, timestamp: 1_000_000};
  assert.equal(commsPositionEvent(null, 1_100_000), null);
  assert.equal(commsPositionEvent(fix, 1_300_001), null);
  assert.equal(commsPositionEvent(fix, 999_999), null);
  assert.equal(commsPositionEvent({...fix, accuracy: null}, 1_100_000), null);
  assert.equal(commsPositionEvent({...fix, longitude: 999}, 1_100_000), null);
});
