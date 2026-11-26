import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {mapLocationInfo} from "../src/retium/static/map-location-info.js";
import mgrs from "../src/retium/static/vendor/geographiclib-mgrs/index.mjs";

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
    assert.match(menu, /data-map-location-info/);
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
    assert.match(menu, /data-map-location-info[^>]*role="menuitem"[^>]*>/);
    assert.match(menu, /REPORT/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:warning|observation|obstacle)"/);
    assert.match(menu, /data-quick-marker="note"/);
    assert.equal((menu.match(/data-map-draw="area"/g) || []).length, 1);
    assert.doesNotMatch(menu, /map-context-symbols/);
    assert.doesNotMatch(menu, /data-quick-marker="(?:car|tank|helicopter|airplane)"/);
  }
});

test("location info formats the clicked point for GPS, MGRS and Google Maps", () => {
  const point = mapLocationInfo({lat: -33.8569, lon: 511.2152}, mgrs);
  assert.equal(point.gps, "-33.856900, 151.215200");
  assert.equal(point.mgrs, mgrs.forward([151.2152, -33.8569], 5));
  assert.equal(point.mgrs.length, 15);
  const link = new URL(point.mapsUrl);
  assert.equal(link.origin + link.pathname, "https://www.google.com/maps/search/");
  assert.equal(link.searchParams.get("api"), "1");
  assert.equal(link.searchParams.get("query"), "-33.856900,151.215200");
  assert.equal(mapLocationInfo({lat: 0, lon: 0}).gps, "0.000000, 0.000000");
  assert.equal(mapLocationInfo({lat: 91, lon: 0}, mgrs), null);
});
