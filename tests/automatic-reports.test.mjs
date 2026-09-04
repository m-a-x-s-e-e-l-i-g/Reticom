import assert from "node:assert/strict";
import test from "node:test";

import {
  activeAutomaticReports,
  buildAutomaticReportFeatures,
  parseAutomaticReport,
} from "../src/retium/static/automatic-reports.js";

const reports = [
  ["Road blocked 200 metres east", "road-blocked", "road-block"],
  ["Casualty at my position", "casualty", "point"],
  ["Need evacuation here", "medevac", "point"],
  ["Rally point at the church", "rally-point", "point"],
  ["Checkpoint established here", "checkpoint", "point"],
  ["Landing zone clear, 300 metres south", "landing-zone", "point"],
  ["Vehicle disabled at my position", "vehicle-disabled", "point"],
  ["Unit moving northeast", "unit-moving", "heading"],
  ["Possible movement northwest", "possible-movement", "uncertainty"],
  ["Drone spotted west, 500 metres", "drone-spotted", "direction"],
  ["Fire or smoke southeast", "fire-smoke", "hazard-area"],
  ["Radio dead zone here", "radio-dead-zone", "warning-area"],
  ["Supply cache at this location", "supply-cache", "point"],
  ["Water available here", "water", "point"],
  ["Route Alpha compromised", "route-compromised", "route-warning"],
  ["Bridge damaged", "bridge-damaged", "point"],
  ["Search this area", "search-area", "search-area"],
  ["Last seen here ten minutes ago", "last-seen", "last-known"],
  ["Hold north of this road", "hold-line", "phase-line"],
  ["Contact north 100 meters", "contact", "contact"],
];

for (const [message, type, shape] of reports) {
  test(`parses automatic report: ${message}`, () => {
    const report = parseAutomaticReport(message);
    assert.equal(report?.type, type);
    assert.equal(report?.shape, shape);
    assert.equal(report?.action, "create");
  });
}

test("accepts compact tactical point calls", () => {
  const compact = [
    ["Evac", "evac-point"],
    ["Evac point", "evac-point"],
    ["Evacuation point", "evac-point"],
    ["Extraction point", "evac-point"],
    ["Pickup zone", "evac-point"],
    ["Medical point", "medical-point"],
    ["Aid station", "medical-point"],
    ["Checkpoint", "checkpoint"],
    ["Landing zone", "landing-zone"],
    ["LZ", "landing-zone"],
    ["Supply point", "supply-point"],
    ["Ammo point", "supply-point"],
    ["Water point", "water-point"],
  ];
  compact.forEach(([message, type]) => assert.equal(parseAutomaticReport(message)?.type, type, message));
  assert.equal(parseAutomaticReport("Checkpoint").state, "DESIGNATED");
  assert.equal(parseAutomaticReport("Checkpoint established here").state, "ESTABLISHED");
  assert.equal(parseAutomaticReport("LZ").state, "REPORTED");
  assert.equal(parseAutomaticReport("LZ clear").state, "CLEAR");
});

test("extracts direction, distance, route, landmark and relative time", () => {
  const road = parseAutomaticReport("Road blocked 2 kilometres east");
  assert.equal(road.bearing, 90);
  assert.equal(road.distance, 2000);

  const rally = parseAutomaticReport("Rally point at the church");
  assert.equal(rally.landmark, "CHURCH");
  assert.equal(rally.approximate, true);

  assert.equal(parseAutomaticReport("Route Alpha compromised").routeName, "ALPHA");
  assert.equal(parseAutomaticReport("Last seen here ten minutes ago").occurredAgoSeconds, 600);
});

test("rejects directional reports when no usable direction is present", () => {
  assert.equal(parseAutomaticReport("Contact maybe nearby"), null);
  assert.equal(parseAutomaticReport("Unit moving"), null);
  assert.equal(parseAutomaticReport("Drone spotted"), null);
});

test("builds the requested point, line, area and sector geometries", () => {
  const meta = {id: "report-1", callsign: "ALPHA", senderHash: "alpha", createdAt: 10_000, message: "report"};
  const origin = [4.3, 51.5];
  const blocked = buildAutomaticReportFeatures(parseAutomaticReport("Road blocked 200 metres east"), origin, meta);
  assert.deepEqual(blocked.map((feature) => feature.properties.kind), ["auto-line", "auto-point"]);
  assert.ok(blocked.at(-1).geometry.coordinates[0] > origin[0]);

  const uncertain = buildAutomaticReportFeatures(parseAutomaticReport("Possible movement northwest"), origin, meta);
  assert.equal(uncertain[0].geometry.type, "Polygon");
  assert.equal(uncertain.at(-1).properties.symbol, "?");

  const search = buildAutomaticReportFeatures(parseAutomaticReport("Search this area"), origin, meta);
  assert.deepEqual(search.map((feature) => feature.geometry.type), ["Polygon", "MultiLineString", "Point"]);

  const contact = buildAutomaticReportFeatures(parseAutomaticReport("Contact north 100 meters"), origin, meta);
  assert.deepEqual(contact.map((feature) => feature.properties.kind), ["route", "contact"]);
});

test("cancel last report only removes that speaker's newest automatic report", () => {
  const now = 20_000;
  const active = activeAutomaticReports([
    {id: "a1", senderHash: "alpha", callsign: "ALPHA", createdAt: now - 30, message: "Casualty at my position"},
    {id: "b1", senderHash: "bravo", callsign: "BRAVO", createdAt: now - 20, message: "Checkpoint established here"},
    {id: "a2", senderHash: "alpha", callsign: "ALPHA", createdAt: now - 10, message: "Road blocked 200 metres east"},
    {id: "cancel", senderHash: "alpha", callsign: "ALPHA", createdAt: now, message: "Cancel last contact"},
  ], now);

  assert.deepEqual(active.map((item) => item.id), ["a1", "b1"]);
});

test("expired tactical reports disappear automatically", () => {
  const now = 50_000;
  const active = activeAutomaticReports([
    {id: "old-move", senderHash: "alpha", createdAt: now - 21 * 60, message: "Unit moving northeast"},
    {id: "fresh-water", senderHash: "alpha", createdAt: now - 21 * 60, message: "Water available here"},
  ], now);
  assert.deepEqual(active.map((item) => item.id), ["fresh-water"]);
});

test("last known position retains the reported observation time", () => {
  const features = buildAutomaticReportFeatures(
    parseAutomaticReport("Last seen here ten minutes ago"),
    [4.3, 51.5],
    {id: "lkp", createdAt: 10_000, nowSeconds: 10_000, message: "Last seen here ten minutes ago"},
  );
  assert.equal(features.at(-1).properties.observedAt, 9_400);
  assert.ok(features.at(-1).properties.fadeOpacity < 0.78);
});

test("last-known expiry starts at the stated observation time", () => {
  const now = 50_000;
  const active = activeAutomaticReports([
    {id: "old-lkp", senderHash: "alpha", createdAt: now - 1, message: "Last seen here thirty minutes ago"},
  ], now);
  assert.equal(active.length, 0);
});
