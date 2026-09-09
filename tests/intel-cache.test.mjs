import test from "node:test";
import assert from "node:assert/strict";
import {retainIntel, INTEL_CACHE_FEATURES, INTEL_CACHE_POSITIONS} from "../src/retium/static/intel-cache.js";
const point=(id,x=0)=>({type:"Feature",id,properties:{},geometry:{type:"Point",coordinates:[x,0]}});
const area={...point("area"),geometry:{type:"Polygon",coordinates:[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]]}};
const data=(features=[],status="fresh",extra={})=>({type:"FeatureCollection",features,status,...extra});
const boxes=[{west:-2,south:-2,east:2,north:2}];
test("zoom limits, failures and partial coverage preserve polygons and markers",()=>{
  for(const incoming of [data([],"zoom_in"),data([],"error"),data([],"cached",{coverage_complete:false}),data([],"fresh",{truncated:true})]) {
    assert.deepEqual(retainIntel(data([point("one"),area]),incoming,boxes).features,[point("one"),area]);
  }
});
test("complete newer coverage removes deleted objects only inside the refreshed region",()=>{
  const old=data([point("deleted"),area,point("other-region",10)]);
  assert.deepEqual(retainIntel(old,data(),boxes).features,[point("other-region",10)]);
  const newer={...area,properties:{title:"Updated name"}};
  assert.deepEqual(retainIntel(old,data([newer]),boxes).features,[newer,point("other-region",10)]);
});
test("static cache bounds both object count and complex geometry size",()=>{
  const many=Array.from({length:INTEL_CACHE_FEATURES+1},(_,i)=>point(i));
  assert.equal(retainIntel(null,data(many)).features.length,INTEL_CACHE_FEATURES);
  const huge={...area,geometry:{type:"LineString",coordinates:Array(INTEL_CACHE_POSITIONS+1).fill([0,0])}};
  const retained=retainIntel(null,data([huge,point("small")]));
  assert.deepEqual(retained.features,[point("small")]); assert.equal(retained.cache_limited,true);
});
