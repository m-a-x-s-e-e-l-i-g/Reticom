import test from "node:test";
import assert from "node:assert/strict";
import {GDACS_TYPES, gdacsCode, gdacsIconSvg, normalizeGdacsTypes, gdacsDisplayData, installGdacsIcons} from "../src/retium/static/gdacs-markers.js";
import {intelFeatureHtml, installIntelLayers, removeIntelLayers} from "../src/retium/static/intel-packs.js";

const feature = (id, code, geometry) => ({type: "Feature", id, properties: {disaster_code: code, severity: "orange"}, geometry});
const ring = [[3, 50], [5, 50], [5, 52], [3, 52], [3, 50]];
const hole = [[3.5, 50.5], [3.5, 51], [4, 51], [4, 50.5], [3.5, 50.5]];
const data = {type: "FeatureCollection", status: "fresh", features: [
  feature("fire-point", "WF", {type: "Point", coordinates: [4, 51]}),
  feature("fire-area", "WF", {type: "Polygon", coordinates: [ring, hole]}),
  feature("flood-areas", "FL", {type: "MultiPolygon", coordinates: [[ring, hole], [ring]]}),
  feature("cyclone-track", "TC", {type: "LineString", coordinates: [[4, 51], [6, 53]]}),
  feature("cyclone-tracks", "TC", {type: "MultiLineString", coordinates: [[[4, 51], [6, 53]], [[6, 53], [7, 52]]]}),
]};

test("GDACS maps codes and legacy public disaster names to seven distinct code-owned icons", () => {
  const codes = GDACS_TYPES.map(type => type.id);
  assert.equal(new Set(codes).size, 7);
  assert.equal(new Set(codes.map(gdacsIconSvg)).size, 7);
  assert.equal(gdacsCode({event_type: "wf"}), "WF");
  for (const [name, code] of [["Forest fire", "WF"], ["Wildfire", "WF"], ["Flood", "FL"], ["Earthquake", "EQ"], ["Tropical cyclone", "TC"], ["Drought", "DR"], ["Volcanic eruption", "VO"], ["Tsunami", "TS"]]) {
    assert.equal(gdacsCode({disaster_type: name}), code);
  }
  assert.equal(gdacsCode({event_type: "future-event"}), "other");
  assert.equal(gdacsCode({}), "other");
  for (const svg of codes.map(gdacsIconSvg)) assert.match(svg, /^<svg [\s\S]*aria-hidden="true"[\s\S]*<path d="[^\"]+"\/><\/svg>$/);
});

test("GDACS icons never interpolate upstream SVG, URLs, or unexpected event codes", () => {
  const hostile = '<script src="https://evil.invalid/a.js"></script><svg onload="evil()">';
  assert.equal(gdacsIconSvg(hostile), gdacsIconSvg("other"));
  assert.doesNotMatch(gdacsIconSvg(hostile), /script|onload|https:|evil/);
  const html = intelFeatureHtml({properties: {event_type: hostile, title: hostile, area_label: '<img src=x onerror="evil()">', disaster_type: "Forest fire", source_url: "javascript:evil()"}}, {id: "gdacs", title: "GDACS", source: "GDACS"}, {});
  assert.match(html, /Area \/ vector/);
  assert.match(html, /reference buffers/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<script|<img|<svg onload|href="javascript:/);
});

test("GDACS selection defaults to all and normalizes to the supported catalogue order", () => {
  const all = GDACS_TYPES.map(type => type.id);
  assert.deepEqual(normalizeGdacsTypes(undefined), all);
  assert.deepEqual(normalizeGdacsTypes([]), []);
  assert.deepEqual(normalizeGdacsTypes(["TC", "WF", "WF", "bogus", "wf"]), all.filter(code => ["WF", "TC"].includes(code)));
  assert.deepEqual(normalizeGdacsTypes(undefined, [{id: "FL"}, {id: "WF"}]), ["FL", "WF"]);
  assert.deepEqual(normalizeGdacsTypes(["EQ", "WF"], [{id: "FL"}, {id: "WF"}]), ["WF"]);
});

test("GDACS filtering removes every excluded geometry and preserves polygon holes and paths unchanged", () => {
  const original = structuredClone(data);
  const fires = gdacsDisplayData(data, ["WF"]);
  assert.deepEqual(fires.features.map(item => item.id), ["fire-point", "fire-area"]);
  assert.deepEqual(fires.features[1].geometry.coordinates, [ring, hole]);
  assert.equal(fires.status, "fresh");
  const flood = gdacsDisplayData(data, ["FL"]);
  assert.deepEqual(flood.features[0].geometry, data.features[2].geometry);
  const tracks = gdacsDisplayData(data, ["TC"]);
  assert.deepEqual(tracks.features.map(item => item.geometry), data.features.slice(3).map(item => item.geometry));
  assert.deepEqual(gdacsDisplayData(data, []).features, []);
  assert.deepEqual(data, original, "Filtering must not mutate cached source data");
});

test("legacy uncategorized cached points only remain visible for the unfiltered selection", () => {
  const old = {type: "FeatureCollection", features: [{type: "Feature", id: "legacy", properties: {title: "Old public event"}, geometry: {type: "Point", coordinates: [4, 51]}}]};
  assert.equal(gdacsDisplayData(old).features[0].properties.disaster_code, "other");
  assert.deepEqual(gdacsDisplayData(old, ["WF"]).features, []);
  assert.deepEqual(gdacsDisplayData(old, []).features, []);
  assert.deepEqual(gdacsDisplayData(undefined).features, []);
});

test("GDACS icon sprites are local, high-density, and only installed once per map style", () => {
  const originalDocument = globalThis.document, originalPath = globalThis.Path2D;
  const images = new Map(), drawn = [];
  globalThis.Path2D = class { constructor(path) { this.path = path; } };
  globalThis.document = {createElement(tag) {
    assert.equal(tag, "canvas");
    return {getContext: () => ({scale(x, y) { assert.equal(x, 2); assert.equal(y, 2); }, stroke(path) { drawn.push(path.path); }, getImageData: () => ({width: 48, height: 48})})};
  }};
  const map = {hasImage: id => images.has(id), addImage(id, image, options) { images.set(id, {image, options}); }};
  try {
    installGdacsIcons(map); installGdacsIcons(map);
    assert.equal(images.size, 8); assert.equal(drawn.length, 8);
    assert.equal(images.get("public-gdacs-WF").options.pixelRatio, 2);
    assert.equal(images.get("public-gdacs-WF").image.width, 48);
    images.clear(); installGdacsIcons(map);
    assert.equal(images.size, 8); assert.equal(drawn.length, 16);
  } finally {
    if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
    if (originalPath === undefined) delete globalThis.Path2D; else globalThis.Path2D = originalPath;
  }
});

test("GDACS layers distinguish polygons, vector paths and event icons without reframing the map", () => {
  const sources = new Map(), layers = new Map();
  const map = {
    getStyle: () => ({layers: [{id: "shared-navigation-lines", source: "shared-navigation", type: "line"}]}),
    getSource: id => sources.get(id), getLayer: id => layers.get(id),
    addSource(id, source) { sources.set(id, {...source, setData(value) { this.data = value; }}); },
    addLayer(layer, before) { assert.equal(before, "shared-navigation-lines"); layers.set(layer.id, layer); },
    removeLayer: id => layers.delete(id), removeSource: id => sources.delete(id),
    fitBounds() { assert.fail("Adding GDACS must not zoom the map"); },
    jumpTo() { assert.fail("Adding GDACS must not move the map"); },
  };
  installIntelLayers(map, {id: "gdacs"}, data);
  assert.equal(layers.size, 6);
  assert.deepEqual(layers.get("public-intel-gdacs-areas").filter, ["==", ["geometry-type"], "Polygon"]);
  assert.deepEqual(layers.get("public-intel-gdacs-lines").filter, ["==", ["geometry-type"], "Polygon"]);
  assert.deepEqual(layers.get("public-intel-gdacs-tracks").filter, ["==", ["geometry-type"], "LineString"]);
  assert.deepEqual(layers.get("public-intel-gdacs-tracks").paint["line-dasharray"], [3, 2]);
  assert.match(JSON.stringify(layers.get("public-intel-gdacs-icons").layout["icon-image"]), /public-gdacs-WF/);
  assert.deepEqual(sources.get("public-intel-gdacs").data.features[1].geometry.coordinates, [ring, hole]);
  removeIntelLayers(map, "gdacs");
  assert.equal(layers.size, 0); assert.equal(sources.size, 0);
});
