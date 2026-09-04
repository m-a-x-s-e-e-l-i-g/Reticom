import assert from "node:assert/strict";
import test from "node:test";

import {
  mapMessageReadStorageKey,
  mapOverlayMessages,
  parseReadMessageIds,
  shouldDismissMapMessage,
} from "../src/retium/static/map-message-read.js";

test("scopes read map messages to the joined team", () => {
  assert.equal(mapMessageReadStorageKey("abc123"), "retium.mapMessagesRead.abc123");
  assert.equal(mapMessageReadStorageKey(""), "retium.mapMessagesRead.unjoined");
});

test("keeps unique valid message ids within the storage cap", () => {
  assert.deepEqual(parseReadMessageIds(["one", "two", "one", "", null], 2), ["one", "two"]);
  assert.deepEqual(parseReadMessageIds(["one", "two", "three"], 2), ["two", "three"]);
  assert.deepEqual(parseReadMessageIds("not-an-array"), []);
});

test("map overlays show incoming messages but never your own transmissions", () => {
  const events = [
    {id: "own-voice", type: "ptt.broadcast", network: {sender_hash: "local"}},
    {id: "incoming-text", type: "chat.message", network: {sender_hash: "peer-a"}},
    {id: "own-text", type: "chat.message", network: {sender_hash: "local"}},
    {id: "incoming-voice", type: "ptt.broadcast", network: {sender_hash: "peer-b"}},
    {id: "marker", type: "marker.created", network: {sender_hash: "peer-a"}},
  ];

  assert.deepEqual(
    mapOverlayMessages(events, {localIdentityHash: "local"}).map(({id}) => id),
    ["incoming-text", "incoming-voice"],
  );
});

test("read incoming messages stay hidden without revealing older own messages", () => {
  const events = [
    {id: "read", type: "chat.message", network: {sender_hash: "peer-a"}},
    {id: "own", type: "chat.message", network: {sender_hash: "local"}},
    {id: "visible", type: "chat.message", network: {sender_hash: "peer-b"}},
  ];

  assert.deepEqual(
    mapOverlayMessages(events, {
      localIdentityHash: "local",
      readIds: new Set(["read"]),
    }).map(({id}) => id),
    ["visible"],
  );
});

test("dismisses a deliberate horizontal swipe", () => {
  assert.equal(shouldDismissMapMessage({deltaX: 96, deltaY: 12, width: 300, elapsedMs: 600}), true);
  assert.equal(shouldDismissMapMessage({deltaX: -48, deltaY: 8, width: 300, elapsedMs: 60}), true);
  assert.equal(shouldDismissMapMessage({deltaX: 68, deltaY: 14, width: 300, elapsedMs: 500}), true);
});

test("keeps the card for short or vertical gestures", () => {
  assert.equal(shouldDismissMapMessage({deltaX: 34, deltaY: 5, width: 300, elapsedMs: 500}), false);
  assert.equal(shouldDismissMapMessage({deltaX: 80, deltaY: 100, width: 300, elapsedMs: 100}), false);
});
