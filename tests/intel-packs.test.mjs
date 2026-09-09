import test from "node:test";
import assert from "node:assert/strict";

test("intel credit strip stays hidden without hiding base-map attribution or source links", () => {
  const css = readFileSync(new URL("../src/retium/static/intel-packs.css", import.meta.url), "utf8");
  assert.match(css, /\.intel-map-credit\s*\{\s*display:\s*none\s*!important;\s*\}/);
  assert.doesNotMatch(css, /\.(?:overview-map-credit|field-map-credit|maplibregl-ctrl-attrib)\s*\{[^}]*display:\s*none/);
  const html = intelFeatureHtml({properties: {title: "Public feature"}}, {id: "military", source_url: "https://www.openstreetmap.org/copyright", attribution: "© OpenStreetMap contributors"}, {});
  assert.match(html, /https:\/\/www.openstreetmap.org\/copyright/);
  assert.match(html, /© OpenStreetMap contributors/);
});
import {readFileSync} from "node:fs";
import {intelNoteText} from "../src/retium/static/intel-packs.js";
import {intelLayerWarning} from "../src/retium/static/intel-packs.js";

test("layer picker suppresses routine refresh information but retains actionable failures", () => {
  for (const id of ["flights", "vessels", "military", "gdacs", "elevation", "firms"]) {
    const pack = {id, enabled: true};
    assert.equal(intelLayerWarning(pack), "");
    for (const status of ["loading", "waiting", "ready", "cached", "zoom_in"]) {
      for (const count of [0, 20]) assert.equal(intelLayerWarning(pack, {status, features: Array(count), note: "Routine update"}), "");
    }
    assert.equal(intelLayerWarning(pack, {status: "error", error: "API limit reached"}), "API limit reached");
    assert.match(intelLayerWarning(pack, {status: "stale"}), /saved data/);
    assert.match(intelLayerWarning({...pack, configured: false}), /Source access/);
    assert.equal(intelLayerWarning({...pack, enabled: false}, {status: "error"}), "");
  }
});

test("contour interval appears once while distinct warnings remain visible", () => {
  const contour = {id: "elevation", enabled: true};
  for (const note of ["50 m contour interval · labels in metres", "100 m contour interval · labels in metres"]) {
    assert.equal(intelStatusText(contour, {status: "ready", note}), note);
    assert.equal(intelNoteText(contour, {status: "ready", note}), "");
    assert.equal(intelNoteText(contour, {status: "stale", note, error: "Provider offline"}), "Provider offline");
  }
  assert.equal(intelNoteText(contour, {status: "zoom_in", note: "Zoom in to see contour lines"}), "");
  assert.equal(intelNoteText({...contour, enabled: false}, {note: "Old status"}), "");
  assert.equal(intelNoteText({id: "military", enabled: true}, {status: "cached", note: "Partial coverage"}), "Partial coverage");
});
import {createIntelPacks, installIntelLayers, removeIntelLayers, intelViewportBoxes, intelPackUrls, mergeIntelResults, intelStatusText, intelFeatureHtml, replaceTrafficPopup} from "../src/retium/static/intel-packs.js";

const empty = {type: "FeatureCollection", features: []};
const feature = (id, title = id) => ({type: "Feature", id, properties: {title}, geometry: {type: "Point", coordinates: [4, 51]}});
const response = body => ({ok: true, json: async () => body});
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const pack = (id, enabled = true) => ({id, enabled, configured: true, min_zoom: 5, title: id, description: `${id} description`, source: "Public source", source_url: "https://example.org/info"});
const catalogue = (packs, credentials = {}) => ({packs, settings: {enabled: packs.filter(pack => pack.enabled).map(pack => pack.id)}, credentials});
const contourUrl = "reticom-contours-contour://{z}/{x}/{y}?multiplier=1";
const prepareContours = async () => contourUrl;

test("partial saved OSM coverage is never labelled as an empty surveyed view", () => {
  const partial = {...empty, status: "stale", coverage_complete: false};
  assert.match(intelStatusText(pack("military"), partial), /incomplete coverage/);
  assert.doesNotMatch(intelStatusText(pack("military"), partial), /No public features in this view/);
  assert.equal(mergeIntelResults([{...empty, status: "cached", coverage_complete: true}, partial]).coverage_complete, false);
});

class FakeMap {
  constructor() {
    this.sources = new Map(); this.layers = new Map([["base-labels", {id: "base-labels", type: "symbol"}], ["verified-points", {id: "verified-points", source: "verified-events", type: "circle"}]]);
    this.events = new Map(); this.added = []; this.bounds = {west: 3.9, south: 50.9, east: 4.1, north: 51.1}; this.zoom = 12;
  }
  isStyleLoaded() { return true; }
  getStyle() { return {layers: [...this.layers.values()]}; }
  getSource(id) { return this.sources.get(id); }
  addSource(id, source) { this.sources.set(id, {...source, setData(value) { this.data = value; }}); }
  removeSource(id) { this.sources.delete(id); }
  getLayer(id) { return this.layers.get(id); }
  addLayer(layer, before) { this.layers.set(layer.id, layer); this.added.push({layer, before}); }
  removeLayer(id) { this.layers.delete(id); }
  on(event, fn) { if (!this.events.has(event)) this.events.set(event, new Set()); this.events.get(event).add(fn); }
  off(event, fn) { this.events.get(event)?.delete(fn); }
  emit(event, value = {}) { for (const fn of this.events.get(event) || []) fn(value); }
  getZoom() { return this.zoom; }
  getBounds() { return this.bounds; }
  queryRenderedFeatures() { return this.rendered || []; }
  fitBounds() { assert.fail("Public intel must not reframe the map"); }
  jumpTo() { assert.fail("Public intel must not move the map"); }
}

test("viewport queries wrap the world and split the antimeridian without oversized boxes", () => {
  assert.deepEqual(intelViewportBoxes({west: 179, south: 10, east: 181, north: 11}), [{west: 179, south: 10, east: 180, north: 11}, {west: -180, south: 10, east: -179, north: 11}]);
  assert.deepEqual(intelViewportBoxes({west: 539, south: 10, east: 541, north: 11}), intelViewportBoxes({west: 179, south: 10, east: 181, north: 11}));
  assert.deepEqual(intelViewportBoxes({west: 170, south: 10, east: -170, north: 11}), [{west: 170, south: 10, east: 180, north: 11}, {west: -180, south: 10, east: -170, north: 11}]);
  assert.deepEqual(intelViewportBoxes({west: -240, south: -90, east: 200, north: 90}), [{west: -180, south: -85, east: 180, north: 85}]);
  assert.deepEqual(intelViewportBoxes({west: 1, south: 50, east: 1, north: 51}), []);
  assert.deepEqual(intelViewportBoxes({west: NaN, south: 50, east: 1, north: 51}), []);
  assert.deepEqual(intelPackUrls("../../secret", {west: 0, south: 0, east: 1, north: 1}, 12), []);
  assert.match(intelPackUrls("military", {west: 0, south: 0, east: 1, north: 1}, 12)[0], /^\/api\/intel-packs\/military\?west=0\.00000&south=0\.00000&east=1\.00000&north=1\.00000&zoom=12\.00$/);
  assert.deepEqual(intelPackUrls("trails", {west: 0, south: 0, east: 1, north: 1}, 12), []);
});

test("merged antimeridian results deduplicate features and retain partial-source warnings", () => {
  const data = mergeIntelResults([{...empty, features: [feature("same"), feature("west")], status: "fresh", fetched_at: 1000}, {...empty, features: [feature("same"), feature("east")], status: "cached", fetched_at: 900}]);
  assert.equal(data.features.length, 3);
  assert.equal(data.fetched_at, 900);
  assert.equal(data.status, "fresh");
  const partial = mergeIntelResults([data, {...empty, status: "error", error: "Provider unavailable"}]);
  assert.equal(partial.status, "stale"); assert.equal(partial.error, "Provider unavailable");
  assert.equal(mergeIntelResults([{...empty, status: "needs_key"}]).status, "needs_key");
  assert.equal(mergeIntelResults([data, {...empty, status: "fresh", truncated: true}]).truncated, true);
  assert.equal(mergeIntelResults([data, {...empty, status: "fresh", capped: true}]).capped, true);
});

test("public feature popups escape provider text and reject unsafe source links", () => {
  const html = intelFeatureHtml({properties: {title: '<img src=x onerror="hack()">', detail: "<script>bad()</script>", source_url: "javascript:alert(1)", observed_at: 1234, confidence: "nominal"}}, pack("firms"), {status: "stale", fetched_at: 1234});
  assert.match(html, /&lt;img/); assert.match(html, /&lt;script/);
  assert.doesNotMatch(html, /<img|<script|javascript:/);
  assert.match(html, /not a confirmed fire/);
  assert.match(html, /Not a verified team report/);
  assert.match(html, /stale cached data/);
  const military = intelFeatureHtml(feature("base"), pack("military"), {});
  assert.match(military, /Boundaries and access may be incomplete/);
});

test("aircraft popup keeps the essential picture compact and protects its photo route", () => {
  const html = intelFeatureHtml({properties: {title: "FIX123", identity: "abcdef", registration: "PH-TEST", aircraft_registration: "PH-TEST", aircraft_manufacturer: "Airbus", aircraft_model: "A320-232", aircraft_type_code: "A320", aircraft_photo_url: "/api/intel-packs/live/flights/abcdef/thumbnail", altitude_m: 9144, speed_knots: 440, position_time: Date.now()/1000 - 8, source_url: "https://www.adsb.lol/"}}, pack("flights"), {});
  assert.match(html, /Airbus · A320-232/);
  assert.match(html, /intel-aircraft-photo/);
  assert.match(html, /Flight details/);
  assert.match(html, /9144 m/);
  const vessel = intelFeatureHtml({properties: {title: "MV EXAMPLE", vessel_type: "Cargo", speed_knots: 12}}, pack("vessels"), {});
  assert.match(vessel, /Vessel details/);
  assert.doesNotMatch(vessel, /Flight details/);
  const unsafe = intelFeatureHtml({properties: {title: "FIX", identity: "abcdef", aircraft_photo_url: "https://evil.example/image.jpg"}}, pack("flights"), {});
  assert.doesNotMatch(unsafe, /<img/);
  const css = readFileSync(new URL("../src/retium/static/intel-packs.css", import.meta.url), "utf8");
  assert.match(css, /\.intel-feature-popup\s*\{[^}]*max-block-size[^}]*overflow-y:\s*auto/s);
});

test("live traffic refresh keeps an expanded details disclosure open", () => {
  let details = {open: true};
  const popup = {
    getElement: () => ({querySelector: selector => selector === ".intel-traffic-details" ? details : null}),
    setHTML: () => { details = {open: false}; },
  };
  replaceTrafficPopup(popup, "updated traffic card");
  assert.equal(details.open, true);
});

test("status copy separates missing access, zoom, empty results and stale context", () => {
  assert.equal(intelStatusText({...pack("firms"), configured: false}, null), "Source access needed below");
  assert.match(intelStatusText(pack("trails"), {status: "zoom_in"}), /Zoom in/);
  assert.match(intelStatusText(pack("gdacs"), {...empty, status: "fresh"}), /No public features in this view/);
  assert.match(intelStatusText(pack("gdacs"), {features: [feature("one")], status: "stale", fetched_at: 1234}), /1 public feature · stale cache/);
  assert.equal(intelStatusText(pack("trails", false), {status: "error"}), "Off");
});

test("contour lines and metre labels remain below team data without changing camera", () => {
  const map = new FakeMap();
  installIntelLayers(map, pack("military"), {...empty, features: [feature("base")]});
  assert.equal(map.getSource("public-intel-military").data.features.length, 1);
  assert.equal(map.added.length, 4);
  assert.ok(map.added.every(item => item.before === "base-labels"));
  installIntelLayers(map, pack("military"), empty);
  assert.equal(map.added.length, 4); assert.equal(map.getSource("public-intel-military").data.features.length, 0);
  installIntelLayers(map, pack("elevation"), empty, contourUrl);
  const dem = map.getSource("public-intel-elevation");
  assert.equal(dem.type, "vector"); assert.equal(dem.minzoom, 9); assert.equal(dem.maxzoom, 15);
  assert.equal(dem.tiles[0], contourUrl);
  assert.equal(map.getLayer("public-intel-elevation-lines").type, "line");
  assert.deepEqual(map.getLayer("public-intel-elevation-labels").layout["text-field"], ["concat", ["to-string", ["get", "ele"]], " m"]);
  assert.ok(map.added.every(item => item.before === "base-labels"));
  assert.ok(map.added.every(item => item.layer.type !== "hillshade"));
  removeIntelLayers(map, "elevation");
  assert.equal(map.getSource("public-intel-elevation"), undefined);
  assert.equal(map.getLayer("public-intel-elevation-labels"), undefined);
  removeIntelLayers(map, "military");
  assert.equal(map.getSource("public-intel-military"), undefined);
  assert.equal(map.getLayer("public-intel-military-points"), undefined);
  assert.ok(map.getLayer("verified-points"));
});

test("explicitly disabled packs make no provider requests, while minimum zoom and missing keys are explicit", async () => {
  const map = new FakeMap(), requests = [];
  const controller = createIntelPacks({fetchImpl: async url => { requests.push(url); return response(url === "/api/intel-packs" ? catalogue([pack("trails", false), {...pack("firms"), configured: false}, {...pack("military"), min_zoom: 14}]) : {...empty,status:"zoom_in"}); }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(requests.length, 2, "A wide enabled view may read locally cached areas; disabled packs never load");
    assert.equal(controller.getState().results.firms.status, "needs_key");
    assert.equal(controller.getState().results.military.status, "zoom_in");
    assert.equal(map.getSource("public-intel-trails"), undefined);
    assert.equal(map.getSource("public-intel-firms"), undefined);
  } finally { controller.destroy(); }
});

test("terrain waits for persisted enable before MapLibre may request tiles", async () => {
  const map = new FakeMap(); let commit;
  const controller = createIntelPacks({prepareContours, fetchImpl: async (url, options) => {
    if (options?.method === "PATCH") return new Promise(resolve => { commit = resolve; });
    return response(catalogue([pack("elevation", false)]));
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    const pending = controller.setEnabled("elevation", true);
    await tick();
    assert.equal(map.getSource("public-intel-elevation"), undefined, "no premature tile requests against disabled endpoint");
    commit(response(catalogue([pack("elevation", true)])));
    await pending;
    await tick();
    assert.equal(map.getSource("public-intel-elevation").type, "vector");
  } finally { controller.destroy(); }
});

test("terrain refresh recreates failed contour sources without moving the camera", async () => {
  const map = new FakeMap();
  const controller = createIntelPacks({prepareContours, fetchImpl: async () => response(catalogue([pack("elevation")]))});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    const failed = map.getSource("public-intel-elevation");
    map.emit("error", {sourceId: "public-intel-elevation"});
    assert.equal(controller.getState().results.elevation.status, "error");
    await controller.refresh();
    assert.notEqual(map.getSource("public-intel-elevation"), failed);
    assert.equal(controller.getState().results.elevation, undefined);
  } finally { controller.destroy(); }
});

test("source tiles and live team updates do not block public data while isStyleLoaded is false", async () => {
  const map = new FakeMap();
  map.isStyleLoaded = () => false;
  const controller = createIntelPacks({fetchImpl: async url => response(url === "/api/intel-packs" ? catalogue([pack("gdacs")]) : {...empty, status: "fresh", features: [feature("loaded")]})});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(controller.getState().results.gdacs.status, "fresh");
    assert.equal(map.getSource("public-intel-gdacs").data.features[0].id, "loaded");
  } finally { controller.destroy(); }
});

test("a map attached before its style is parsed loads when style.load arrives, without repeated idle requests", async () => {
  const map = new FakeMap(), getStyle = map.getStyle.bind(map); let available = false, calls = 0;
  map.getStyle = () => available ? getStyle() : undefined;
  const controller = createIntelPacks({fetchImpl: async url => { calls++; return response(url === "/api/intel-packs" ? catalogue([pack("gdacs")]) : {...empty, status: "fresh", features: [feature("loaded")]}); }});
  try {
    await controller.ready; controller.attachMap(map); await tick(); assert.equal(calls, 1);
    available = true; map.emit("style.load"); await tick(); assert.equal(calls, 2);
    map.emit("idle"); await tick(); assert.equal(calls, 2);
    assert.equal(map.getSource("public-intel-gdacs").data.features[0].id, "loaded");
  } finally { controller.destroy(); }
});

test("disabling a pack immediately hides it and a late fetch cannot restore it", async () => {
  const map = new FakeMap(); let finish;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue([pack("military")]));
    if (options.method === "PATCH") return response(catalogue([pack("military", false)]));
    return new Promise(resolve => { finish = resolve; });
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.ok(finish); assert.ok(map.getSource("public-intel-military"));
    await controller.setEnabled("military", false);
    assert.equal(map.getSource("public-intel-military"), undefined);
    finish(response({...empty, status: "fresh", features: [feature("late")]})); await tick();
    assert.equal(map.getSource("public-intel-military"), undefined);
    assert.equal(controller.getState().packs[0].enabled, false);
  } finally { controller.destroy(); }
});

test("legacy enabled hiking intel pack is omitted and never requests duplicate trail data", async () => {
  const requests = [], map = new FakeMap();
  const controller = createIntelPacks({fetchImpl: async url => { requests.push(url); return response(catalogue([pack("trails")])); }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.deepEqual(controller.getState().packs, []);
    assert.deepEqual(requests, ["/api/intel-packs"]);
    assert.equal(map.getSource("public-intel-trails"), undefined);
  } finally { controller.destroy(); }
});

test("style replacement reattaches enabled data, stale viewport responses cannot win", async () => {
  const map = new FakeMap(); const pending = [];
  const controller = createIntelPacks({fetchImpl: async url => {
    if (url === "/api/intel-packs") return response(catalogue([pack("gdacs")]));
    return new Promise(resolve => pending.push(resolve));
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    map.bounds = {west: 5, south: 51, east: 6, north: 52};
    map.sources.clear(); for (const id of [...map.layers.keys()]) if (id.startsWith("public-intel-")) map.layers.delete(id);
    map.emit("style.load"); await tick();
    assert.equal(pending.length, 2);
    pending[1](response({...empty, status: "fresh", features: [feature("new-view")]})); await tick();
    pending[0](response({...empty, status: "fresh", features: [feature("old-view")]})); await tick();
    assert.equal(map.getSource("public-intel-gdacs").data.features[0].id, "new-view");
    assert.equal(controller.getState().results.gdacs.features[0].id, "new-view");
  } finally { controller.destroy(); }
});

test("aircraft remain visible while a moved viewport waits for its next refresh", async () => {
  const map = new FakeMap(), pending = [];
  const aircraft = feature("abc123", "KLM123");
  aircraft.properties = {title: "KLM123", identity: "abc123", traffic_pack: "flights", position_time: Date.now() / 1000, heading: 90, traffic_type: "medium", affiliation: "commercial"};
  const controller = createIntelPacks({fetchImpl: async url => {
    if (url === "/api/intel-packs") return response(catalogue([pack("flights")]));
    return new Promise(resolve => pending.push(resolve));
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    assert.equal(pending.length, 1);
    pending.shift()(response({...empty, status: "fresh", traffic: true, features: [aircraft]})); await tick();
    assert.equal(map.getSource("public-intel-flights").data.features.length, 1);
    map.bounds = {west: 4, south: 50.95, east: 4.2, north: 51.15};
    map.emit("style.load"); await tick();
    assert.equal(pending.length, 1);
    pending.shift()(response({...empty, status: "waiting", traffic: true, features: [], note: "Map moved · waiting"})); await tick();
    assert.equal(map.getSource("public-intel-flights").data.features.length, 1);
    assert.equal(controller.getState().results.flights.status, "stale");
  } finally { controller.destroy(); }
});

test("military markers and polygons survive zoom limits and returning while a refresh is pending", async () => {
  const map = new FakeMap(), pending=[];
  const polygon={...feature("area"),geometry:{type:"Polygon",coordinates:[[[3.95,50.95],[4.05,50.95],[4.05,51.05],[3.95,51.05],[3.95,50.95]]]}};
  const original=[feature("marker"),polygon];
  const controller=createIntelPacks({fetchImpl:async url=>{
    if(url==="/api/intel-packs") return response(catalogue([{...pack("military"),min_zoom:9}]));
    return new Promise(resolve=>pending.push(resolve));
  }});
  const shown=()=>map.getSource("public-intel-military").data.features;
  try {
    await controller.ready; controller.attachMap(map); await tick();
    pending.shift()(response({...empty,status:"fresh",features:original})); await tick();
    map.zoom=3; map.bounds={west:-10,south:40,east:20,north:60};
    await controller.refresh(); await tick();
    assert.deepEqual(shown(),original,"Already loaded geometry stays visible during the wide request");
    pending.shift()(response({...empty,status:"zoom_in"})); await tick();
    assert.deepEqual(shown(),original,"A zoom restriction cannot erase cached geometry");
    map.zoom=12; map.bounds={west:3.9,south:50.9,east:4.1,north:51.1};
    await controller.refresh(); await tick();
    assert.deepEqual(shown(),original,"Zooming back needs no provider response to restore the area");
    pending.shift()(response({...empty,status:"stale",coverage_complete:false})); await tick();
    assert.deepEqual(shown(),original);
    map.sources.clear(); map.layers.clear(); map.emit("style.load");
    assert.deepEqual(shown(),original,"Style recreation restores retained geometry synchronously");
  } finally {controller.destroy();}
});

test("wide traffic views reuse the last accepted region without repeatedly requesting impossible coverage", async () => {
  const map = new FakeMap(), calls=[];
  const aircraft=feature("abc123");
  aircraft.properties={identity:"abc123",traffic_pack:"flights",position_time:Date.now()/1000};
  const controller=createIntelPacks({fetchImpl:async url=>{
    if(url==="/api/intel-packs") return response(catalogue([pack("flights")]));
    calls.push(url);
    return response({...empty,status:url.includes("west=-100")?"zoom_in":"fresh",features:url.includes("west=-100")?[]:[aircraft]});
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    const regional=calls[0];
    map.bounds={west:-100,south:-60,east:100,north:60}; map.zoom=6;
    await controller.refresh(); await tick();
    assert.equal(calls.length,3,"One rejected wide view immediately falls back to the accepted region");
    assert.equal(calls[2],regional);
    await controller.refresh(); await tick();
    assert.equal(calls.length,4); assert.equal(calls[3],regional);
    assert.equal(map.getSource("public-intel-flights").data.features.length,1);
  } finally {controller.destroy();}
});

test("panning lets an in-flight traffic response populate the cache", async () => {
  const map=new FakeMap(), pending=[];
  const aircraft=feature("abc123");
  aircraft.properties={identity:"abc123",traffic_pack:"flights",position_time:Date.now()/1000};
  const controller=createIntelPacks({fetchImpl:async (url,options)=>{
    if(url==="/api/intel-packs") return response(catalogue([pack("flights")]));
    return new Promise((resolve,reject)=>{
      pending.push({resolve,signal:options.signal});
      options.signal.addEventListener("abort",()=>reject(Object.assign(new Error("Aborted"),{name:"AbortError"})),{once:true});
    });
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    map.bounds={west:4,south:51,east:5,north:52}; map.emit("moveend");
    assert.equal(pending[0].signal.aborted,false);
    pending[0].resolve(response({...empty,status:"fresh",features:[aircraft]})); await tick();
    assert.equal(map.getSource("public-intel-flights").data.features.length,1);
  } finally {controller.destroy();}
});

test("failed refresh keeps previous data visibly stale; failed setting saves roll back", async () => {
  const map = new FakeMap(); let fail = false;
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (url === "/api/intel-packs") return response(catalogue([pack("gdacs")]));
    if (options.method === "PATCH" || fail) throw new Error("Offline");
    return response({...empty, status: "fresh", features: [feature("cached")], fetched_at: 1234});
  }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    fail = true; map.emit("style.load"); await tick();
    assert.equal(controller.getState().results.gdacs.status, "stale");
    assert.equal(map.getSource("public-intel-gdacs").data.features[0].id, "cached");
    assert.equal(await controller.setEnabled("gdacs", false), false);
    assert.equal(controller.getState().packs[0].enabled, true);
    assert.ok(map.getSource("public-intel-gdacs"));
  } finally { controller.destroy(); }
});

test("public feature click never intercepts signed team features", async () => {
  const map = new FakeMap(), popups = [];
  class Popup { constructor() { popups.push(this); } setLngLat() { return this; } setHTML(html) { this.html = html; return this; } addTo() { return this; } remove() {} }
  const controller = createIntelPacks({fetchImpl: async url => response(url === "/api/intel-packs" ? catalogue([pack("gdacs")]) : empty)});
  try {
    await controller.ready; controller.attachMap(map, {Popup}); await tick();
    const publicFeature = {...feature("flood"), layer: {id: "public-intel-gdacs-points", type: "circle"}};
    map.rendered = [publicFeature, {layer: {id: "verified-points", source: "verified-events"}}];
    map.emit("click", {point: {}, lngLat: [4, 51]}); assert.equal(popups.length, 0);
    map.rendered = [publicFeature];
    map.emit("click", {point: {}, lngLat: [4, 51]}); assert.equal(popups.length, 1); assert.match(popups[0].html, /PUBLIC SOURCE/);
  } finally { controller.destroy(); }
});

test("UI keeps readable sources, blank password inputs and 44px mobile controls", () => {
  const js = readFileSync(new URL("../src/retium/static/intel-packs.js", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/retium/static/intel-packs.css", import.meta.url), "utf8");
  assert.match(js, /type=\\?"password\\?"/);
  assert.match(js, /Choices and access keys stay on this device/);
  assert.match(js, /Not a verified team report/);
  assert.match(js, /prefers-reduced-motion|setInterval/);
  assert.match(css, /\.intel-packs-head button[\s\S]*?place-items: center[\s\S]*?width: 44px; height: 44px/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});

test("provider key forms live in their own layer and use masked placeholders", () => {
  const js = readFileSync(new URL("../src/retium/static/intel-packs.js", import.meta.url), "utf8");
  assert.match(js, /data-pack-row="\$\{field.pack\}"/);
  assert.match(js, /data-intel-access-form="\$\{key\}"/);
  assert.match(js, /input.placeholder = configured \? catalogue.credential_previews/);
  assert.doesNotMatch(js, /<details class="intel-source-access">/);
});

test("military areas expose a durable current-view download control", () => {
  const source = readFileSync(new URL("../src/retium/static/intel-packs.js", import.meta.url), "utf8");
  assert.match(source, /data-intel-download/);
  assert.match(source, /SAVE THIS VIEW OFFLINE/);
  assert.match(source, /\/api\/intel-packs\/\$\{encodeURIComponent\(id\)\}\/download/);
});


test("FIRMS window changes persist and reject unsupported ranges", async () => {
  let days = 3;
  const changes = [];
  const controller = createIntelPacks({fetchImpl: async (url, options) => {
    if (options.method === "PATCH") { days = JSON.parse(options.body).firms_days; changes.push(days); }
    return response({...catalogue([pack("firms")]), settings: {enabled: ["firms"], firms_days: days}});
  }});
  try {
    await controller.ready;
    for (const value of [1, 7, 3]) {
      assert.equal(await controller.setFirmsDays(value), true);
      assert.equal(controller.getState().settings.firms_days, value);
    }
    assert.equal(await controller.setFirmsDays(2), false);
    assert.deepEqual(changes, [1, 7, 3]);
  } finally { controller.destroy(); }
});
