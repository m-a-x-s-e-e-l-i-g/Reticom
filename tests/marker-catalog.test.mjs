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
  assert.equal(TACTICAL_MARKERS.length, 30);
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
    ["Threat / hazard", "Medical", "Control", "Operations", "Movement / support", "Units", "Notes / unknown"],
  );
  assert.deepEqual(
    groups.flatMap(({markers}) => markers.map(({type}) => type)).sort(),
    TACTICAL_PALETTE_MARKERS.map(({type}) => type).sort(),
  );
});

test("vehicle and aircraft markers live in the Tactical palette", () => {
  assert.deepEqual(
    TACTICAL_UNIT_MARKERS.map(({type}) => type),
    ["car", "tank", "helicopter", "airplane"],
  );
  assert.equal(new Set(TACTICAL_PALETTE_MARKERS.map(({type}) => type)).size, 34);
  assert.equal(tacticalMarker("helicopter")?.group, "Units");
});

test("core speech and PTT report markers have visible manual equivalents", () => {
  for (const type of [
    "command-post", "base-camp", "objective", "assembly-point",
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
