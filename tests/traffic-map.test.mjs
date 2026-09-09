import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {normalizeTrafficFilters, trafficDisplayData, trafficLayers} from "../src/retium/static/traffic-map.js";
import {intelPackUrls, intelFeatureHtml, mergeIntelResults, installIntelLayers, removeIntelLayers, createIntelPacks} from "../src/retium/static/intel-packs.js";

const now = Date.now()/1000;
const point = (id, overrides={}) => ({type:"Feature", id, geometry:{type:"Point",coordinates:[4.8,51.6]},properties:{title:id,traffic_pack:"flights",affiliation:"unknown",traffic_type:"helicopter",position_time:now-5,heading:90,...overrides}});
const data = (...features) => ({type:"FeatureCollection",features,status:"fresh"});
test("classification, type and search combine without assuming civilian ownership", () => {
  const source = data(point("mil",{affiliation:"military"}),point("commercial",{affiliation:"commercial",traffic_type:"medium",type_code:"A320"}),point("unknown"));
  assert.equal(trafficDisplayData("flights",source,{affiliation:"military"},now).features[0].id,"mil");
  assert.equal(trafficDisplayData("flights",source,{affiliation:"commercial",query:"a320"},now).features.length,1);
  assert.equal(trafficDisplayData("flights",source,{affiliation:"commercial",type:"helicopter"},now).features.length,0);
  assert.equal(trafficDisplayData("flights",source,{affiliation:"unknown"},now).features[0].id,"unknown");
  assert.equal(normalizeTrafficFilters({flights:{type:"invalid"}}).flights.type,"all");
});
test("last-seen positions remain cached for 30 minutes and selected targets stay visible", () => {
  const source = data(point("recent"),point("stale",{position_time:now-180}),point("expired",{position_time:now-1801}),point("invalid",{position_time:"bad"}));
  const display=trafficDisplayData("flights",source,{},now);
  assert.deepEqual(display.features.map(f=>f.id),["recent","stale"]);
  assert.equal(display.features[1].properties.stale,true);
  assert.equal(trafficDisplayData("flights",source,{},now+1801).features.length,0);
  assert.equal(trafficDisplayData("vessels",data(point("boat",{traffic_pack:"vessels",position_time:now-601})),{},now).features.length,1);
  const selected = data(point("selected",{position_time:now-3600,traffic_selected:true}));
  assert.equal(trafficDisplayData("flights",selected,{},now).features.length,1);
  assert.equal(trafficDisplayData("flights",selected,{query:"other"},now).features.length,0);
});
test("unknown heading is visibly distinct and sea/air shapes remain separate", () => {
  assert.equal(trafficDisplayData("flights",data(point("x",{heading:null})),{},now).features[0].properties.traffic_icon,"unknown");
  assert.equal(trafficDisplayData("flights",data(point("x")),{},now).features[0].properties.traffic_icon,"helicopter");
  assert.equal(trafficDisplayData("vessels",data(point("x",{traffic_pack:"vessels"})),{},now).features[0].properties.traffic_icon,"ship");
  const layers=trafficLayers("test");
  assert.equal(layers[1].layout["icon-rotation-alignment"],"map");
  assert.deepEqual(layers[1].layout["icon-size"].slice(0, 4),["interpolate",["linear"],["zoom"],6]);
});
test("live queries split date-line views and waiting is not presented as a successful empty feed", () => {
  const urls=intelPackUrls("vessels",{west:179,south:10,east:181,north:11},10);
  assert.equal(urls.length,2); assert.ok(urls.every(url=>url.startsWith("/api/intel-packs/live/vessels?")));
  assert.equal(mergeIntelResults([{...data(),status:"waiting",note:"Connecting"}]).status,"waiting");
});
test("public traffic popups escape names and explain uncertain classifications", () => {
  const html=intelFeatureHtml(point("x",{title:'<img onerror="bad()">',classification_note:"Ownership unknown",identity:"abcdef"}),{id:"flights",source:"ADSB.lol",source_url:"javascript:bad()"},{});
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|javascript:/);
  assert.match(html,/Ownership unknown/);assert.match(html,/Not a verified team report/);
});
test("aircraft and ships survive style recreation and do not move camera or replace team layers", () => {
  const sources=new Map(),layers=new Map([["verified-points",{id:"verified-points",type:"symbol"}]]);
  const map={getStyle:()=>({layers:[...layers.values()]}),getSource:id=>sources.get(id),addSource:(id,source)=>sources.set(id,{...source,setData(){}}),removeSource:id=>sources.delete(id),getLayer:id=>layers.get(id),addLayer:(layer,before)=>{assert.equal(before,"verified-points");layers.set(layer.id,layer);},removeLayer:id=>layers.delete(id)};
  for(const id of ["flights","vessels"]){installIntelLayers(map,{id},data()); assert.equal(layers.get(`public-intel-${id}-icons`).type,"symbol");removeIntelLayers(map,id);assert.equal(sources.size,0);assert.equal(layers.size,1);installIntelLayers(map,{id},data());removeIntelLayers(map,id);}
});
test("filter preferences are saved device-locally; AIS credentials never enter the browser stream", async () => {
  let settings={enabled:[],traffic_filters:normalizeTrafficFilters()}, saved;
  const controller=createIntelPacks({fetchImpl:async(_url,options)=>{
    if(options.method==="PATCH"){saved=JSON.parse(options.body);settings={...settings,...saved};}
    return {ok:true,json:async()=>({settings,packs:[{id:"flights",enabled:false}],credentials:{}})};
  }});
  try{await controller.ready;assert.equal(await controller.setTrafficFilters("flights",{type:"helicopter",affiliation:"military"}),true);assert.equal(saved.traffic_filters.flights.type,"helicopter");assert.equal(controller.getState().settings.traffic_filters.flights.affiliation,"military");}finally{controller.destroy();}
  const js=readFileSync(new URL("../src/retium/static/intel-packs.js",import.meta.url),"utf8");
  assert.doesNotMatch(js,/wss:\/\/stream\.aisstream/);
  assert.match(js,/visibilitychange/);
  assert.match(js,/releaseTraffic/);
  const css=readFileSync(new URL("../src/retium/static/intel-packs.css",import.meta.url),"utf8");
  assert.match(css,/intel-traffic-filter :is\(select, input, button\).*min-height: 44px/);
});
