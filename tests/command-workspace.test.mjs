import test from 'node:test';
import assert from 'node:assert/strict';
import {readWorkspace, clampPanel} from '../src/retium/static/command-workspace.js';

test('Command defaults to only live Comms, not a dashboard of panels', () => {
  assert.deepEqual(readWorkspace(null), {open:['comms'],positions:{}});
  assert.deepEqual(readWorkspace('broken'), {open:['comms'],positions:{}});
});
test('saved layout accepts only known panels and finite positions', () => {
  assert.deepEqual(readWorkspace(JSON.stringify({open:['ops','ops','nope','layers'],positions:{ops:{x:15,y:22},comms:{x:'5',y:3},tasks:{x:-30,y:2}}})),
    {open:['ops'],positions:{ops:{x:15,y:22},tasks:{x:0,y:2}}});
  assert.deepEqual(readWorkspace('{"open":[]}').open,[]);
});
test('panels remain reachable after laptop window resizing', () => {
  assert.deepEqual(clampPanel({x:2000,y:2000},{width:1024,height:700},{width:336,height:470}),{x:680,y:146});
  assert.deepEqual(clampPanel({x:-50,y:-50},{width:360,height:600},{width:336,height:470}),{x:8,y:8});
});
