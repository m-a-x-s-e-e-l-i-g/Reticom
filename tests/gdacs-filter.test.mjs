import test from "node:test";
import assert from "node:assert/strict";
import {createIntelPacks} from "../src/retium/static/intel-packs.js";
import {GDACS_TYPES} from "../src/retium/static/gdacs-markers.js";

const all = GDACS_TYPES.map(type => type.id);
const response = body => ({ok: true, json: async () => body});
const tick = () => new Promise(resolve => setTimeout(resolve, 25));
const catalogue = (selected = all) => ({packs: [{id: "gdacs", title: "GDACS", enabled: true, configured: true, min_zoom: 0, disaster_types: GDACS_TYPES.map(({id, label}) => ({id, label}))}], settings: {enabled: ["gdacs"], gdacs_types: selected}, credentials: {}});
const point = (id, code) => ({type: "Feature", id, properties: {event_type: code, title: id}, geometry: {type: "Point", coordinates: [4, 51]}});
const fire = point("fire-point", "WF"), flood = point("flood-point", "FL");
const fireArea = {...point("fire-area", "WF"), geometry: {type: "Polygon", coordinates: [[[3, 50], [5, 50], [5, 52], [3, 50]]]}};
const floodArea = {...point("flood-area", "FL"), geometry: {type: "MultiPolygon", coordinates: [[[[3, 50], [5, 50], [5, 52], [3, 50]]]]}};
const data = {type: "FeatureCollection", status: "fresh", features: [fire, fireArea, flood, floodArea]};

class FakeMap {
  constructor() { this.sources = new Map(); this.layers = new Map(); this.events = new Map(); }
  getStyle() { return {layers: [...this.layers.values()]}; }
  getSource(id) { return this.sources.get(id); }
  addSource(id, source) { this.sources.set(id, {...source, setData(value) { this.data = value; }}); }
  removeSource(id) { this.sources.delete(id); }
  getLayer(id) { return this.layers.get(id); }
  addLayer(layer) { this.layers.set(layer.id, layer); }
  removeLayer(id) { this.layers.delete(id); }
  on(event, fn) { if (!this.events.has(event)) this.events.set(event, new Set()); this.events.get(event).add(fn); }
  off(event, fn) { this.events.get(event)?.delete(fn); }
  emit(event) { for (const fn of this.events.get(event) || []) fn({}); }
  getZoom() { return 12; }
  getBounds() { return {west: 3.9, south: 50.9, east: 4.1, north: 51.1}; }
  fitBounds() { assert.fail("Disaster filters must not zoom the map"); }
  jumpTo() { assert.fail("Disaster filters must not move the map"); }
}
const visible = map => map.getSource("public-intel-gdacs")?.data.features.map(item => item.id) || [];

test("saved disaster selection filters points and areas when the map first loads", async () => {
  const map = new FakeMap();
  const controller = createIntelPacks({fetchImpl: async url => response(url === "/api/intel-packs" ? catalogue(["WF"]) : data)});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.deepEqual(controller.getState().settings.gdacs_types, ["WF"]);
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
    assert.deepEqual(controller.getState().results.gdacs.features.map(item => item.id), ["fire-point", "fire-area"]);
  } finally { controller.destroy(); }
});

test("changing disaster filters hides excluded geometry immediately and saves only local preferences", async () => {
  const map = new FakeMap(), patches = []; let finishSave;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") { patches.push(JSON.parse(options.body)); return new Promise(resolve => { finishSave = resolve; }); }
    return response(data);
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(visible(map).length, 4);
    const pending = controller.setGdacsTypes(["WF"]);
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
    assert.deepEqual(patches, [{gdacs_types: ["WF"]}]);
    finishSave(response(catalogue(["WF"])));
    assert.equal(await pending, true); await tick();
    assert.deepEqual(controller.getState().settings.gdacs_types, ["WF"]);
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
  } finally { controller.destroy(); }
});

test("choosing no disasters clears the map and makes no GDACS feature requests", async () => {
  const map = new FakeMap(), requests = [];
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    requests.push({url, method: options.method});
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") return response(catalogue([]));
    return response(data);
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    const before = requests.filter(item => item.url.startsWith("/api/intel-packs/gdacs?")).length;
    assert.equal(await controller.setGdacsTypes([]), true); await tick();
    assert.deepEqual(visible(map), []);
    assert.deepEqual(controller.getState().settings.gdacs_types, []);
    assert.equal(requests.filter(item => item.url.startsWith("/api/intel-packs/gdacs?")).length, before);
    map.emit("style.load"); await tick();
    assert.deepEqual(visible(map), []);
    assert.equal(requests.filter(item => item.url.startsWith("/api/intel-packs/gdacs?")).length, before);
  } finally { controller.destroy(); }
});

test("persisted empty disaster selection avoids provider requests from initial map load", async () => {
  const map = new FakeMap(), requests = [];
  const controller = createIntelPacks({fetchImpl: async url => { requests.push(url); return response(catalogue([])); }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(requests.length, 1);
    assert.deepEqual(visible(map), []);
    assert.deepEqual(controller.getState().settings.gdacs_types, []);
  } finally { controller.destroy(); }
});

test("failed disaster preference writes restore both the selection and cached map geometry", async () => {
  const map = new FakeMap(); let failSave;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") return new Promise((resolve, reject) => { failSave = reject; });
    return response(data);
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    const pending = controller.setGdacsTypes(["WF"]);
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
    failSave(new Error("Offline"));
    assert.equal(await pending, false);
    assert.deepEqual(controller.getState().settings.gdacs_types, all);
    assert.deepEqual(visible(map), data.features.map(item => item.id));
  } finally { controller.destroy(); }
});

test("late in-flight responses cannot restore disaster types excluded while a request was pending", async () => {
  const map = new FakeMap(), loads = [];
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") return response(catalogue(["WF"]));
    return new Promise(resolve => loads.push(resolve));
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(loads.length, 1);
    await controller.setGdacsTypes(["WF"]); await tick();
    assert.equal(loads.length, 2);
    loads[1](response({...data, features: [fire, fireArea]})); await tick();
    loads[0](response(data)); await tick();
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
    assert.deepEqual(controller.getState().results.gdacs.features.map(item => item.id), ["fire-point", "fire-area"]);
  } finally { controller.destroy(); }
});

test("an empty selection survives a late response and stays empty after style replacement", async () => {
  const map = new FakeMap(); let finishLoad, featureRequests = 0;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") return response(catalogue([]));
    featureRequests++;
    return new Promise(resolve => { finishLoad = resolve; });
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    await controller.setGdacsTypes([]); await tick();
    finishLoad(response(data)); await tick();
    assert.deepEqual(visible(map), []);
    map.sources.clear(); map.layers.clear(); map.emit("style.load"); await tick();
    assert.deepEqual(visible(map), []);
    assert.equal(featureRequests, 1);
  } finally { controller.destroy(); }
});

test("a failed filtered refresh cannot put excluded cached areas back on the map", async () => {
  const map = new FakeMap(); let failRefresh = false;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue());
    if (options.method === "PATCH") { failRefresh = true; return response(catalogue(["WF"])); }
    if (failRefresh) throw new Error("Provider offline");
    return response(data);
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(await controller.setGdacsTypes(["WF"]), true); await tick();
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
    assert.equal(controller.getState().results.gdacs.status, "stale");
    assert.deepEqual(controller.getState().results.gdacs.features.map(item => item.id), ["fire-point", "fire-area"]);
    map.sources.clear(); map.layers.clear(); map.emit("style.load"); await tick();
    assert.deepEqual(visible(map), ["fire-point", "fire-area"]);
  } finally { controller.destroy(); }
});

test("disaster filters can be selected while the pack is off without enabling or fetching it", async () => {
  const map = new FakeMap(), requests = [];
  const disabledCatalogue = selection => { const value = catalogue(selection); value.packs[0].enabled = false; value.settings.enabled = []; return value; };
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    requests.push({url, options});
    return response(disabledCatalogue(options.method === "PATCH" ? ["WF"] : all));
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(await controller.setGdacsTypes(["WF"]), true); await tick();
    assert.equal(controller.getState().packs[0].enabled, false);
    assert.deepEqual(controller.getState().settings.gdacs_types, ["WF"]);
    assert.equal(map.getSource("public-intel-gdacs"), undefined);
    assert.deepEqual(requests.map(item => item.url), ["/api/intel-packs", "/api/intel-packs/settings"]);
    assert.deepEqual(JSON.parse(requests[1].options.body), {gdacs_types: ["WF"]});
  } finally { controller.destroy(); }
});
