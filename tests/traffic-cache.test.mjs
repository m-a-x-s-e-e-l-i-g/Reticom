import test from "node:test";
import assert from "node:assert/strict";
import {retainTraffic, TRAFFIC_CACHE_TARGETS} from "../src/retium/static/traffic-cache.js";

const fix = (identity, time, x=0) => ({type:"Feature", geometry:{type:"Point",coordinates:[x,0]}, properties:{identity,position_time:time}});
const data = (...features) => ({type:"FeatureCollection",features,status:"fresh"});

test("empty, partial, failed and out-of-order responses cannot erase newer observations", () => {
  const previous=data(fix("one",1000),fix("two",1001));
  for (const status of ["fresh","waiting","stale","error","zoom_in"]) {
    const result=retainTraffic(previous,{...data(fix("one",999,10)),status},1100);
    assert.equal(result.features.length,2);
    assert.equal(result.features.find(f=>f.properties.identity==="one").geometry.coordinates[0],0);
    assert.equal(retainTraffic(previous,{...data(),status},1100).features.length,2);
  }
  assert.equal(retainTraffic(previous,data(fix("one",1100,1)),1100).features.find(f=>f.properties.identity==="one").geometry.coordinates[0],1);
});

test("observation cache is bounded by age and count without renewing report timestamps", () => {
  assert.equal(retainTraffic(data(fix("old",1000)),data(),2801).features.length,0);
  const large=data(...Array.from({length:TRAFFIC_CACHE_TARGETS+10},(_,i)=>fix(String(i),1000+i/10)));
  const cached=retainTraffic(null,large,1600);
  assert.equal(cached.features.length,TRAFFIC_CACHE_TARGETS);
  assert.equal(cached.features[0].properties.position_time,1500.9);
});
