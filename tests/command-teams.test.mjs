import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {teamApiUrl, teamPageUrl} from '../src/retium/static/command-teams.js';

test('Command position is scoped to the selected team, including when default is stopped', () => {
  assert.equal(teamApiUrl('/api/command/position', 'http://localhost:8780/?team=alpha'),
    'http://localhost:8780/api/command/position?team=alpha');
});

test('each tab scopes HTTP, audio, QR and websocket traffic to its selected team', () => {
  for (const path of ['/api/state', '/api/send', '/api/audio/clip', '/api/team/qr?v=1', 'ws://localhost:8780/api/voice/live', 'ws://localhost:8780/api/live']) {
    assert.equal(new URL(teamApiUrl(path, 'http://localhost:8780/?team=alpha')).searchParams.get('team'), 'alpha');
    assert.equal(new URL(teamApiUrl(path, 'http://localhost:8780/?team=bravo')).searchParams.get('team'), 'bravo');
  }
});
test('Field, legacy Command, external routes and workspace management remain unscoped', () => {
  assert.equal(teamApiUrl('/api/state', 'http://localhost:8780/'), 'http://localhost:8780/api/state');
  assert.equal(teamApiUrl('/api/command/teams', 'http://localhost:8780/?team=a'), 'http://localhost:8780/api/command/teams');
  assert.equal(teamApiUrl('https://maps.example/api/route', 'http://localhost:8780/?team=a'), 'https://maps.example/api/route');
});
test('switching team preserves unrelated app flags', () => {
  assert.equal(teamPageUrl('bravo', 'http://localhost:8780/?live-ptt=1&team=alpha'), 'http://localhost:8780/?live-ptt=1&team=bravo');
});

test('Command exposes code entry, real discovery and recoverable local removal', () => {
  const html = readFileSync(new URL('../src/retium/static/index.html', import.meta.url), 'utf8');
  const js = readFileSync(new URL('../src/retium/static/app.js', import.meta.url), 'utf8');
  const server = readFileSync(new URL('../src/retium/server.py', import.meta.url), 'utf8');
  for (const id of ['commandTeamJoinForm', 'commandJoinCodeInput', 'commandNearbyTeams', 'removedCommandTeamsList']) assert.ok(html.includes(`id="${id}"`));
  for (const endpoint of ['/api/command/join', '/api/command/nearby', '/remove', '/restore']) assert.ok(js.includes(endpoint));
  assert.ok(js.includes('Other devices keep their copies.'));
  assert.ok(js.includes('currentTeamAdmin = role === "gateway" || team.admin === true || currentTeamHosted'));
  assert.ok(js.includes('role = state.role;')); // Command layout must not grant gateway privileges.
  assert.ok(js.includes('if (commandDisplay()) renderJoinedCommand(fieldEvents)'));
  assert.ok(js.includes('IN WORKSPACE · SYNC ACTIVE'));
  assert.ok(js.includes('OPEN TEAM →'));
  assert.ok(html.includes('MANAGE OPERATORS'));
  assert.ok(html.includes('With <em>Everyone is admin</em> enabled'));
  assert.ok(server.includes('request_membership_admin'));
});
