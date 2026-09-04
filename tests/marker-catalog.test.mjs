import test from "node:test";
import assert from "node:assert/strict";

import {
  TACTICAL_MARKERS,
  TACTICAL_MARKER_TYPES,
  TACTICAL_PALETTE_MARKERS,
  TACTICAL_UNIT_MARKERS,
  tacticalMarker,
  tacticalMarkerGroups,
} from "../src/retium/static/marker-catalog.js";

test("tactical marker catalog exposes every manual report marker once", () => {
  assert.equal(TACTICAL_MARKERS.length, 22);
  assert.equal(new Set(TACTICAL_MARKER_TYPES).size, TACTICAL_MARKERS.length);
  assert.deepEqual(
    TACTICAL_MARKERS.map(({type}) => type),
    TACTICAL_MARKER_TYPES,
  );
});

test("tactical marker groups cover the complete catalog", () => {
  const groups = tacticalMarkerGroups();
  assert.deepEqual(
    groups.map(({label}) => label),
    ["Medical", "Control", "Threat / hazard", "Movement / support", "Units"],
  );
  assert.deepEqual(
    groups.flatMap(({markers}) => markers.map(({type}) => type)),
    TACTICAL_PALETTE_MARKERS.map(({type}) => type),
  );
});

test("vehicle and aircraft markers live in the Tactical palette", () => {
  assert.deepEqual(
    TACTICAL_UNIT_MARKERS.map(({type}) => type),
    ["car", "tank", "helicopter", "airplane"],
  );
  assert.equal(new Set(TACTICAL_PALETTE_MARKERS.map(({type}) => type)).size, 26);
  assert.equal(tacticalMarker("helicopter")?.group, "Units");
});

test("core speech and PTT report markers have visible manual equivalents", () => {
  for (const type of [
    "evac-point", "casualty", "contact", "fire-smoke", "drone-spotted",
    "road-blocked", "radio-dead-zone", "supply-cache", "water-point",
  ]) {
    const marker = tacticalMarker(type);
    assert.equal(marker?.type, type);
    assert.ok(marker.label);
    assert.ok(marker.symbol);
    assert.match(marker.color, /^#[0-9a-f]{6}$/i);
  }
  assert.equal(tacticalMarker("submarine"), null);
});
