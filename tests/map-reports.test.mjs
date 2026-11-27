import test from "node:test";
import assert from "node:assert/strict";
import {reportDraft, reportComposerHtml} from "../src/retium/static/map-reports.js";
import {reportProperties} from "../src/retium/static/marker-catalog.js";

test("report offers urgency and submits without a status", () => {
  assert.doesNotMatch(reportComposerHtml(), /<select|Unconfirmed|Confirmed/i);
  assert.deepEqual(reportDraft("road-blocked", {lat: 51, lon: 4}, "Fallen tree", true), {
    type: "marker.created", marker_type: "road-blocked", label: "Road blocked", lat: 51, lon: 4,
    description: "Fallen tree", urgent: true,
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
test("old confirmed and unconfirmed reports render like new reports", () => {
  const event = {...reportDraft("road-blocked", {lat: 0, lon: 0}, "Tree", true), created_at: 100};
  const expected = reportProperties(event);
  for (const status of ["reported", "confirmed"]) {
    assert.deepEqual(reportProperties({...event, report_status: status, report_view: {status}}), expected);
  }
});
test("cleared report is muted, no longer urgent, without changing the original event", () => {
  const event = {...reportDraft("electrical-hazard", {lat: 0, lon: 0}, "Power line", true),
    created_at: 100, report_status: "reported", report_view: {status: "cleared", updated_by: "ALPHA", updated_at: 200}};
  const props = reportProperties(event);
  assert.equal(props.isMapReport, true);
  assert.equal(props.label, "Electrical hazard · cleared");
  assert.equal(props.reportUrgent, false);
  assert.equal(props.symbol, "✓");
  assert.equal(event.report_status, "reported");
  assert.equal(event.urgent, true);
  assert.deepEqual(reportProperties({type: "position.updated"}), {});
});
