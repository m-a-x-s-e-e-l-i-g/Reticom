import test from 'node:test';
import assert from 'node:assert/strict';
import {
  HARDWARE_PTT_CODES,
  isEditablePttTarget,
  isHardwarePttKeyboardEvent,
} from '../src/retium/static/ptt-input.js';

test('dedicated keyboard and HID function keys are recognised as PTT', () => {
  assert.ok(HARDWARE_PTT_CODES.includes('F9'));
  assert.ok(HARDWARE_PTT_CODES.includes('F13'));
  assert.equal(isHardwarePttKeyboardEvent({type: 'keydown', code: 'F13'}), true);
  assert.equal(isHardwarePttKeyboardEvent({type: 'keyup', code: 'F13'}), true);
});

test('PTT never steals typing or modified shortcuts', () => {
  assert.equal(isEditablePttTarget({tagName: 'textarea'}), true);
  assert.equal(isHardwarePttKeyboardEvent({type: 'keydown', code: 'F9'}, {tagName: 'INPUT'}), false);
  assert.equal(isHardwarePttKeyboardEvent({type: 'keydown', code: 'F9', ctrlKey: true}), false);
  assert.equal(isHardwarePttKeyboardEvent({type: 'keydown', code: 'Space'}), false);
});
