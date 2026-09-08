import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";

test("remove operator confirms, posts the identity and refreshes the view", async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      listeners: {}, innerHTML: "", textContent: "",
      classList: {remove() {}, add() {}, toggle() {}, contains: () => false},
      addEventListener(name, fn) { this.listeners[name] = fn; }, focus() {},
    });
    return elements.get(id);
  };
  const requests = [], confirmations = [];
  let refreshed = 0, allow = false;
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [element("open")], addEventListener() {}},
    window: {confirm: text => { confirmations.push(text); return allow; }},
    setInterval: () => 1, clearInterval() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({requests: [], members: options.method ? [] : [
        {identity: "a".repeat(32), callsign: "Phone", status: "approved"},
      ]})};
    },
  });
  vm.runInContext(readFileSync(new URL("../src/retium/static/membership-settings.js", import.meta.url), "utf8")
    .replace("export function", "function"), context);
  const settings = context.initMembershipSettings({apiUrl: path => path, escapeHtml: text => text, toast() {}, onChange: () => { refreshed++; }});
  settings.update({}, "b".repeat(32));
  element("open").listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(element("membershipList").innerHTML, /REMOVE OPERATOR/);
  const button = {dataset: {member: "a".repeat(32), status: "removed", callsign: "Phone"}};
  const click = () => element("membershipList").listeners.click({target: {closest: () => button}});
  await click();
  assert.equal(requests.length, 1, "cancelling must not remove anything");
  allow = true;
  await click();
  assert.match(confirmations.at(-1), /Remove Phone/);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {identity: "a".repeat(32), status: "removed", callsign: "Phone"});
  assert.equal(refreshed, 1);
  assert.doesNotMatch(element("membershipList").innerHTML, /Phone/);
});
