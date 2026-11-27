import test from "node:test";
import assert from "node:assert/strict";
import {TrafficTracks, TRACK_SOURCE, TRACK_ESTIMATE_SOURCE, TRACK_POINTS, TRACK_TARGETS, trafficRouteBounds} from "../src/retium/static/traffic-tracks.js";
import {TrafficMotion} from "../src/retium/static/traffic-motion.js";
import {createIntelPacks} from "../src/retium/static/intel-packs.js";

const fix = (time, point=[0,0], pack="flights", id="one") => ({type:"Feature", id, geometry:{type:"Point",coordinates:point}, properties:{identity:id, title:id, traffic_pack:pack, position_time:time, heading:90, course:90, speed_knots:100}});
const data = (...features) => ({type:"FeatureCollection",features});

test("provider history extends beyond the browser window and dateline bounds stay narrow", () => {
  const tracks = new TrafficTracks("flights");
  const route = tracks.route("plane",10000,{note:"Available history",points:[
    {time:1000,point:[179.99,0]}, {time:1010,point:[-179.99,.01]},
    {time:1020,point:[999,1]}, {time:10001,point:[NaN,1]},
  ]});
  assert.equal(route.count,2);
  const bounds = trafficRouteBounds(route.data);
  assert.ok(bounds[1][0]-bounds[0][0]<1);
  assert.match(route.label,/Available history/);
});

test("a live duplicate preserves the provider's flight-leg break", () => {
  const tracks = new TrafficTracks("flights");
  tracks.ingest(data(fix(1020, [.002, 0])), 1020);
  const route = tracks.route("one", 1030, {points: [
    {time: 1000, point: [0, 0]}, {time: 1010, point: [.001, 0]},
    {time: 1020, point: [.002, 0], breakBefore: true}, {time: 1030, point: [.003, 0]},
  ]});
  assert.deepEqual(route.data.features[0].geometry.coordinates, [
    [[0, 0], [.001, 0]], [[.002, 0], [.003, 0]],
  ]);
});

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

test("a sampled circle is drawn with curved sections through the original fixes", () => {
  const model = new TrafficTracks("flights"), radius = .01;
  const points = Array.from({length: 9}, (_, index) => {
    const angle = index * Math.PI / 4;
    return [Math.cos(angle) * radius, Math.sin(angle) * radius];
  });
  const history = {points: points.map((point, index) => ({point, time: 1000 + index * 10}))};
  const route = model.route("plane", 1080, history);
  const [line] = route.data.features[0].geometry.coordinates;
  assert.equal(route.count, points.length);
  assert.ok(line.length > points.length);
  assert.deepEqual(line.filter(point => points.some(fix => fix[0] === point[0] && fix[1] === point[1])), points);
  assert.ok(line.every(([x, y]) => Math.abs(Math.hypot(x, y) - radius) < radius * .055));
});

test("rounding keeps date-line endpoints and the original disconnected legs", () => {
  const model = new TrafficTracks("vessels");
  const track = {points: [
    {time: 1000, point: [179.998, 0]}, {time: 1010, point: [179.999, .001]},
    {time: 1020, point: [-180, .001]}, {time: 1030, point: [-179.999, 0]},
    {time: 1040, point: [-179.997, 0], breakBefore: true}, {time: 1050, point: [-179.996, 0]},
  ]};
  const lines = model.route("vessel", 1050, track).data.features[0].geometry.coordinates;
  assert.equal(lines.length, 3);
  assert.deepEqual(lines[0].at(-1), [180, .001]);
  assert.deepEqual(lines[1][0], [-180, .001]);
  assert.deepEqual(lines[2], [[-179.997, 0], [-179.996, 0]]);
  for (const line of lines) assert.ok(line.every(([lon, lat]) => Number.isFinite(lat) && Math.abs(lon) <= 180));
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

test("traffic drawer follows the animated marker without reframing for route history", async () => {
  const originalNow=Date.now; let now=1000, report=fix(now), clicks=[], fetches=0, historyAvailable=false, pans=[], zoom=12, emptyFeed=false;
  Date.now=()=>now*1000;
  let frame;
  globalThis.requestAnimationFrame = fn => { frame = fn; return 1; };
  const element = () => ({dataset: {}, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }});
  const body = {...element(), querySelector: () => null};
  const follow = element(), centerButton = element(), trackButton = element(), close = element();
  const drawer = {...element(), querySelector: selector => ({".intel-traffic-drawer-body":body,"[data-traffic-follow]":follow,"[data-traffic-center]":centerButton,"[data-traffic-track]":trackButton,"[data-traffic-close]":close})[selector],
    setAttribute() {}, contains: () => false, remove() { this.removed = true; }};
  const container = {append() {}, ownerDocument: {createElement: tag => tag === "aside" ? drawer : {...element(), remove() {}}}};
  drawer.ownerDocument = container.ownerDocument;
  const sources=new Map(),layers=new Map(),events=new Map();
  let center = {lng: -.01, lat: 0};
  const map={getStyle:()=>({layers:[...layers.values()]}),getZoom:()=>zoom,getBounds:()=>({west:-1,south:-1,east:1,north:1}),getContainer:()=>container,getCenter:()=>center,
    project(point) { const x = point.lng ?? point[0]; return {x, dist: other => Math.abs(other.x - x)*100000}; },
    getSource:id=>sources.get(id),addSource:(id,value)=>sources.set(id,{...value,setData(data){this.data=data;}}),removeSource:id=>sources.delete(id),
    getLayer:id=>layers.get(id),addLayer:layer=>layers.set(layer.id,layer),removeLayer:id=>layers.delete(id),
    on:(name,fn)=>events.set(name,fn),off:name=>events.delete(name),queryRenderedFeatures:()=>clicks,
    jumpTo(options,event) { assert.deepEqual(Object.keys(options),["center"]); assert.equal(event.trafficFollow,true); pans.push(options.center);
      center = {lng:options.center[0],lat:options.center[1]}; events.get("moveend")(event); },
    fitBounds:()=>assert.fail("Never fit the route")};
  class Popup { constructor() { assert.fail("Traffic uses a drawer"); } }
  let settings={enabled:["flights","vessels"]}, controller;
  const wait=()=>new Promise(resolve=>setTimeout(resolve,20));
  const click=(pack="flights",id="one")=>{
    clicks=[{...report,id:42,properties:{...report.properties,identity:id},layer:{id:`public-intel-${pack}-icons`}}];
    events.get("click")({point:{x:1,y:1},lngLat:{lng:0,lat:0}});
  };
  try {
    controller=createIntelPacks({fetchImpl:async(url,options={})=>{
      fetches++;
      if (url.includes("/live/") && url.includes("?")) {
        assert.ok(Number(new URL(url,"http://localhost").searchParams.get("zoom")) >= 6,"Wide route view keeps a regional subscription");
        if (emptyFeed) return {ok:true,json:async()=>({...data(),status:"fresh"})};
      }
      if (url.endsWith("/route") && historyAvailable) return {ok:true,json:async()=>({note:"Provider history",points:[{time:now-300,point:[-.5,0]},{time:now-290,point:[-.49,0]}]})};
      if(options.method==="PATCH") settings={...settings,...JSON.parse(options.body)};
      return {ok:true,json:async()=>url.includes("/live/") ? data({...report,properties:{...report.properties,traffic_pack:url.includes("vessels")?"vessels":"flights"}})
        : {settings,packs:["flights","vessels"].map(id=>({id,configured:true,enabled:settings.enabled.includes(id)}))}};
    }});
    await controller.ready;controller.attachMap(map,{Popup});await wait();
    click();assert.equal(drawer.hidden,false);assert.match(body.innerHTML,/Waiting for consecutive/);
    assert.deepEqual(pans.at(-1),report.geometry.coordinates);assert.equal(zoom,12);
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,0);
    assert.equal(sources.has(TRACK_ESTIMATE_SOURCE),false);
    now=1010;report=fix(now,[.001,0]);await controller.refresh();await wait();
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,1);
    assert.deepEqual(layers.get(TRACK_SOURCE).paint["line-dasharray"],[3,2]);
    assert.equal(layers.get(TRACK_SOURCE).layout["line-cap"],"round");
    assert.match(body.innerHTML,/2 fixes/);
    now=1011;frame(1000);
    const aircraftPoint = sources.get("public-intel-flights").data.features[0].geometry.coordinates;
    assert.deepEqual(pans.at(-1), aircraftPoint);
    assert.deepEqual(sources.get(TRACK_ESTIMATE_SOURCE).data.features[0].geometry.coordinates, [[[.001,0], aircraftPoint]]);
    assert.deepEqual(sources.get(TRACK_SOURCE).data.features[0].geometry.coordinates, [[[0,0],[.001,0]]]);
    assert.deepEqual(layers.get(TRACK_ESTIMATE_SOURCE).paint["line-dasharray"],[1,2]);
    assert.notDeepEqual(pans.at(-1),[0,0]);
    events.get("dragstart")();
    assert.equal(follow.textContent,"Resume follow");
    const paused=pans.length;now=1012;frame(1100);
    assert.equal(pans.length,paused);
    assert.deepEqual(sources.get(TRACK_ESTIMATE_SOURCE).data.features[0].geometry.coordinates[0].at(-1),
      sources.get("public-intel-flights").data.features[0].geometry.coordinates);
    follow.handlers.click();assert.equal(pans.length,paused+1);
    const count=fetches;click("vessels");
    assert.equal(sources.get(TRACK_SOURCE).data.features[0].properties.traffic_pack,"vessels");
    assert.equal(fetches,count+1,"Selecting a track requests its available history");
    assert.deepEqual(pans.at(-1),sources.get("public-intel-vessels").data.features[0].geometry.coordinates,"Ship selection follows its marker too");
    assert.deepEqual(sources.get(TRACK_ESTIMATE_SOURCE).data.features[0].geometry.coordinates[0].at(-1),
      sources.get("public-intel-vessels").data.features[0].geometry.coordinates);
    assert.equal(zoom,12,"Ship selection preserves zoom");
    sources.clear();layers.clear();events.get("style.load")();await wait();
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,1);
    close.handlers.click();assert.equal(drawer.hidden,true);assert.equal(sources.has(TRACK_SOURCE),false);
    assert.equal(sources.has(TRACK_ESTIMATE_SOURCE),false);
    click();clicks=[];events.get("click")({point:{x:9,y:9}});assert.equal(drawer.hidden,false);assert.equal(sources.has(TRACK_SOURCE),true);
    await controller.setTrafficFilters("flights",{query:"not-present"});await wait();assert.equal(drawer.hidden,true);assert.equal(sources.has(TRACK_SOURCE),false);
    click("vessels");await controller.setEnabled("vessels",false);await wait();assert.equal(sources.has(TRACK_SOURCE),false);
    await controller.setTrafficFilters("flights",{});await wait();click();
    now=1300;await controller.refresh();await wait();assert.equal(sources.has(TRACK_SOURCE),true,"Last-seen target keeps its selection");
    now=1310;report=fix(now);await controller.refresh();await wait();click();
    historyAvailable=true;center={lng:-.01,lat:0};const before=pans.length;click();await wait();
    assert.equal(pans.length,before+1,"Only the selection centers the marker");assert.equal(zoom,12,"Available history preserves zoom");
    emptyFeed=true;
    zoom=3;events.get("moveend")();
    await controller.refresh();await wait();assert.equal(pans.length,before+1,"Live updates do not repeatedly move the camera");
    assert.equal(sources.get(TRACK_SOURCE).data.features.length,1,"Route survives zooming below traffic minimum");
    assert.equal(sources.get("public-intel-flights").data.features.length,1,"Selected aircraft survives empty refresh at zoom 3");
    now=4000;await controller.refresh();await wait();
    assert.equal(sources.get("public-intel-flights").data.features.length,1,"Selected aircraft remains beyond normal cache expiry");
    assert.match(body.innerHTML,/Last seen/);
    events.get("remove")();assert.equal(sources.has(TRACK_SOURCE),false);assert.equal(drawer.removed,true);
  } finally {controller?.destroy();Date.now=originalNow;delete globalThis.requestAnimationFrame;}
});
