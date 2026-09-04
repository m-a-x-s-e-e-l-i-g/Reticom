import assert from "node:assert/strict";
import test from "node:test";

import {latestOperatorMapTarget, mapTargetCapabilities} from "../src/retium/static/map-target.js";

test("field waypoints offer both navigation modes but no chat", () => {
  assert.deepEqual(mapTargetCapabilities({role: "field", kind: "marker", markerType: "waypoint"}), {
    navigate: true,
    privateChat: false,
    targetKind: "waypoint",
  });
});

test("another field operator offers navigation and private chat", () => {
  assert.deepEqual(mapTargetCapabilities({
    role: "field",
    kind: "operator",
    senderHash: "peer",
    localIdentityHash: "self",
  }), {
    navigate: true,
    privateChat: true,
    targetKind: "operator",
  });
});

test("own operator marker never offers a private chat", () => {
  assert.equal(mapTargetCapabilities({
    role: "field",
    kind: "operator",
    senderHash: "self",
    localIdentityHash: "self",
  }).privateChat, false);
});

test("operator direct navigation follows the latest verified displayed fix", () => {
  assert.deepEqual(latestOperatorMapTarget([
    {type: "position.updated", callsign: "VIPER", lat: 52.1, lon: 4.3, network: {sender_hash: "peer"}},
  ], "peer"), {
    target: [4.3, 52.1],
    label: "VIPER",
  });
});
