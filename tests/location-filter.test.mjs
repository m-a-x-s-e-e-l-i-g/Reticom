import assert from "node:assert/strict";
import test from "node:test";

import {displayPositionSeries} from "../src/retium/static/location-filter.js";

function fix(id, time, lat, lon, accuracy = 8) {
  return {
    id,
    type: "position.updated",
    callsign: "ALPHA",
    lat,
    lon,
    accuracy,
    created_at: time,
    network: {received_at: time, sender_hash: "alpha"},
  };
}

test("ignores an inaccurate fix after a trusted position", () => {
  const series = displayPositionSeries([
    fix("good", 1_000, 51.5, 4.3, 8),
    fix("poor", 1_015, 51.51, 4.31, 400),
  ]);

  assert.deepEqual(series.map((event) => event.id), ["good"]);
});

test("removes an isolated GPS teleport from the path and current position", () => {
  const series = displayPositionSeries([
    fix("start", 1_000, 51.5, 4.3),
    fix("jump", 1_015, 51.51, 4.31),
    fix("return", 1_030, 51.5001, 4.3001),
  ]);

  assert.deepEqual(series.map((event) => event.id), ["start", "return"]);
  assert.ok(series.at(-1).lat < 51.501);
  assert.ok(series.at(-1).lon < 4.301);
});

test("accepts a rapid relocation after a consistent confirmation", () => {
  const series = displayPositionSeries([
    fix("start", 1_000, 51.5, 4.3),
    fix("candidate", 1_015, 51.51, 4.31),
    fix("confirmed", 1_030, 51.5101, 4.3101),
  ]);

  assert.deepEqual(series.map((event) => event.id), ["start", "confirmed"]);
  assert.ok(series.at(-1).lat > 51.505);
});

test("reported accuracy controls the smoothed display weight", () => {
  const accurate = displayPositionSeries([
    fix("start", 1_000, 51.5, 4.3, 5),
    fix("accurate", 1_015, 51.5002, 4.3002, 5),
  ]).at(-1);
  const uncertain = displayPositionSeries([
    fix("start", 1_000, 51.5, 4.3, 5),
    fix("uncertain", 1_015, 51.5002, 4.3002, 80),
  ]).at(-1);

  assert.ok(accurate.lat > uncertain.lat);
  assert.ok(accurate.lon > uncertain.lon);
});

test("does not average a resumed position with an old stale fix", () => {
  const series = displayPositionSeries([
    fix("old", 1_000, 51.5, 4.3),
    fix("candidate", 5_000, 51.51, 4.31),
    fix("resumed", 5_015, 51.5101, 4.3101),
  ]);

  assert.ok(series.at(-1).lat > 51.51);
  assert.ok(series.at(-1).lon > 4.31);
});
