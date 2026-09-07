// Shared by Field, Command, draft previews and the combined-team map.
export const DRAWING_COLORS = {
  amber: "#b8a16d", red: "#c46855", green: "#899a78",
  blue: "#7ca4bd", purple: "#a393af", white: "#d5d1bd",
};
export const DRAWING_FILLS = ["none", "solid", "lines", "crosses"];

export function validDrawingArea(points) {
  const ring = areaRing((points || []).map(p => p.map(n => Number(n.toFixed(5)))));
  if (ring.length < 4) return false;
  const [x, y] = ring[0];
  const area = ring.slice(1).reduce((sum, p, i) => sum + (ring[i][0] - x) * (p[1] - y) - (p[0] - x) * (ring[i][1] - y), 0);
  return Math.abs(area) >= 1e-12;
}

export function areaRing(points) {
  const ring = [];
  for (const [lon, lat] of points || []) {
    let x = lon;
    if (ring.length) {
      while (x - ring.at(-1)[0] > 180) x -= 360;
      while (x - ring.at(-1)[0] < -180) x += 360;
    }
    if (!ring.length || x !== ring.at(-1)[0] || lat !== ring.at(-1)[1]) ring.push([x, lat]);
  }
  if (ring.length && (ring[0][0] !== ring.at(-1)[0] || ring[0][1] !== ring.at(-1)[1])) ring.push([...ring[0]]);
  return ring;
}

// Choose an interior midpoint near the centre, including concave freehand areas.
export function areaLabelPoint(points) {
  const ring = areaRing(points);
  if (ring.length < 4) return null;
  const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const low = Math.min(...ys), high = Math.max(...ys), cy = (low + high) / 2;
  let best = null, score = Infinity;
  for (let row = 0; row < 33; row++) {
    const y = low + (high - low) * (row + .5) / 33;
    const cuts = [];
    for (let i = 1; i < ring.length; i++) {
      const a = ring[i - 1], b = ring[i];
      if ((a[1] > y) !== (b[1] > y)) cuts.push(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]));
    }
    cuts.sort((a, b) => a - b);
    for (let i = 0; i + 1 < cuts.length; i += 2) {
      if (cuts[i + 1] - cuts[i] < 1e-10) continue;
      const x = (cuts[i] + cuts[i + 1]) / 2;
      const distance = (x - cx) ** 2 + (y - cy) ** 2;
      if (distance < score) { best = [x, y]; score = distance; }
    }
  }
  return best;
}

export function drawingFeatures(event, properties = {}, geometry = null) {
  const area = event.drawing_type === "area" && validDrawingArea(event.points);
  const colorKey = Object.hasOwn(DRAWING_COLORS, event.drawing_color) ? event.drawing_color : "amber";
  const props = {...properties, color: event.drawing_color ? DRAWING_COLORS[colorKey] : properties.color || DRAWING_COLORS.amber,
    drawingType: event.drawing_type || "trace", fillStyle: event.fill_style || "none",
    fillPattern: `drawing-${colorKey}-${event.fill_style === "crosses" ? "crosses" : "lines"}`,
    label: event.label || (area ? "Area" : event.drawing_type === "arrow" ? "Direction arrow" : "Trace"),
    drawingLabel: false};
  const features = [{type: "Feature", geometry: area ? {type: "Polygon", coordinates: [areaRing(event.points)]}
    : geometry || {type: "LineString", coordinates: event.points}, properties: props}];
  if (event.label) {
    const center = area ? areaLabelPoint(event.points) : event.points?.[Math.floor(event.points.length / 2)];
    if (center) features.push({type: "Feature", geometry: {type: "Point", coordinates: center}, properties: {...props, drawingLabel: true}});
  }
  return features;
}

export function installDrawingPatterns(map) {
  for (const [key, color] of Object.entries(DRAWING_COLORS)) {
    for (const style of ["lines", "crosses"]) {
      const id = `drawing-${key}-${style}`;
      if (map.hasImage(id)) continue;
      const size = 24, data = new Uint8Array(size * size * 4);
      const rgb = [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16));
      for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        const mark = style === "lines" ? (x + y) % 12 < 2
          : Math.abs(x - 12) <= 4 && Math.abs(y - 12) <= 4 && (Math.abs(x - y) < 2 || Math.abs(x + y - 24) < 2);
        data.set([...rgb, mark ? 180 : 0], (y * size + x) * 4);
      }
      map.addImage(id, {width: size, height: size, data});
    }
  }
}

export function addDrawingDecorationLayers(map, source, prefix = source) {
  installDrawingPatterns(map);
  const area = ["==", ["get", "drawingType"], "area"];
  map.addLayer({id: `${prefix}-fill`, type: "fill", source,
    filter: ["all", area, ["==", ["geometry-type"], "Polygon"]],
    paint: {"fill-color": ["get", "color"], "fill-opacity": ["case", ["==", ["get", "fillStyle"], "solid"], .23, 0]}});
  map.addLayer({id: `${prefix}-pattern`, type: "fill", source,
    filter: ["all", area, ["in", ["get", "fillStyle"], ["literal", ["lines", "crosses"]]],
      ["==", ["geometry-type"], "Polygon"]],
    paint: {"fill-pattern": ["get", "fillPattern"], "fill-opacity": .65}});
  map.addLayer({id: `${prefix}-names`, type: "symbol", source,
    filter: ["==", ["get", "drawingLabel"], true],
    layout: {"text-field": ["get", "label"], "text-size": 13, "text-font": ["Noto Sans Regular"], "text-max-width": 14},
    paint: {"text-color": ["get", "color"], "text-halo-color": "#090c09", "text-halo-width": 2}});
}

export function drawingReviewHtml() {
  return `<span data-draw-heading>DRAW TRACE</span>
    <div class="drawing-review hidden" data-drawing-review>
      <label>Name<input data-drawing-name maxlength="80" placeholder="e.g. Search area Alpha" autocomplete="off"></label>
      <div class="drawing-review-options"><label>Shape<select data-drawing-shape><option value="trace">Open trace</option><option value="area">Closed area</option><option value="arrow">Arrow</option></select></label>
      <label>Fill<select data-drawing-fill>${DRAWING_FILLS.map(fill => `<option value="${fill}">${fill === "none" ? "Outline only" : fill[0].toUpperCase() + fill.slice(1)}</option>`).join("")}</select></label></div>
      <fieldset class="drawing-colors"><legend>Color</legend>${Object.entries(DRAWING_COLORS).map(([name, color]) => `<label style="--drawing-color:${color}" title="${name}"><input type="radio" name="drawing-color" data-drawing-color value="${name}" aria-label="${name}" ${name === "amber" ? "checked" : ""}><span aria-hidden="true"></span></label>`).join("")}</fieldset>
      <small data-drawing-feedback aria-live="polite">Preview only · not shared yet</small>
    </div>
    <div class="drawing-review-actions"><button type="button" class="text-button" data-clear-map-drawing>REDRAW</button><button type="button" class="text-button" data-cancel-map-drawing>DISCARD</button><button type="button" class="compact" data-send-map-drawing disabled>SAVE & SHARE</button></div>`;
}
