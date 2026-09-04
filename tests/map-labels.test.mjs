import assert from "node:assert/strict";
import test from "node:test";

import {nextWaypointLabel, withDisplayWaypointLabels} from "../src/retium/static/map-labels.js";

function waypoint(label) {
  return {id: crypto.randomUUID(), type: "marker.created", marker_type: "waypoint", label};
}

test("starts waypoint numbering at one", () => {
  assert.equal(nextWaypointLabel([]), "Waypoint 1");
});

test("uses the next number after verified waypoint labels", () => {
  assert.equal(
    nextWaypointLabel([
      waypoint("Waypoint 2"),
      waypoint("WAYPOINT 7"),
      {type: "marker.created", marker_type: "warning", label: "Warning"},
    ]),
    "Waypoint 8",
  );
});

test("counts legacy generic waypoints when choosing a suffix", () => {
  assert.equal(
    nextWaypointLabel([waypoint("Waypoint"), waypoint("Waypoint")]),
    "Waypoint 3",
  );
});

test("gives legacy generic waypoints stable visible numbers", () => {
  const older = {...waypoint("Waypoint"), created_at: 100};
  const newer = {...waypoint("Waypoint"), created_at: 200};
  const events = withDisplayWaypointLabels([newer, older]);

  assert.equal(events.find((event) => event.id === older.id).display_label, "Waypoint 1");
  assert.equal(events.find((event) => event.id === newer.id).display_label, "Waypoint 2");
});
