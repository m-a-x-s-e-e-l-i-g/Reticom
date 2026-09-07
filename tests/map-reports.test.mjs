import test from "node:test";
import assert from "node:assert/strict";
import {reportDraft} from "../src/retium/static/map-reports.js";
import {reportProperties} from "../src/retium/static/marker-catalog.js";

test("specific report has one pin with independent urgency and confidence", () => {
  assert.deepEqual(reportDraft("road-blocked", {lat: 51, lon: 4}, "Fallen tree", "reported", true), {
    type: "marker.created", marker_type: "road-blocked", label: "Road blocked", lat: 51, lon: 4,
    description: "Fallen tree", report_status: "reported", urgent: true,
  });
});
test("note and other require a description, with a readable map label", () => {
  for (const type of ["note", "other"]) {
    assert.throws(() => reportDraft(type, {lat: 0, lon: 0}, " "), /Describe/);
    assert.equal(reportDraft(type, {lat: 0, lon: 0}, "  Locked gate  ").label, "Locked gate");
  }
  assert.throws(() => reportDraft("warning", {}, "Danger"), /Choose/);
  assert.throws(() => reportDraft("flooding", {}, "x".repeat(161)), /160/);
});
test("cleared report is muted, no longer urgent, without changing the original event", () => {
  const event = {...reportDraft("electrical-hazard", {lat: 0, lon: 0}, "Power line", "reported", true),
    created_at: 100, report_view: {status: "cleared", updated_by: "ALPHA", updated_at: 200}};
  const props = reportProperties(event);
  assert.equal(props.reportStatus, "cleared");
  assert.equal(props.reportUrgent, false);
  assert.equal(props.symbol, "✓");
  assert.equal(event.report_status, "reported");
  assert.equal(event.urgent, true);
  assert.deepEqual(reportProperties({type: "position.updated"}), {});
});
