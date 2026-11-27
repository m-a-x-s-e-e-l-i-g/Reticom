import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {readFileSync} from "node:fs";

const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");
const preferences = app.slice(app.indexOf("function readIncomingAlertPreference("), app.indexOf("function loadReadMapMessages("));
const actions = app.slice(app.indexOf("async function armIncomingAlerts("), app.indexOf("function hasNativeTtsBridge("));

function harness(preference = null, play = async () => {}) {
  const audio = [], writes = [], notices = [], native = [];
  const context = vm.createContext({
    localStorage: {getItem: () => preference, setItem: (...args) => writes.push(args)},
    window: {RetiumAndroid: {setIncomingAlertsEnabled: value => native.push(value)}, speechSynthesis: {cancel() {}}},
    Audio: class { constructor(source) { this.source = source; audio.push(this); } play() { return play(); } pause() {} },
    incomingAlertsEnabled: true, incomingAudioReady: false, incomingAudioUnlock: null, incomingAudioUnlockGeneration: 0,
    incomingTtsSpeaking: false, incomingCuePlaying: false, activeIncomingCue: null, activeIncomingAudio: null,
    incomingCuePreloads: [{pause() {}}],
    updateIncomingAlertsUi() {}, toast: (...args) => notices.push(args),
  });
  vm.runInContext(preferences + actions, context);
  context.incomingAlertsEnabled = context.readIncomingAlertPreference();
  return {context, audio, writes, notices, native};
}

test("fresh installs automatically activate alerts without requiring the arm button", async () => {
  const {context, audio, writes, notices} = harness();
  assert.equal(context.incomingAlertsEnabled, true);
  await context.armIncomingAlerts({automatic: true});
  assert.equal(context.incomingAudioReady, true);
  assert.equal(audio[0].volume, 1, "a muted probe must not give a false autoplay success");
  assert.deepEqual(writes, []);
  assert.deepEqual(notices, []);
});

test("an explicit saved mute is preserved at startup and during normal interactions", async () => {
  const {context, audio, writes} = harness("off");
  await context.armIncomingAlerts({automatic: true});
  context.automaticallyArmIncomingAlerts({isTrusted: true, type: "pointerup"});
  assert.equal(context.incomingAlertsEnabled, false);
  assert.equal(context.incomingAudioReady, false);
  assert.equal(audio.length, 0);
  assert.deepEqual(writes, []);
});

test("blocked browser autoplay retries on normal interaction without changing the preference", async () => {
  let blocked = true;
  const {context, notices, writes} = harness(null, async () => { if (blocked) throw new Error("NotAllowedError"); });
  await context.armIncomingAlerts({automatic: true});
  assert.equal(context.incomingAudioReady, false);
  assert.equal(context.incomingAlertsEnabled, true);
  blocked = false;
  context.automaticallyArmIncomingAlerts({isTrusted: true, type: "pointerup"});
  await context.incomingAudioUnlock;
  assert.equal(context.incomingAudioReady, true);
  assert.deepEqual(notices, []);
  assert.deepEqual(writes, []);
});

test("the alert toggle gesture is not intercepted by automatic activation", () => {
  const {context, audio} = harness();
  context.automaticallyArmIncomingAlerts({isTrusted: true, target: {closest: () => true}});
  context.automaticallyArmIncomingAlerts({isTrusted: false});
  context.automaticallyArmIncomingAlerts({isTrusted: true, type: "keydown", key: "Escape"});
  assert.equal(audio.length, 0);
});

test("muting during an in-flight unlock cannot be undone by its late completion", async () => {
  let finish;
  const {context, native} = harness(null, () => new Promise(resolve => { finish = resolve; }));
  const pending = context.armIncomingAlerts({automatic: true});
  context.muteIncomingAlerts();
  finish(); await pending;
  assert.equal(context.incomingAlertsEnabled, false);
  assert.equal(context.incomingAudioReady, false);
  assert.deepEqual(native, [false]);
});

test("manual enable persists and synchronizes with Android background playback", async () => {
  const {context, writes, native} = harness("off");
  await context.armIncomingAlerts();
  assert.equal(context.incomingAudioReady, true);
  assert.deepEqual(writes, [["retium.incomingAlerts", "on"]]);
  assert.deepEqual(native, [true]);
  assert.match(app, /void armIncomingAlerts\(\{automatic: true\}\);\s*document.addEventListener\("pointerup"/);
});

function playbackHarness(playLive, nativeHistory = [], playCue = async () => {}, android = true) {
  const played = [];
  const persisted = new Set(nativeHistory);
  const context = vm.createContext({
    window: {RetiumAndroid: android ? {
      getSeenIncomingEventIds: () => JSON.stringify([...persisted]),
      syncSeenIncomingEventIds: ids => JSON.parse(ids).forEach(id => persisted.add(id)),
    } : undefined},
    Audio: class {
      constructor(url) { this.src = url; this.recorded = Boolean(url); this.readyState = 2; this.events = {}; }
      addEventListener(name, handler) { this.events[name] = handler; }
      pause() {}
      play() {
        played.push(this.recorded ? "archive" : "live");
        if (!this.recorded) return playLive();
        setImmediate(() => this.events.ended());
        return Promise.resolve();
      }
    },
    HTMLMediaElement: {HAVE_CURRENT_DATA: 2}, URL, Blob, setTimeout, clearTimeout,
    incomingLiveVoice: null, incomingRecordedClipId: null, incomingFeedInitialized: true,
    incomingAlertsEnabled: true, incomingAlertQueue: Promise.resolve(), activeIncomingAudio: null,
    seenIncomingEventIds: new Set(), localIdentityHash: "local", commsOnly: false,
    apiUrl: url => url, requestVoiceTranscript() {}, toast() {}, updateIncomingAlertsUi() {},
    playIncomingMessageCue: playCue, INCOMING_MESSAGE_CUE_URL: "start",
    INCOMING_MESSAGE_END_CUE_URL: "end", VOICE_READY_TIMEOUT_MS: 500,
    incomingCuePreloads: [{currentTime: 5, play() { return playCue("live beep", this.volume, this.currentTime); }}],
  });
  vm.runInContext(app.slice(app.indexOf("function waitForIncomingVoiceReady("), app.indexOf("function setLiveVoiceAvailability("))
    + app.slice(app.indexOf("function readNativeIncomingHistory("), app.indexOf("function shortHash(")), context);
  const stream = {stream_id: "clip-1", callsign: "Other operator", sender_hash: "other"};
  const archive = {id: "different-event-id", clip_id: stream.stream_id, type: "ptt.broadcast",
    created_at: 1, network: {sender_hash: stream.sender_hash}};
  const finishStream = () => {
    context.startIncomingLiveVoice(stream);
    context.receiveIncomingLiveChunk(stream.stream_id, 0, new Uint8Array([1]).buffer);
    context.finishIncomingLiveVoice(stream.stream_id);
  };
  return {context, played, persisted, archive, finishStream};
}

test("history delivered after the first team sync stays silent", async () => {
  const {context} = playbackHarness(async () => {});
  const spoken = [];
  context.playIncomingTextAlert = event => spoken.push(event.id);
  const message = (id, created_at) => ({id, created_at, type: "chat.message", network: {sender_hash: "other"}});
  context.incomingFeedInitialized = false;
  context.queueIncomingAlerts([], 100);
  context.queueIncomingAlerts([message("older batch", 50), message("join second", 100)], 100);
  context.queueIncomingAlerts([message("another batch", 80), message("new message", 101)], 100);
  context.queueIncomingAlerts([message("new message", 101)], 100);
  await context.incomingAlertQueue;
  assert.deepEqual(spoken, ["new message"]);
});

test("the archive waits for live playback and skips an already heard clip", async () => {
  let startPlayback;
  const {context, played, persisted, archive, finishStream} = playbackHarness(() => new Promise(resolve => { startPlayback = resolve; }));
  finishStream();
  context.queueIncomingAlerts([archive]);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(played, ["live"]);
  startPlayback();
  await context.incomingAlertQueue;
  assert.deepEqual(played, ["live"]);
  assert.ok(persisted.has("voice:clip-1"));
});

test("the saved recording still plays when live playback fails", async () => {
  const {context, played, archive, finishStream} = playbackHarness(async () => { throw new Error("NotAllowedError"); });
  finishStream();
  context.queueIncomingAlerts([archive]);
  await context.incomingAlertQueue;
  assert.deepEqual(played, ["live", "archive"]);
});

test("Command plays the preloaded live beep at full volume alongside voice", async () => {
  const order = [];
  const {context, played, archive, finishStream} = playbackHarness(async () => { order.push("voice"); }, [], (cue, volume, position) => {
    order.push(cue);
    assert.equal(volume, 1);
    assert.equal(position, 0);
    return new Promise(() => {});
  }, false);
  finishStream();
  context.queueIncomingAlerts([archive]);
  await context.incomingAlertQueue;
  assert.deepEqual(order, ["voice", "live beep"]);
  assert.deepEqual(played, ["live"]);
});

test("a clip heard on Android in the background also stays quiet in the WebView", async () => {
  const {context, played, archive, finishStream} = playbackHarness(async () => {}, ["voice:clip-1"]);
  finishStream();
  context.queueIncomingAlerts([archive, {...archive, clip_id: "another-clip", id: "another-event"}]);
  await context.incomingAlertQueue;
  assert.deepEqual(played, ["archive"]);
});
