import assert from "node:assert/strict";
import test from "node:test";

import {compassCardinal, compassTargetIndicator, compassTicks, normalizeHeading} from "../src/retium/static/compass-tape.js";

test("normalizes sensor headings around north", () => {
  assert.equal(normalizeHeading(-10), 350);
  assert.equal(normalizeHeading(370), 10);
  assert.equal(normalizeHeading("bad"), null);
});

test("uses familiar eight-point compass labels", () => {
  assert.equal(compassCardinal(0), "N");
  assert.equal(compassCardinal(90), "E");
  assert.equal(compassCardinal(225), "SW");
  assert.equal(compassCardinal(359), "N");
});

test("builds a centered numerical tape across the north wrap", () => {
  const ticks = compassTicks(355);
  const north = ticks.find((tick) => tick.degrees === 0);

  assert.ok(north);
  assert.equal(north.label, "N");
  assert.ok(north.left > 50);
  assert.ok(ticks.some((tick) => tick.label === "330"));
  assert.ok(ticks.some((tick) => tick.label === "030"));
});

test("places target bearings relative to the current heading", () => {
  assert.equal(compassTargetIndicator(0, 0).left, 50);
  assert.equal(compassTargetIndicator(0, 30).left, 75);
  assert.equal(compassTargetIndicator(0, 330).left, 25);
  assert.equal(compassTargetIndicator(350, 10).left, 50 + (20 / 120) * 100);
});

test("holds targets outside the visible tape at the correct edge", () => {
  assert.deepEqual(compassTargetIndicator(0, 100), {left: 97, offset: 100, offscreen: true, side: "right"});
  assert.deepEqual(compassTargetIndicator(0, 260), {left: 3, offset: -100, offscreen: true, side: "left"});
  assert.equal(compassTargetIndicator("bad", 90), null);
});
