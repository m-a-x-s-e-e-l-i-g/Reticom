// Closure reports are time-sensitive; never use the indefinite area cache.
export function roadDisplayData(data, now = Date.now()/1000) {
  return {...data, type:"FeatureCollection", features:(data?.features || []).filter(feature => {
    const p = feature.properties || {};
    return Number.isFinite(p.starts_at) && Number.isFinite(p.expires_at) && p.starts_at <= now
      && p.expires_at > now && (p.ends_at == null || Number.isFinite(p.ends_at) && p.ends_at > now);
  }).map(feature => ({...feature, properties:{...feature.properties, stale:feature.properties.stale || now >= feature.properties.stale_at}}))};
}

export function retainRoads(previous, incoming, now = Date.now()/1000) {
  const replaced = new Set(incoming.replaced_sources || []);
  const features = new Map((previous?.features || []).filter(f => !replaced.has(f.properties?.road_source)).map(f => [f.id,f]));
  for (const feature of incoming.features || []) features.set(feature.id, feature);
  return roadDisplayData({...incoming, features:[...features.values()]}, now);
}

export const ROAD_IMPACTS = {closed:"Road / carriageway closed", restricted:"Lane or vehicle restriction", incident:"Disruption reported · closure not confirmed"};
export function roadLayers(source) {
  const color = ["match",["get","road_impact"],"closed","#df8275","restricted","#d8b16c","#d7c98b"];
  const opacity = ["case",["==",["get","stale"],true],.4,.95];
  const lineFilter = ["==",["geometry-type"],"LineString"];
  return [
    {id:`${source}-areas`,type:"line",source,filter:lineFilter,paint:{"line-color":"#181713","line-width":8,"line-opacity":opacity}},
    {id:`${source}-lines`,type:"line",source,filter:lineFilter,paint:{"line-color":color,"line-width":4,"line-opacity":opacity,"line-dasharray":[2,1]}},
    {id:`${source}-points`,type:"circle",source,filter:["==",["geometry-type"],"Point"],paint:{"circle-radius":6,"circle-color":color,"circle-opacity":opacity,"circle-stroke-color":"#181713","circle-stroke-width":2}},
    {id:`${source}-labels`,type:"symbol",source,minzoom:11,filter:lineFilter,layout:{"symbol-placement":"line","text-field":["get","title"],"text-font":["Noto Sans Regular"],"text-size":11},paint:{"text-color":color,"text-opacity":opacity,"text-halo-color":"#181713","text-halo-width":2}},
  ];
}
