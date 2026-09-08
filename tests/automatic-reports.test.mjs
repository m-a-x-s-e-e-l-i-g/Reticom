import assert from "node:assert/strict";
import test from "node:test";

import {
  activeAutomaticReports,
  automaticReportOrigin,
  buildAutomaticReportFeatures,
  parseAutomaticReport,
} from "../src/retium/static/automatic-reports.js";

const reports = [
  ["heat signature spotted 200 meters north of your position", "heat-signature", "point"],
  ["rally 100m north", "rally-point", "point"],
  ["rally at church", "rally-point", "point"],
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

test("all report types share compact, spaced, decimal and click distances", () => {
  for (const phrase of ["rally", "heat signature spotted", "checkpoint", "contact", "search this area", "casualty", "water point"]) {
    for (const [unit, distance] of [["100m", 100], ["100 m", 100], ["100 meters", 100], ["100 metres", 100], ["0.1km", 100], ["0.1 km", 100], ["0.1 clicks", 100], ["0.1 klicks", 100], ["one click", 1000], ["half a click", 500]]) {
      const report = parseAutomaticReport(`${phrase} ${unit} north`);
      assert.equal(report?.distance, distance, `${phrase} ${unit}`);
      assert.equal(report.bearing, 0);
      const point = buildAutomaticReportFeatures(report, [4, 50]).find(f => f.geometry.type === "Point");
      assert.ok(point.geometry.coordinates[1] > 50);
      assert.ok(Math.abs(point.geometry.coordinates[0] - 4) < 0.000001);
    }
  }
});

test("landmarks resolve to map coordinates, never silently to the reporter", () => {
  const report = parseAutomaticReport("rally at church");
  assert.equal(report.landmark, "CHURCH");
  assert.deepEqual(buildAutomaticReportFeatures(report, [4, 50]), []);
  const features = buildAutomaticReportFeatures(report, [4, 50], {landmarkLocation: {coordinates: [4.01, 50.01], name: "Nearest church"}});
  assert.deepEqual(features[0].geometry.coordinates, [4.01, 50.01]);
  assert.equal(features[0].properties.landmarkResolved, true);
  assert.equal(parseAutomaticReport("casualty at my position").landmark, "");
});

test("invalid distances cannot silently fall back to the sender or due north", () => {
  for (const message of ["rally 100m", "rally 99999m north", "rally 0m north", "rally 6 clicks north", "rally -100m north"]) {
    assert.equal(parseAutomaticReport(message), null, message);
  }
  assert.equal(parseAutomaticReport("I am moving north").distance, 180);
});

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
    {id: "cancel", senderHash: "alpha", callsign: "ALPHA", createdAt: now, message: "Cancel last report"},
  ], now);

  assert.deepEqual(active.map((item) => item.id), ["a1", "b1"]);
});

test("spoken enemy-contact cancellation targets only that speaker's latest contact", () => {
  for (const phrase of ["cancel last enemy contact", "Cancel my last enemy contact.", "cancel the last contact"]) {
    const items = [
      {id: "older", senderHash: "a", createdAt: 100, message: "contact north 100 meters"},
      {id: "latest", senderHash: "a", createdAt: 110, message: "contact east 200 meters"},
      {id: "water", senderHash: "a", createdAt: 120, message: "water point here"},
      {id: "other", senderHash: "b", createdAt: 125, message: "contact south 50 meters"},
      {id: "cancel", senderHash: "a", createdAt: 130, message: phrase},
    ];
    assert.deepEqual(activeAutomaticReports(items, 140).map(item => item.id), ["older", "water", "other"]);
    items[1].event = {automatic_report_dismissed: true};
    assert.deepEqual(activeAutomaticReports(items, 140).map(item => item.id), ["older", "water", "other"]);
    assert.equal(activeAutomaticReports(items.slice(0, -1), 140).some(item => item.id === "latest"), false);
  }
});

test("automatic point and arrow share the source ID and explicit removal permission", () => {
  const report = parseAutomaticReport("contact north 100 meters");
  for (const removable of [false, true]) {
    const features = buildAutomaticReportFeatures(report, [4, 51], {id: "source-message", removable});
    assert.equal(features.length, 2);
    for (const feature of features) {
      assert.equal(feature.properties.mapKind, "automatic-report");
      assert.equal(feature.properties.id, "source-message");
      assert.equal(feature.properties.removable, removable);
    }
  }
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


test("landmark commands without sender GPS use a recent fix from their own team", () => {
  const message = {created_at: 1000, network: {sender_hash: "command"}};
  const fix = {lat: 50, lon: 4, created_at: 876};
  const positions = new Map([["field", [fix]]]);
  const report = parseAutomaticReport("rally at church");
  assert.equal(automaticReportOrigin(report, message, positions), fix);
  assert.equal(automaticReportOrigin(parseAutomaticReport("rally 100m north"), message, positions), null);
  assert.equal(automaticReportOrigin(report, message, new Map()), undefined);
  assert.equal(automaticReportOrigin(report, {...message, created_at: 2000}, positions), undefined);
  const own = {...fix, created_at: 850};
  positions.set("command", [own]);
  assert.equal(automaticReportOrigin(report, message, positions), own);
});


test("speech does not fall back to regex markers while local AI is processing", () => {
  const reports = activeAutomaticReports([{id: "speech", createdAt: 1000, senderHash: "sender",
    message: "contact 100m north", event: {type: "ptt.broadcast"}}], 1001);
  assert.deepEqual(reports, []);
});
