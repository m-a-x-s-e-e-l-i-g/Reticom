import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";

test("admins see join requests before opening the panel and decisions update the indicator", async () => {
  const elements = new Map();
  const element = id => {
    const classes = new Set(["membershipPanel", "reviewMembershipRequests"].includes(id) ? ["hidden"] : []);
    if (!elements.has(id)) elements.set(id, {
      listeners: {}, innerHTML: "", textContent: "",
      classList: {remove: name => classes.delete(name), add: name => classes.add(name),
        toggle: (name, show) => show ? classes.add(name) : classes.delete(name), contains: name => classes.has(name)},
      replaceChildren() { this.innerHTML = ""; },
      addEventListener(name, fn) { this.listeners[name] = fn; }, focus() {},
    });
    return elements.get(id);
  };
  const requests = [], confirmations = [];
  let refreshed = 0, allow = false, hasPending = false;
  const pending = {identity: "c".repeat(32), callsign: "New Command", command: true};
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [element("open"), element("reviewMembershipRequests")], addEventListener() {}},
    window: {confirm: text => { confirmations.push(text); return allow; }},
    setInterval: () => 1, clearInterval() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({can_grant_admin: true, requests: hasPending && !options.method ? [pending, {identity: "d".repeat(32), callsign: "New phone", previously_seen: true}] : [], members: options.method ? [] : [
        {identity: "a".repeat(32), callsign: "Phone", status: "approved"},
      ]})};
    },
  });
  vm.runInContext(readFileSync(new URL("../src/retium/static/membership-settings.js", import.meta.url), "utf8")
    .replaceAll("export function", "function"), context);
  const settings = context.initMembershipSettings({apiUrl: path => path, escapeHtml: text => text, toast() {}, onChange: () => { refreshed++; }});
  const review = element("reviewMembershipRequests");
  settings.update({}, "b".repeat(32), false);
  assert.equal(requests.length, 0);
  settings.update({destination: "team"}, "b".repeat(32), true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(review.classList.contains("hidden"), true);
  hasPending = true;
  await settings.refresh();
  assert.equal(review.textContent, "1 join request to review");
  assert.equal(review.classList.contains("hidden"), false);
  assert.equal(element("membershipList").innerHTML, "");
  review.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(element("membershipList").innerHTML, /Remove operator/);
  assert.match(element("membershipList").innerHTML, /data-admin="true">Approve as Command admin/);
  assert.match(element("membershipList").innerHTML, /New phone/);
  const button = {dataset: {member: "a".repeat(32), status: "removed", callsign: "Phone"}};
  const click = () => element("membershipList").listeners.click({target: {closest: () => button}});
  await click();
  assert.equal(requests.length, 3, "cancelling must not remove anything");
  allow = true;
  await click();
  assert.match(confirmations.at(-1), /Remove Phone/);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {identity: "a".repeat(32), status: "removed", callsign: "Phone"});
  assert.equal(refreshed, 1);
  assert.doesNotMatch(element("membershipList").innerHTML, /Phone/);
  assert.equal(review.classList.contains("hidden"), true);
});
