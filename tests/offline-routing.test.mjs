import test from "node:test";
import assert from "node:assert/strict";
import {calculateDeviceRoute} from "../src/retium/static/offline-routing.js";

const origin = [4, 51], target = [4.1, 51.1];
const response = (payload, ok = true) => ({ok, json: async () => payload});

test("uses local routing without making any external request", async () => {
  const calls = [];
  const result = await calculateDeviceRoute(origin, target, "walking", {
    fetcher: async (url, options) => { calls.push(url); assert.equal(JSON.parse(options.body).profile, "walking"); return response({code: "Ok", source: "offline"}); },
    beforeOnline: () => assert.fail("must not reserve online request"),
  });
  assert.equal(result.source, "offline");
  assert.deepEqual(calls, ["/api/offline-routing/route"]);
});

for (const code of ["outside_coverage", "engine_unavailable"]) {
  test(`online fallback only when allowed: ${code}`, async () => {
    const calls = [];
    const fetcher = async url => { calls.push(url); return calls.length === 1 ? response({code, detail: "Missing region"}, false) : response({code: "Ok"}); };
    assert.equal((await calculateDeviceRoute(origin, target, "walking", {fetcher})).source, "online");
    assert.match(calls[1], /^https:\/\/routing.openstreetmap.de\/routed-foot/);
    calls.length = 0;
    await assert.rejects(calculateDeviceRoute(origin, target, "walking", {fetcher, offlineOnly: true}), /Missing region/);
    assert.equal(calls.length, 1);
  });
}

for (const code of ["no_route", "engine_error", "invalid_coordinates"]) {
  test(`does not leak coordinates after local ${code}`, async () => {
    let calls = 0;
    await assert.rejects(calculateDeviceRoute(origin, target, "driving", {
      fetcher: async () => { calls++; return response({code, detail: "Cannot calculate"}, false); },
    }), /Cannot calculate/);
    assert.equal(calls, 1);
  });
}

test("cancelling before fallback prevents an online request", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(calculateDeviceRoute(origin, target, "driving", {
    signal: controller.signal,
    fetcher: async () => { calls++; return response({code: "outside_coverage"}, false); },
    beforeOnline: async () => controller.abort(),
  }), {name: "AbortError"});
  assert.equal(calls, 1);
});

const routes = [0,1].map(i=>({geometry:{type:"LineString",coordinates:[[4,51],[4.1,51+i*.01]]},distance:100+i*50,duration:20+i,legs:[{steps:[{name:`route ${i}`}]}]}));

test("driving selects the checked alternative and keeps its directions, distance and time",async()=>{
  const calls=[];
  const result=await calculateDeviceRoute(origin,target,"driving",{checkClosures:true,fetcher:async(url,options)=>{
    calls.push(url);
    if(url==="/api/offline-routing/route") { assert.equal(JSON.parse(options.body).alternatives,true);return response({code:"outside_coverage"},false); }
    if(url.startsWith("https:")) { assert.match(url,/alternatives=true/);return response({code:"Ok",routes}); }
    assert.equal(url,"/api/intel-packs/roads/check-route");
    assert.deepEqual(JSON.parse(options.body),{routes:routes.map(r=>r.geometry.coordinates),cached_only:false});
    return response({selected:1,status:"checked",note:"Alternative chosen"});
  }});
  assert.equal(result.routes[0],routes[1]);assert.equal(result.road_check.selected,1);assert.equal(calls.length,3);
});

test("offline closure checking requests cached data and never falls back online",async()=>{
  const result=await calculateDeviceRoute(origin,target,"driving",{checkClosures:true,offlineOnly:true,fetcher:async(url,options)=>{
    assert.ok(url.startsWith("/api/"));
    if(url==="/api/offline-routing/route") return response({code:"Ok",source:"offline",routes});
    assert.equal(JSON.parse(options.body).cached_only,true);
    return response({selected:0,status:"closures",warning:"Closure ahead"});
  }});
  assert.equal(result.road_check.warning,"Closure ahead");assert.equal(result.source,"offline");
});

test("closure-check failure remains visible and walking is not blocked by vehicle closures",async()=>{
  const fetcher=async(url)=> url==="/api/offline-routing/route" ? response({code:"Ok",routes}) : response({},false);
  assert.match((await calculateDeviceRoute(origin,target,"driving",{checkClosures:true,fetcher})).road_check.warning,/not been checked/);
  const walking=await calculateDeviceRoute(origin,target,"walking",{checkClosures:true,fetcher:async(url)=>{
    assert.equal(url,"/api/offline-routing/route");return response({code:"Ok",routes});
  }});
  assert.equal(walking.road_check,undefined);
});
