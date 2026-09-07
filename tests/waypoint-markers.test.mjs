import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const app = fs.readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
const renderer = app.slice(app.indexOf("function syncWaypointMarkers("), app.indexOf("async function renderFieldMap("));

function harness() {
  const created = [], popups = [];
  const context = vm.createContext({
    waypointMarkersByMap: new WeakMap(), role: "gateway", currentTeamAdmin: false, localIdentityHash: "self",
    document: {createElement: () => ({append() {}, setAttribute(key, value) { this[key] = value; }, addEventListener(key, fn) { this[key] = fn; }})},
    mapMarkerClass: class {
      constructor({element}) { this.element = element; created.push(this); }
      setLngLat(point) { this.point = [...point]; return this; }
      addTo(map) { this.map = map; return this; }
      remove() { this.removed = true; }
    },
    mapPopupClass: class {},
    showMapFeaturePopup: (...args) => popups.push(args),
  });
  vm.runInContext(renderer, context);
  return {context, created, popups, sync: context.syncWaypointMarkers};
}
const waypoint = {id: "one", type: "marker.created", marker_type: "waypoint", label: "Waypoint 1", lon: 4, lat: 51, network: {sender_hash: "other"}};

test("Command and Field share waypoint diamonds, with independent map lifecycles", () => {
  const {sync, created, popups, context} = harness();
  const command = {}, field = {};
  sync(command, [waypoint]); sync(field, [waypoint]);
  assert.equal(created.length, 2);
  for (const marker of created) assert.equal(marker.element.className, "field-waypoint-marker");
  sync(command, [{...waypoint, display_label: "Rally Alpha", lon: 5}]);
  assert.equal(created.length, 2);
  created[0].element.click({stopPropagation() {}});
  assert.equal(popups[0][2].properties.label, "Rally Alpha");
  assert.equal(popups[0][2].properties.removable, true);
  assert.deepEqual([...popups[0][3]], [5, 51]);
  context.role = "field";
  created[1].element.click({stopPropagation() {}});
  assert.equal(popups[1][2].properties.removable, false);
  sync(command, []);
  assert.equal(created[0].removed, true);
  assert.equal(created[1].removed, undefined);
});

test("both maps invoke the shared renderer and exclude duplicate waypoint circles and labels", () => {
  assert.equal((app.match(/syncWaypointMarkers\(map, locations\);/g) || []).length, 2);
  for (const id of ["verified-points", "verified-labels", "field-points", "field-labels"]) {
    const start = app.indexOf(`id: "${id}"`);
    const layer = app.slice(start, app.indexOf("\n  });", start));
    assert.match(layer, /\["!=", \["get", "markerType"\], "waypoint"\]/);
  }
});
