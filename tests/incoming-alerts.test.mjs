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
