import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {readFileSync} from "node:fs";
import {mapMessageReadStorageKey, mapOverlayMessages, parseReadMessageIds} from "../src/retium/static/map-message-read.js";

const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
const fn = (name, next) => app.slice(app.indexOf(`function ${name}(`), app.indexOf(`function ${next}(`));
const message = (id, type = "chat.message") => ({id, type, callsign: "Peer", message: id, network: {sender_hash: "peer", received_at: 1}});

function harness(saved = new Map()) {
  const elements = Object.fromEntries(["commandLatestMessages", "latestMessages"].map(id => [id, {id, innerHTML: "", classList: {add() {}, remove() {}}, replaceChildren() { this.innerHTML = ""; }, querySelector() { return null; }}]));
  const timers = [];
  const context = vm.createContext({
    readMapMessageIds: new Set(), readMapMessageDestination: null, localIdentityHash: "local",
    commandEvents: [message("text"), message("voice", "ptt.broadcast")], fieldEvents: [message("field-only")],
    mapMessageReadStorageKey, mapOverlayMessages, parseReadMessageIds,
    localStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)},
    $: id => elements[id], document: {activeElement: null}, window: {innerWidth: 1000, setTimeout: callback => timers.push(callback)},
    escapeHtml: text => String(text), parseAutomaticReport: () => null, contactReportText: () => "",
    operatorColor: () => "olive", operatorGlyph: () => "•", timeLabel: () => "now", ownMessageRemoveButton: () => "",
    eventDescription: event => `Audio ${event.id}`, fetch: () => assert.fail("Dismissing must not send a network mutation"),
  });
  vm.runInContext([
    fn("loadReadMapMessages", "saveReadMapMessages"), fn("saveReadMapMessages", "readMapStylePreference"),
    fn("renderMapMessages", "renderLatestMessages"), fn("finishMapMessageCard", "settleMapMessageCard"),
  ].join("\n"), context);
  const card = id => ({dataset: {mapMessageTeam: context.readMapMessageDestination, mapMessageId: id}, closest: () => elements.commandLatestMessages,
    contains: () => false, offsetWidth: 400, classList: {add() {}, remove() {}}, style: {setProperty() {}}});
  return {context, elements, timers, saved, card};
}

test("Command text and audio dismiss locally, remain in feed and stay dismissed on reload", () => {
  const {context, elements, timers, saved, card} = harness();
  context.loadReadMapMessages("host-destination");
  context.renderMapMessages(context.commandEvents, "commandLatestMessages");
  assert.match(elements.commandLatestMessages.innerHTML, /Dismiss message from Peer from map/);
  assert.match(elements.commandLatestMessages.innerHTML, /data-map-message-id="text"/);
  context.finishMapMessageCard(card("text"), "text");
  timers.shift()();
  assert.doesNotMatch(elements.commandLatestMessages.innerHTML, /data-map-message-id="text"/);
  assert.match(elements.commandLatestMessages.innerHTML, /data-map-message-id="voice"/);
  assert.equal(elements.latestMessages.innerHTML, "", "Command must rerender its own overlay, not Field");
  context.finishMapMessageCard(card("voice"), "voice"); timers.shift()();
  assert.equal(elements.commandLatestMessages.innerHTML, "");
  assert.equal(context.commandEvents.length, 2, "Feed data remains untouched");
  const reload = harness(saved);
  reload.context.loadReadMapMessages("host-destination");
  reload.context.renderMapMessages(reload.context.commandEvents, "commandLatestMessages");
  assert.equal(reload.elements.commandLatestMessages.innerHTML, "");
});

test("team changes cannot leak dismissals or replay an old animation into the new overlay", () => {
  const {context, elements, timers, card} = harness();
  context.loadReadMapMessages("team-a");
  const oldCard = card("text");
  context.finishMapMessageCard(oldCard, "text");
  context.loadReadMapMessages("team-b");
  elements.commandLatestMessages.innerHTML = "new team is rendering";
  timers.shift()();
  assert.equal(elements.commandLatestMessages.innerHTML, "new team is rendering");
  context.finishMapMessageCard(oldCard, "voice");
  assert.equal(context.readMapMessageIds.size, 0);
  context.renderMapMessages(context.commandEvents, "commandLatestMessages");
  assert.match(elements.commandLatestMessages.innerHTML, /data-map-message-id="text"/);
  context.loadReadMapMessages("team-a");
  assert.equal(context.readMapMessageIds.has("text"), true);
});

test("host and joined Command both load read state before rendering, with desktop controls and touch handlers", () => {
  assert.match(app, /role === "gateway" \? state.network.destination : state.team.destination/);
  assert.ok(app.indexOf("if (mapMessageDestination !== readMapMessageDestination)") < app.indexOf('renderMapMessages(commandEvents, "commandLatestMessages")', app.indexOf("async function refresh()")));
  assert.match(app, /\["latestMessages", "commandLatestMessages"\]\.forEach\(id =>/);
  const css = readFileSync(new URL("../src/retium/static/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.command-latest-messages \.map-message-read\s*\{\s*display: inline-grid/);
});
