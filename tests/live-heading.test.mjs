import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {HeadingClock, HeadingTracker, HeadingConnection, HEADING_TTL, headingAnchor, smoothHeading} from "../src/retium/static/live-heading.js";

const sender_hash = "a".repeat(32);
const sample = (heading, at = 1000) => ({type:"heading.sample", sender_hash, heading, at});
test("browser/node clock skew is corrected without reviving stale or reordered headings", () => {
  for (const offset of [6000, -6000, 60000]) {
    const clock = new HeadingClock();
    assert.equal(clock.sync(10000 + offset + 10, 10000, 20), true);
    assert.equal(clock.offset, offset);
    const tracker = new HeadingTracker(clock);
    const now = clock.now(10100);
    assert.equal(tracker.accept(sample(30, 10000 + offset), now), true);
    assert.equal(tracker.active(now).length, 1);
    tracker.accept(sample(null, 10001 + offset), now);
    assert.equal(tracker.accept(sample(30, 10000 + offset), now), false);
    assert.equal(tracker.active(now).length, 0);
    assert.equal(tracker.accept(sample(30, now - HEADING_TTL - 1), now), false);
    const fresh = sample(45, now);
    tracker.accept(fresh, now);
    assert.equal(tracker.active(now + HEADING_TTL + 1).length, 0);
    assert.equal(headingAnchor({type:"position.updated",lat:51,lon:4,accuracy:10,created_at:now/1000}, clock.now(10200)), true);
  }
});
test("slow and malformed clock replies cannot calibrate the stream", () => {
  const clock = new HeadingClock();
  assert.equal(clock.sync(10000, 100, 4001), false);
  assert.equal(clock.sync(NaN, 100, 10), false);
  assert.equal(clock.sync(10000, 100, -1), false);
  assert.equal(clock.ready, false);
  clock.sync(10000, 4000, 0);
  clock.reset();
  assert.equal(clock.ready, false);
  assert.equal(clock.offset, 0);
});
test("headings interpolate across north using the short turn", () => {
  assert.equal(smoothHeading(359, 1, .5), 0);
  assert.equal(smoothHeading(1, 359, .5), 0);
});
test("stale samples expire and reordered packets cannot undo a stop", () => {
  const tracker = new HeadingTracker();
  assert.equal(tracker.accept(sample(90), 1000), true);
  assert.equal(tracker.active(1000).length, 1);
  tracker.accept(sample(null, 1100), 1100);
  assert.equal(tracker.accept(sample(10, 1099), 1100), false);
  assert.equal(tracker.active(1100).length, 0);
  tracker.accept(sample(30, 1200), 1200);
  assert.equal(tracker.active(1200 + HEADING_TTL + 1).length, 0);
  assert.equal(tracker.accept(sample(90), 9000), false);
});
test("invalid headings are ignored and reduced motion snaps to actual samples", () => {
  const tracker = new HeadingTracker();
  for (const heading of [NaN, -1, 360, "90"]) assert.equal(tracker.accept(sample(heading), 1000), false);
  tracker.accept(sample(10), 1000);
  tracker.accept(sample(170, 1100), 1100);
  assert.equal(tracker.active(1101, true)[0].heading, 170);
});
test("fresh compass must not make an old or inaccurate position look live", () => {
  const fix = {type:"position.updated", lat:51, lon:4, created_at:100, accuracy:10};
  assert.equal(headingAnchor(fix, 101000), true);
  assert.equal(headingAnchor(fix, 221000), false);
  assert.equal(headingAnchor({...fix, accuracy:500}, 101000), false);
  assert.equal(headingAnchor({...fix, accuracy:undefined}, 101000), false);
  assert.equal(headingAnchor({...fix, accuracy:-1}, 101000), false);
});
test("Android gates heading delivery on resumed, unlocked and interactive state", () => {
  const java = readFileSync(new URL("../android/app/src/main/java/com/retium/field/MainActivity.java", import.meta.url), "utf8");
  assert.match(java, /protected void onPause\(\)[\s\S]*?compassForeground = false;[\s\S]*?stopCompass\(\)/);
  assert.match(java, /power\.isInteractive\(\)/);
  assert.match(java, /!keyguard\.isKeyguardLocked\(\)/);
  assert.match(java, /public boolean isHeadingSharingActive/);
  assert.match(java, /GeomagneticField/);
});

test("sharing stops on toggle, stale sensor or hidden app and never queues buffered samples", () => {
  const previous = Object.fromEntries(["document", "window", "WebSocket"].map(key => [key, globalThis[key]]));
  const sends = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    bufferedAmount = 0;
    listeners = {};
    addEventListener(name, fn) { this.listeners[name] = fn; }
    send(raw) { sends.push(JSON.parse(raw)); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = Socket;
  globalThis.window = {addEventListener() {}, removeEventListener() {}};
  globalThis.document = {hidden:false, ...window};
  let enabled = true, sensor = {heading:90, at:Date.now()}, connection;
  try {
    connection = new HeadingConnection({url:()=>"ws://test", operational:()=>true,
      eligible:()=>enabled, sample:()=>sensor, receive() {}, status() {}});
    connection.tick();
    connection.socket.listeners.message({data:JSON.stringify({type:"heading.ready", ready:true, interval_ms:100})});
    assert.equal(sends.at(-1).type, "heading.clock");
    // Wrong probes and pre-calibration samples cannot affect the stream.
    connection.socket.listeners.message({data:JSON.stringify({type:"heading.clock", request_id:-1, server_at:Date.now()+6000})});
    assert.equal(connection.clock.ready, false);
    connection.socket.listeners.message({data:JSON.stringify({type:"heading.clock", request_id:connection.probe.id, server_at:Date.now()+6000})});
    assert.equal(connection.clock.ready, true);
    connection.tick();
    assert.equal(sends.at(-1).heading, 90);
    assert.ok(Math.abs(sends.at(-1).at - Date.now() - 6000) < 50);
    enabled = false;
    connection.tick();
    assert.equal(sends.at(-1).heading, null);
    const count = sends.length;
    connection.tick();
    assert.equal(sends.length, count);
    enabled = true;
    connection.lastSent = 0;
    connection.socket.bufferedAmount = 1000;
    connection.tick();
    assert.equal(sends.length, count);
    connection.socket.bufferedAmount = 0;
    sensor = {heading:180, at:Date.now()};
    connection.tick();
    assert.equal(sends.at(-1).heading, 180);
    sensor.at -= 2000;
    connection.tick();
    assert.equal(sends.at(-1).heading, null);
    document.hidden = true;
    connection.tick();
    assert.equal(connection.socket, null);
  } finally {
    connection?.destroy();
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  }
});
