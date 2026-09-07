import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const html = readFileSync(new URL("../src/retium/static/index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/retium/static/styles.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");

test("PTT returns after review is saved or discarded", () => {
  assert.match(css, /\.map-draw-controls\.is-reviewing:not\(\.hidden\) ~ \.map-actions \.ptt-button/);
  const stop = app.slice(app.indexOf("function stopMapDrawing"), app.indexOf("function addMapDrawPoint"));
  assert.match(stop, /classList\.remove\("is-reviewing"\)/);
});

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
    assert.equal((menu.match(/data-map-draw="area"/g) || []).length, 1);
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
    assert.match(menu, /REPORT/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:warning|observation|obstacle)"/);
    assert.match(menu, /data-quick-marker="note"/);
    assert.equal((menu.match(/data-map-draw="area"/g) || []).length, 1);
    assert.doesNotMatch(menu, /map-context-symbols/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:car|tank|helicopter|airplane)"/);
  }
});
