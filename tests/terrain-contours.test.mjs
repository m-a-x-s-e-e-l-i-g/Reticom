import test from "node:test";
import assert from "node:assert/strict";
import contours from "../src/retium/static/vendor/maplibre-contour/index.mjs";
import {contourInterval, contourLayers, CONTOUR_THRESHOLDS} from "../src/retium/static/terrain-contours.js";
import {createIntelPacks} from "../src/retium/static/intel-packs.js";

test("contour intervals adapt to zoom and remain in metres", () => {
  assert.equal(contourInterval(8.9), null);
  assert.equal(contourInterval(9), 200);
  assert.equal(contourInterval(12.4), 50);
  assert.equal(contourInterval(14), 10);
  assert.equal(contourInterval(15), 5);
  assert.equal(contourInterval(22), 5);
  const dem = new contours.DemSource({url: "https://example.org/{z}/{x}/{y}.png", maxzoom: 14, worker: false});
  const url = dem.contourProtocolUrl({thresholds: CONTOUR_THRESHOLDS, multiplier: 1});
  assert.match(url, /multiplier=1/);
  const [lines, labels, minorLabels] = contourLayers("elevation");
  assert.equal(lines["source-layer"], "contours");
  assert.equal(lines.paint["line-color"], "#000000");
  assert.equal(lines.paint["line-opacity"], 1);
  assert.equal(labels.layout["symbol-placement"], "line");
  assert.deepEqual(labels.filter, [">", ["get", "level"], 0]);
  assert.equal(labels.layout["text-allow-overlap"], false);
  assert.equal(minorLabels.minzoom, 14);
  assert.equal(minorLabels.layout["text-field"].at(-1), " m");
  assert.equal(minorLabels.layout["text-allow-overlap"], false);
});

test("actual contour engine decodes Terrarium heights including negative elevations", () => {
  const width = 16, input = new Uint8ClampedArray(width * width * 4);
  for (let y = 0; y < width; y++) for (let x = 0; x < width; x++) {
    const value = 32768 - 20 + x * 5;
    input.set([Math.floor(value / 256), value % 256, 0, 255], (y * width + x) * 4);
  }
  const dem = contours.decodeParsedImage(width, width, "terrarium", input);
  const tile = contours.HeightTile.fromRawDem(dem);
  assert.equal(tile.get(0, 0), -20);
  assert.equal(tile.get(8, 0), 20);
  const lines = contours.generateIsolines(10, tile, 4096, 0);
  assert.ok(lines[-10]?.length, "below sea level contour");
  assert.ok(lines[0]?.length, "sea level contour");
  assert.ok(lines[20]?.length, "20 metre contour");
  assert.ok(Object.keys(lines).every(height => Number(height) % 10 === 0));
  assert.deepEqual(contours.generateIsolines(10, new contours.HeightTile(16, 16, () => 23), 4096, 0), {});
});

const tick = () => new Promise(resolve => setTimeout(resolve, 20));
test("disabling during contour preparation cannot restore the layer", async () => {
  let resolveTiles, enabled = true;
  const layers = new Map(), sources = new Map();
  const map = {getStyle: () => ({layers: []}), getZoom: () => 14,
    getSource: id => sources.get(id), addSource: (id, value) => sources.set(id, value), removeSource: id => sources.delete(id),
    getLayer: id => layers.get(id), addLayer: layer => layers.set(layer.id, layer), removeLayer: id => layers.delete(id), on() {}, off() {}};
  const controller = createIntelPacks({prepareContours: () => new Promise(resolve => { resolveTiles = resolve; }),
    fetchImpl: async (_url, options) => {
      if (options.method === "PATCH") enabled = JSON.parse(options.body).enabled.includes("elevation");
      return {ok: true, json: async () => ({packs: [{id: "elevation", enabled}], settings: {enabled: enabled ? ["elevation"] : []}})};
    }});
  try {
    await controller.ready; controller.attachMap(map); await tick();
    await controller.setEnabled("elevation", false);
    resolveTiles("test-contours://{z}/{x}/{y}"); await tick();
    assert.equal(sources.size, 0); assert.equal(layers.size, 0);
  } finally { controller.destroy(); }
});
