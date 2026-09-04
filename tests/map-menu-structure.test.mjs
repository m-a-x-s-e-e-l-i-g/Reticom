import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const html = readFileSync(new URL("../src/retium/static/index.html", import.meta.url), "utf8");

function between(start, end) {
  const startIndex = html.indexOf(start);
  const endIndex = html.indexOf(end, startIndex);
  assert.notEqual(startIndex, -1, `missing ${start}`);
  assert.notEqual(endIndex, -1, `missing ${end}`);
  return html.slice(startIndex, endIndex);
}

test("Field and Command expose Tactical and keep Draw focused", () => {
  const menus = [
    between('id="commandMapRadialMenu"', 'id="commandMapContextMenu"'),
    between('id="mapRadialMenu"', 'id="mapContextMenu"'),
  ];

  for (const menu of menus) {
    assert.match(menu, /data-map-marker-palette/);
    assert.match(menu, /data-map-radial-page="draw"/);
    assert.match(menu, /<span>DRAW<\/span>/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:car|tank|helicopter|airplane)"/);
    assert.doesNotMatch(menu, /<span>TOOLS<\/span>/);
  }
});

test("desktop context menus expose the shared Tactical palette", () => {
  const menus = [
    between('id="commandMapContextMenu"', 'id="commandMapMarkerPalette"'),
    between('id="mapContextMenu"', 'id="mapMarkerPalette"'),
  ];

  for (const menu of menus) {
    assert.match(menu, /data-map-marker-palette/);
    assert.match(menu, /TACTICAL MARKERS/);
    assert.doesNotMatch(menu, /map-context-symbols/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:car|tank|helicopter|airplane)"/);
  }
});
