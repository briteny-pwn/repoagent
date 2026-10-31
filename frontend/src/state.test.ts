import assert from 'node:assert/strict';
import test from 'node:test';
import { reconcileForm, shouldRefreshSettingsForTask } from './state.ts';

test('background settings updates preserve a form with unsaved edits', () => {
  const editing = { base_url: 'https://new.example/v1', model: 'new-model', api_key: 'unsaved-key' };
  const saved = { base_url: 'https://old.example/v1', model: 'old-model', api_key: '' };
  assert.equal(reconcileForm(editing, saved, true), editing);
  assert.deepEqual(reconcileForm(editing, saved, false), saved);
});

test('completed subscription and node jobs refresh settings once', () => {
  assert.equal(shouldRefreshSettingsForTask(undefined, { kind: 'subscription', status: 'completed' }), true);
  assert.equal(shouldRefreshSettingsForTask('running', { kind: 'node', status: 'completed' }), true);
  assert.equal(shouldRefreshSettingsForTask('completed', { kind: 'node', status: 'completed' }), false);
  assert.equal(shouldRefreshSettingsForTask('running', { kind: 'analysis', status: 'completed' }), false);
  assert.equal(shouldRefreshSettingsForTask('queued', { kind: 'subscription', status: 'running' }), false);
});
