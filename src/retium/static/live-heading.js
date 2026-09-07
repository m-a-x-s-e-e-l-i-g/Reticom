export const HEADING_TTL = 4000;
export const headingDelta = (from, to) => ((to - from + 540) % 360) - 180;
export const smoothHeading = (from, to, fraction) => (from + headingDelta(from, to) * fraction + 360) % 360;

// Correlate browser time with its local node, not with the phone's clock.
// Never infer an offset from sensor packets: that would revive delayed samples.
export class HeadingClock {
  constructor() { this.reset(); }
  reset() { this.offset = 0; this.ready = false; }
  sync(serverAt, sentAt, roundTrip) {
    if (![serverAt, sentAt, roundTrip].every(Number.isFinite) || roundTrip < 0 || roundTrip > 1000) return false;
    this.offset = serverAt - (sentAt + roundTrip / 2);
    this.ready = true;
    return true;
  }
  now(localNow = Date.now()) { return localNow + this.offset; }
}

export class HeadingTracker {
  constructor(clock = new HeadingClock()) { this.clock = clock; this.samples = new Map(); }
  accept(frame, now = this.clock.now()) {
    if (frame.type !== "heading.sample" || !/^[a-f0-9]{32}$/.test(frame.sender_hash || "") ||
        !Number.isFinite(frame.at) || now - frame.at > HEADING_TTL || frame.at - now > HEADING_TTL) return false;
    const previous = this.samples.get(frame.sender_hash);
    if (previous && frame.at <= previous.at) return false;
    if (frame.heading === null) {
      this.samples.set(frame.sender_hash, {at: frame.at, stopped: true, received: now});
      return true;
    }
    if (!Number.isFinite(frame.heading) || frame.heading < 0 || frame.heading >= 360) return false;
    this.samples.set(frame.sender_hash, {at: frame.at, received: now, heading: frame.heading,
      shown: previous?.shown ?? frame.heading, drawn: now});
    return true;
  }
  active(now = this.clock.now(), reducedMotion = false) {
    const result = [];
    for (const [sender, sample] of this.samples) {
      const age = Math.max(now - sample.at, now - sample.received);
      if (age > HEADING_TTL) { this.samples.delete(sender); continue; }
      if (sample.stopped) continue;
      sample.shown = reducedMotion ? sample.heading : smoothHeading(sample.shown, sample.heading, 1 - Math.exp(-(now - sample.drawn) / 65));
      if (Math.abs(headingDelta(sample.shown, sample.heading)) < .15) sample.shown = sample.heading;
      sample.drawn = now;
      result.push({sender, heading: sample.shown, opacity: Math.min(1, (HEADING_TTL - age) / 1000)});
    }
    return result;
  }
  clear() { this.samples.clear(); }
}

// Only current positions are valid anchors. A live compass is not a live GPS fix.
export function headingAnchor(event, now = Date.now()) {
  return event?.type === "position.updated" && Number.isFinite(event.lat) && Number.isFinite(event.lon)
    && Math.abs(event.lat) <= 90 && Math.abs(event.lon) <= 180
    && now / 1000 - event.created_at >= -10 && now / 1000 - event.created_at <= 120
    && Number.isFinite(event.accuracy) && event.accuracy >= 0 && event.accuracy <= 100;
}

export class HeadingOverlay {
  constructor(map, tracker, ownIdentity = () => "", sourceId = "live-pointing") {
    this.map = map; this.tracker = tracker; this.ownIdentity = ownIdentity;
    this.sourceId = sourceId;
    this.operators = []; this.frame = null; this.signature = ""; this.lastDraw = 0;
    map.on("move", () => this.wake());
    map.on("style.load", () => { this.signature = ""; this.wake(); });
    map.on("remove", () => { this.removed = true; cancelAnimationFrame(this.frame); });
  }
  setOperators(events) { this.operators = events; this.wake(); }
  destroy() {
    this.removed = true; cancelAnimationFrame(this.frame);
    if (this.map.getLayer(`${this.sourceId}-ray`)) this.map.removeLayer(`${this.sourceId}-ray`);
    if (this.map.getSource(this.sourceId)) this.map.removeSource(this.sourceId);
  }
  wake() { if (!this.removed && this.frame === null) this.frame = requestAnimationFrame(() => this.draw()); }
  draw() {
    this.frame = null;
    if (this.removed || document.hidden || !this.map.getCanvas().offsetParent || !this.map.isStyleLoaded()) return;
    const now = this.tracker.clock.now();
    if (now - this.lastDraw < 32) { this.wake(); return; }
    this.lastDraw = now;
    const map = this.map;
    if (!map.getSource(this.sourceId)) {
      map.addSource(this.sourceId, {type:"geojson", data:{type:"FeatureCollection", features:[]}});
      map.addLayer({id:`${this.sourceId}-ray`, type:"line", source:this.sourceId, paint:{
        "line-color":["get","color"], "line-width":2, "line-opacity":["*",["get","opacity"],.55]}});
    }
    const features = [];
    const active = this.tracker.active(now, matchMedia("(prefers-reduced-motion: reduce)").matches);
    for (const sample of active) {
      if (sample.sender === this.ownIdentity()) continue;
      const event = this.operators.find(event => event.network?.sender_hash === sample.sender && headingAnchor(event, now));
      if (!event) continue;
      const origin = map.project([event.lon, event.lat]);
      const angle = (sample.heading - map.getBearing()) * Math.PI / 180;
      const dx = Math.sin(angle), dy = -Math.cos(angle);
      const point = (forward, side = 0) => {
        const position = map.unproject([origin.x + dx * forward - dy * side, origin.y + dy * forward + dx * side]);
        return [position.lng, position.lat];
      };
      features.push({type:"Feature", geometry:{type:"MultiLineString", coordinates:[
        [point(15), point(76)], [point(66,-5), point(76), point(66,5)]]}, properties:{
        opacity:sample.opacity, color:event.headingColor || "#899a78"}});
    }
    const signature = JSON.stringify(features);
    if (signature !== this.signature) {
      this.signature = signature;
      map.getSource(this.sourceId).setData({type:"FeatureCollection", features});
    }
    if (active.length) this.wake();
  }
}

export class HeadingConnection {
  constructor({url, operational, eligible, sample, receive, status, clock = new HeadingClock()}) {
    Object.assign(this, {url, operational, eligible, sample, receive, status, clock});
    this.socket = null; this.ready = false; this.sent = false; this.lastSent = 0; this.retryAt = 0; this.period = 100;
    this.probe = null; this.probeId = 0; this.lastProbe = -Infinity;
    this.timer = setInterval(() => this.tick(), 100);
    this.onVisibility = () => this.tick(); this.onHide = () => this.close();
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("pagehide", this.onHide);
  }
  destroy() {
    clearInterval(this.timer); this.close();
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("pagehide", this.onHide);
  }
  close() {
    if (this.socket) { this.socket.close(); this.socket = null; }
    this.ready = false; this.sent = false;
    this.clock.reset(); this.probe = null; this.lastProbe = -Infinity;
    this.receive({type:"heading.clear"});
  }
  tick() {
    const now = Date.now();
    if (!this.operational() || document.hidden) { this.close(); this.status("Paused"); return; }
    if (!this.socket && now >= this.retryAt) {
      const socket = new WebSocket(this.url()); this.socket = socket;
      socket.addEventListener("message", event => {
        if (socket !== this.socket) return;
        let frame; try { frame = JSON.parse(event.data); } catch { return; }
        if (frame.type === "heading.ready") {
          this.ready = frame.ready === true;
          this.period = Math.max(100, Math.min(2000, Number(frame.interval_ms) || 100));
        } else if (frame.type === "heading.clock" && frame.request_id === this.probe?.id) {
          const offset = this.clock.offset;
          if (this.clock.sync(frame.server_at, this.probe.at, performance.now() - this.probe.started) &&
              Math.abs(this.clock.offset - offset) > 1000) this.receive({type:"heading.clear"});
          this.probe = null;
        } else if (frame.type === "heading.sample" && this.clock.ready) this.receive(frame);
      });
      socket.addEventListener("close", () => {
        if (socket === this.socket) { this.close(); this.retryAt = Date.now() + 2000; }
      });
    }
    if (this.socket?.readyState === WebSocket.OPEN && this.socket.bufferedAmount <= 512 && performance.now() - this.lastProbe >= 2000) {
      this.lastProbe = performance.now();
      this.probe = {id:++this.probeId, at:now, started:this.lastProbe};
      this.socket.send(JSON.stringify({type:"heading.clock", request_id:this.probe.id}));
    }
    const sample = this.sample();
    const allowed = this.eligible() && sample && now - sample.at < 1500;
    const heading = allowed ? sample.heading : null;
    this.status(!this.eligible() ? "Sharing off / paused" : !sample || now - sample.at >= 1500
      ? "Waiting for compass" : !this.ready ? "Waiting for live Reticulum link" : !this.clock.ready ? "Synchronizing live clock"
      : `Live · up to ${Number((1000 / this.period).toFixed(1))} updates/s · encrypted`);
    if (!this.ready || !this.clock.ready || this.socket?.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 512) return;
    if (heading === null && !this.sent) return;
    if (heading !== null && (now - this.lastSent < this.period ||
        (this.sent && Math.abs(headingDelta(this.lastHeading, heading)) < 1 && now - this.lastSent < 1000))) return;
    this.socket.send(JSON.stringify({type:"heading.sample", heading, at:Math.round(this.clock.now(now))}));
    this.sent = heading !== null; this.lastHeading = heading; this.lastSent = now;
  }
}
