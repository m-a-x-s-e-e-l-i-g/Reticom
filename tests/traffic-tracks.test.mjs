import test from "node:test";
import assert from "node:assert/strict";
import {TrafficTracks, TRACK_SOURCE, TRACK_POINTS, TRACK_TARGETS} from "../src/retium/static/traffic-tracks.js";
import {TrafficMotion} from "../src/retium/static/traffic-motion.js";
import {createIntelPacks} from "../src/retium/static/intel-packs.js";

const fix = (time, point=[0,0], pack="flights", id="one") => ({type:"Feature", id, geometry:{type:"Point",coordinates:point}, properties:{identity:id, title:id, traffic_pack:pack, position_time:time, heading:90, course:90, speed_knots:100}});
const data = (...features) => ({type:"FeatureCollection",features});

for (const pack of ["flights", "vessels"]) test(`${pack}: stores real fixes without cached duplicates or older rewinds`, () => {
  const model = new TrafficTracks(pack), first = data(fix(1000,[0,0],pack));
  model.ingest(first,1000); model.ingest(first,1010);
  assert.equal(model.route("one",1010).count,1);
  assert.equal(model.route("one",1010).data.features.length,0);
  model.ingest(data(fix(1010,[.001,0],pack)),1010);
  model.ingest(data(fix(1005,[1,0],pack)),1010);
  const route=model.route("one",1010);
  assert.equal(route.count,2);
  assert.deepEqual(route.data.features[0].geometry.coordinates,[[[0,0],[.001,0]]]);
  assert.match(route.label,/recent observed/);
  assert.deepEqual(first.features[0].geometry.coordinates,[0,0]);
});

test("animation predictions never become observed route points", () => {
  const model=new TrafficTracks("flights"), source=data(fix(1000));
  model.ingest(source,1000);
  model.ingest(new TrafficMotion("flights").sample(source,1010).data,1010);
  assert.equal(model.route("one",1010).count,1);
});

test("invalid, expired, future and wrong-pack reports are ignored", () => {
  const model=new TrafficTracks("flights");
  for (const report of [fix(1),fix(2000),fix(1000,[NaN,0]),fix(1000,[181,0]),fix(1000,[0,91]),fix(1000,[0,0],"vessels")]) model.ingest(data(report),1000);
  assert.equal(model.tracks.size,0);
});

test("lost reception and impossible jumps split tracks instead of connecting them", () => {
  const model=new TrafficTracks("flights");
  for(const [time,point] of [[1000,[0,0]],[1010,[.01,0]],[1200,[.02,0]],[1210,[.03,0]],[1220,[80,0]],[1230,[80.01,0]]]) model.ingest(data(fix(time,point)),time);
  assert.equal(model.route("one",1230).data.features[0].geometry.coordinates.length,3);
});

test("date-line crossing stays short in both directions", () => {
  for(const sign of [1,-1]) {
    const model=new TrafficTracks("flights");
    model.ingest(data(fix(1000,[sign*179.99,0])),1000);
    model.ingest(data(fix(1010,[-sign*179.99,.01])),1010);
    const lines=model.route("one",1010).data.features[0].geometry.coordinates;
    assert.equal(lines.length,2);
    for(const line of lines) assert.ok(Math.abs(line[0][0]-line[1][0])<.02);
    assert.equal(lines[0][1][0],sign*180);
    assert.equal(lines[1][0][0],-sign*180);
  }
});

test("history is time-, point- and target-bounded and can be cleared", () => {
  const model=new TrafficTracks("flights");
  for(let i=0;i<TRACK_POINTS+10;i++) model.ingest(data(fix(1000+i,[i*.0001,0])),1000+i);
  assert.equal(model.route("one",1100).count,TRACK_POINTS);
  for(let i=0;i<TRACK_TARGETS+10;i++) model.ingest(data(fix(1100,[0,0],"flights",String(i))),1100);
  assert.equal(model.tracks.size,TRACK_TARGETS);
  assert.equal(model.route("one",1100).count,0);
  model.prune(2001); assert.equal(model.tracks.size,0);
  model.ingest(data(fix(2001)),2001); model.clear(); assert.equal(model.tracks.size,0);
});

test("click selection, new reports, popup close, style changes and filters manage one dashed track without camera movement", async () => {
  const originalNow=Date.now; let now=1000, report=fix(now), popup, clicks=[], fetches=0;
  Date.now=()=>now*1000;
  const sources=new Map(),layers=new Map(),events=new Map();
  const map={getStyle:()=>({layers:[...layers.values()]}),getZoom:()=>12,getBounds:()=>({west:-1,south:-1,east:1,north:1}),
    getSource:id=>sources.get(id),addSource:(id,value)=>sources.set(id,{...value,setData(data){this.data=data;}}),removeSource:id=>sources.delete(id),
    getLayer:id=>layers.get(id),addLayer:layer=>layers.set(layer.id,layer),removeLayer:id=>layers.delete(id),
    on:(name,fn)=>events.set(name,fn),off:name=>events.delete(name),queryRenderedFeatures:()=>clicks,
    jumpTo:()=>assert.fail("Keep camera"),fitBounds:()=>assert.fail("Keep zoom")};
  class Popup {
    constructor(){popup=this;this.events=new Map();}
    setLngLat(){return this;} setHTML(html){this.html=html;return this;} addTo(){return this;}
    on(name,fn){this.events.set(name,fn);return this;} remove(){this.events.get("close")?.();}
  }
  let settings={enabled:["flights","vessels"]}, controller;
  const wait=()=>new Promise(resolve=>setTimeout(resolve,20));
  const click=(pack="flights",id="one")=>{
    clicks=[{...report,id:42,properties:{...report.properties,identity:id},layer:{id:`public-intel-${pack}-icons`}}];
    events.get("click")({point:{x:1,y:1},lngLat:{lng:0,lat:0}});
  };
  try {
    controller=createIntelPacks({fetchImpl:async(url,options={})=>{
      fetches++;
      if(options.method==="PATCH") settings={...settings,...JSON.parse(options.body)};
      return {ok:true,json:async()=>url.includes("/live/") ? data({...report,properties:{...report.properties,traffic_pack:url.includes("vessels")?"vessels":"flights"}})
        : {settings,packs:["flights","vessels"].map(id=>({id,configured:true,enabled:settings.enabled.includes(id)}))}};
    }});
    await controller.ready;controller.attachMap(map,{Popup});await wait();
    click();assert.match(popup.html,/Waiting for consecutive/);
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,0);
    now=1010;report=fix(now,[.001,0]);await controller.refresh();await wait();
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,1);
    assert.deepEqual(layers.get(TRACK_SOURCE).paint["line-dasharray"],[3,2]);
    assert.match(popup.html,/2 fixes/);
    const count=fetches;click("vessels");
    assert.equal(sources.get(TRACK_SOURCE).data.features[0].properties.traffic_pack,"vessels");
    assert.equal(fetches,count,"Selecting a track does not call providers");
    sources.clear();layers.clear();events.get("style.load")();await wait();
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,1);
    popup.remove();assert.equal(sources.has(TRACK_SOURCE),false);
    click();clicks=[];events.get("click")({point:{x:9,y:9}});assert.equal(sources.has(TRACK_SOURCE),false);
    click();await controller.setTrafficFilters("flights",{query:"not-present"});await wait();assert.equal(sources.has(TRACK_SOURCE),false);
    click("vessels");await controller.setEnabled("vessels",false);await wait();assert.equal(sources.has(TRACK_SOURCE),false);
    await controller.setTrafficFilters("flights",{});await wait();click();
    now=1300;await controller.refresh();await wait();assert.equal(sources.has(TRACK_SOURCE),false,"Expired target clears selection");
    now=1310;report=fix(now);await controller.refresh();await wait();click();
    events.get("remove")();assert.equal(sources.has(TRACK_SOURCE),false);
  } finally {controller?.destroy();Date.now=originalNow;}
});
