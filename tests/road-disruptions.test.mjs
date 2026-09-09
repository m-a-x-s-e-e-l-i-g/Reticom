import test from "node:test";
import assert from "node:assert/strict";
import {roadDisplayData,retainRoads,roadLayers,filterRoadReports,normalizeRoadFilters} from "../src/retium/static/road-disruptions.js";
import {createIntelPacks,intelFeatureHtml,mergeIntelResults,intelLayerWarning} from "../src/retium/static/intel-packs.js";
const fix=(id="one",source="ndw",extra={})=>({type:"Feature",id,geometry:{type:"MultiLineString",coordinates:[[[4,51],[4.01,51.01]]]},properties:{road_id:id,road_source:source,road_impact:"closed",title:"Road",starts_at:900,ends_at:2000,stale_at:1100,expires_at:1500,...extra}});
const data=(features=[],extra={})=>({type:"FeatureCollection",features,status:"fresh",...extra});

test("vehicle obstructions start hidden and report/impact filters combine without losing cache",()=>{
  const all=data([fix("vehicle","ndw",{road_type:"vehicle_obstruction",road_impact:"incident"}),fix("closure","ndw",{road_type:"closures"})]);
  assert.deepEqual(filterRoadReports(all,undefined,1000).features.map(f=>f.id),["closure"]);
  assert.deepEqual(filterRoadReports(all,{types:["vehicle_obstruction"],impacts:["incident"]},1000).features.map(f=>f.id),["vehicle"]);
  assert.equal(filterRoadReports(all,{types:["vehicle_obstruction"],impacts:["closed"]},1000).features.length,0);
  assert.equal(filterRoadReports(all,{types:[],impacts:[]},1000).features.length,0);
  assert.equal(all.features.length,2);
  assert.equal(normalizeRoadFilters({types:[]}).types.length,0);
});

test("viewport merging preserves uncovered status and every authoritative road source",()=>{
  const uncovered=mergeIntelResults([data([],{status:"uncovered",note:"No connected road feed"})]);
  assert.equal(uncovered.status,"uncovered");
  assert.match(intelLayerWarning({id:"roads",enabled:true},uncovered),/No connected/);
  assert.deepEqual(mergeIntelResults([data([],{replaced_sources:["ndw"]}),data([],{replaced_sources:["ncdot"]})]).replaced_sources,["ndw","ncdot"]);
});

test("closures go stale and expire by source age or end time without another response",()=>{
  const source=data([fix()]);
  assert.equal(roadDisplayData(source,1099).features[0].properties.stale,false);
  assert.equal(roadDisplayData(source,1100).features[0].properties.stale,true);
  assert.equal(roadDisplayData(source,1500).features.length,0);
  assert.equal(roadDisplayData(data([fix("end","ndw",{ends_at:1000})]),1000).features.length,0);
  assert.equal(roadDisplayData(data([fix("future","ndw",{starts_at:1001})]),1000).features.length,0);
});

test("authoritative provider snapshots remove cleared closures across cached viewports",()=>{
  const previous=data([fix(),fix("other","ncdot")]);
  assert.deepEqual(retainRoads(previous,data([],{replaced_sources:["ndw"]}),1000).features.map(f=>f.id),["other"]);
  assert.equal(retainRoads(previous,data([],{status:"error"}),1000).features.length,2);
  assert.equal(retainRoads(previous,data([],{status:"error"}),1600).features.length,0);
});

test("road geometry is highlighted without inventing segments and popup text is escaped",()=>{
  const layers=roadLayers("roads");
  assert.equal(layers.find(l=>l.id==="roads-lines").type,"line");
  assert.deepEqual(layers.find(l=>l.id==="roads-points").filter,["==",["geometry-type"],"Point"]);
  const html=intelFeatureHtml(fix("one","ndw",{title:"<script>bad</script>",reason:"<b>flood</b>",stale:true,source_url:"javascript:alert(1)",updated_at:900}),{id:"roads"},{});
  assert.match(html,/STALE/);assert.match(html,/Last update/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|javascript:/);
  assert.match(html,/1970/); // Long-running works must show the year, not an apparently past end date.
});

test("roads expire and close their popup even when aircraft and ship layers are disabled",async()=>{
  let now=1000,clicked=[],popup;const originalNow=Date.now,originalInterval=globalThis.setInterval;
  Date.now=()=>now*1000;const intervals=new Map();globalThis.setInterval=(fn,ms)=>{intervals.set(ms,fn);return 0;};
  const sources=new Map(),layers=new Map(),events=new Map();
  const map={getStyle:()=>({layers:[...layers.values()]}),getZoom:()=>12,getBounds:()=>({west:4,south:51,east:5,north:52}),
    getSource:id=>sources.get(id),addSource:(id,s)=>sources.set(id,{...s,setData(data){this.data=data;}}),removeSource:id=>sources.delete(id),
    getLayer:id=>layers.get(id),addLayer:l=>layers.set(l.id,l),removeLayer:id=>layers.delete(id),
    on:(e,f)=>events.set(e,f),off:e=>events.delete(e),queryRenderedFeatures:()=>clicked,fitBounds:()=>assert.fail("Keep camera")};
  class Popup {constructor(){popup=this;this.events=new Map();}setLngLat(){return this;}setHTML(html){this.html=html;return this;}addTo(){return this;}on(e,f){this.events.set(e,f);return this;}remove(){this.closed=true;this.events.get("close")?.();}}
  let controller;
  try {
    controller=createIntelPacks({fetchImpl:async url=>({ok:true,json:async()=>url==="/api/intel-packs"?{settings:{enabled:["roads"]},packs:[{id:"roads",enabled:true,configured:true,min_zoom:0}]}:data([fix("one","ndw",{ends_at:1200})],{replaced_sources:["ndw"]})})});
    await controller.ready;controller.attachMap(map,{Popup});await new Promise(r=>setTimeout(r,20));
    assert.equal(sources.get("public-intel-roads").data.features.length,1);
    clicked=[{...fix(),id:42,layer:{id:"public-intel-roads-lines"}}];events.get("click")({point:{x:1,y:1},lngLat:{lng:4,lat:51}});
    now=1101;intervals.get(5000)();assert.match(popup.html,/STALE/);
    now=1200;intervals.get(5000)();assert.equal(sources.get("public-intel-roads").data.features.length,0);assert.ok(popup.closed);
  } finally {controller?.destroy();Date.now=originalNow;globalThis.setInterval=originalInterval;}
});
