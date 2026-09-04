import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const html = readFileSync(new URL("../src/retium/static/index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/retium/static/app.js", import.meta.url), "utf8");

test("Field onboarding can create and host a team", () => {
  assert.match(html, /id="fieldTeamNameInput"/);
  assert.match(html, /id="fieldCreateTasksModule"/);
  assert.match(html, /id="fieldCreateTeam"[^>]*>CREATE \+ HOST TEAM/);
  assert.match(app, /fieldCreateTeam[\s\S]*?\/api\/team\/create/);
});

test("Field admin settings expose Command-grade team controls", () => {
  const userToggle = html.indexOf('id="userSettingsToggle"');
  const adminToggle = html.indexOf('id="adminSettingsToggle"');
  assert.ok(userToggle >= 0 && adminToggle > userToggle, "admin settings should sit beside user settings");

  for (const id of [
    "fieldToggleTasksModule",
    "fieldToggleEveryoneAdmin",
    "fieldClearMessageHistory",
    "adminCopyJoinCode",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(app, new RegExp(`\\$\\("${id}"\\)\\.addEventListener`));
  }
  assert.match(app, /fieldToggleTasksModule[\s\S]*?\/api\/team\/modules/);
  assert.match(app, /fieldToggleEveryoneAdmin[\s\S]*?\/api\/team\/permissions/);
  assert.match(app, /fieldClearMessageHistory[\s\S]*?\/api\/messages\/history/);
});
