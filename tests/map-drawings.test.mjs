import test from "node:test";
import assert from "node:assert/strict";
import {areaRing, areaLabelPoint, validDrawingArea, drawingFeatures, addDrawingDecorationLayers, drawingReviewHtml, DRAWING_COLORS} from "../src/retium/static/map-drawings.js";
import {allTeamsModel} from "../src/retium/static/all-teams-model.js";

const area = {id: "area-1", type: "drawing.created", drawing_type: "area", label: "Search Alpha",
  drawing_color: "blue", fill_style: "crosses", points: [[4,51], [4.1,51], [4.1,51.1], [4,51.1]], callsign: "Max", created_at: 1};

test("named areas produce a closed ring, chosen style and an interior label", () => {
  const features = drawingFeatures(area, {id: area.id, removable: true, color: "#111111"});
  assert.equal(features.length, 2);
  assert.equal(features[0].geometry.type, "Polygon");
  assert.deepEqual(features[0].geometry.coordinates[0].at(-1), [4,51]);
  assert.equal(features[0].properties.color, DRAWING_COLORS.blue);
  assert.equal(features[0].properties.fillPattern, "drawing-blue-crosses");
  assert.equal(features[1].properties.label, "Search Alpha");
  assert.equal(features[1].properties.removable, true);
  assert.equal(features[1].properties.id, area.id);
  assert.ok(Math.abs(features[1].geometry.coordinates[0] - 4.05) < .00001);
  assert.ok(Math.abs(features[1].geometry.coordinates[1] - 51.05) < .00001);
});

test("concave and dateline areas have local interior labels", () => {
  const concave = [[0,0],[3,0],[3,1],[1,1],[1,3],[0,3]];
  const [x,y] = areaLabelPoint(concave);
  assert.ok(x < 1 || y < 1);
  const crossing = [[179.9,0],[-179.9,0],[-179.9,1],[179.9,1]];
  const ring = areaRing(crossing);
  assert.ok(Math.max(...ring.map(p=>p[0])) - Math.min(...ring.map(p=>p[0])) < 1);
  assert.equal(areaLabelPoint(crossing)[0], 180);
});

test("unfinished and collapsed areas stay an outline instead of an invalid polygon", () => {
  assert.equal(areaLabelPoint([[0,0],[1,1]]), null);
  assert.equal(areaLabelPoint([[0,0],[1,1],[2,2]]), null);
  assert.equal(validDrawingArea([[0,0],[.000001,0],[0,.000001]]), false);
  assert.equal(validDrawingArea([[0,0],[1,1],[0,1],[1,0]]), false);
  assert.equal(drawingFeatures({...area, points: [[0,0],[1,1]]})[0].geometry.type, "LineString");
});

test("legacy traces and arrow geometry retain their appearance", () => {
  const trace = {points:[[1,2],[3,4]]};
  assert.equal(drawingFeatures(trace, {color:"#123456"})[0].properties.color, "#123456");
  assert.equal(drawingFeatures(trace)[0].geometry.type, "LineString");
  assert.equal(drawingFeatures({...trace,label:"Trace Bravo"})[1].properties.label, "Trace Bravo");
  const arrow = {type:"MultiLineString", coordinates:[trace.points]};
  assert.equal(drawingFeatures({...trace,drawing_type:"arrow"}, {}, arrow)[0].geometry, arrow);
});

test("all fill patterns install once and decorations are scoped to their source", () => {
  const images = new Map(), layers = [];
  const map = {hasImage:id=>images.has(id), addImage:(id,data)=>images.set(id,data), addLayer:layer=>layers.push(layer)};
  addDrawingDecorationLayers(map, "field-drawings");
  addDrawingDecorationLayers(map, "command-drawings");
  assert.equal(images.size, 12);
  assert.equal(layers.length, 6);
  assert.equal(images.get("drawing-blue-crosses").data.length, 24*24*4);
  assert.ok(images.get("drawing-blue-crosses").data.some((v,i)=>i%4===3 && v===0));
  assert.ok(layers.every(layer=>layer.source===layer.id.replace(/-(fill|pattern|names)$/, "")));
});

test("review has explicit save, discard, redraw, accessible names and all fills", () => {
  const html = drawingReviewHtml();
  for (const control of ["data-send-map-drawing disabled", "data-cancel-map-drawing", "data-clear-map-drawing", "data-drawing-name", 'value="solid"', 'value="lines"', 'value="crosses"', 'value="none"']) assert.ok(html.includes(control));
  for (const name of Object.keys(DRAWING_COLORS)) assert.ok(html.includes('aria-label="'+name+'"'));
});

test("combined-team map preserves drawing style and centered name", () => {
  const model = allTeamsModel([{id:"alpha",name:"Alpha",hosting:true,events:[area]}]);
  const features = model.features.features;
  assert.equal(features.length, 2);
  assert.equal(features[0].geometry.type, "Polygon");
  assert.equal(features[0].properties.fillStyle, "crosses");
  assert.equal(features[1].properties.drawingLabel, true);
  assert.equal(features[1].properties.label, "Search Alpha");
  assert.equal(features[1].properties.teamId, "alpha");
});
