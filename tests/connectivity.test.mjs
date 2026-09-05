import test from "node:test";
import assert from "node:assert/strict";
import {connectivityState} from "../src/retium/static/connectivity.js";

const state = (interfaces, role = "field") => ({role, team: {joined: true}, network: {interfaces: {interfaces}}});
const tcp = {type: "TCPClientInterface", status: true};
test("configured but down interfaces never count as connectivity", () => {
  assert.equal(connectivityState(state([{...tcp, status: false}])).bars, 0);
  assert.equal(connectivityState(state([{type: "TCPServerInterface", status: true, clients: 0}])).bars, 0);
});
test("connected does not imply measured quality or team reachability", () => {
  assert.equal(connectivityState(state([tcp])).bars, 1);
  const offline = connectivityState(state([tcp]), {at: 1000, online: false}, 1001);
  assert.equal(offline.label, "Team offline");
  assert.equal(offline.latency, null);
});
test("fresh measured response sets quality and expires", () => {
  for (const [ms, bars] of [[100, 4], [700, 3], [2000, 2]]) {
    assert.equal(connectivityState(state([tcp]), {at: 1000, online: true, ms}, 2000).bars, bars);
  }
  assert.equal(connectivityState(state([tcp]), {at: 1000, online: true, ms: 100}, 46000).bars, 1);
  assert.equal(connectivityState(state([tcp]), {at: 1000, online: true, ms: null}, 2000).bars, 1);
});
test("local hosting and unreachable backend cannot display full bars", () => {
  assert.equal(connectivityState(state([tcp], "gateway"), {at: 1000, online: true, ms: 0}, 2000).bars, 1);
  assert.equal(connectivityState(null).bars, 0);
});
