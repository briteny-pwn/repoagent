import assert from 'node:assert/strict';
import test from 'node:test';
import { missingAnalysisSettings, missingModelSettings } from './readiness.ts';
const ready = { model: { model: 'example', api_key_configured: true }, github: { repository: 'owner/repo' } };
test('lists all required analysis settings without requiring a GitHub token', () => {
  assert.deepEqual(missingAnalysisSettings(ready), []);
  assert.deepEqual(missingAnalysisSettings({model: {model: ' ', api_key_configured: false}, github: {repository: null}}), ['模型名称', '模型 API Key', 'GitHub 目标仓库']);
});
test('lists only missing configuration', () => {
  assert.deepEqual(missingAnalysisSettings({...ready, model: {...ready.model, api_key_configured: false}}), ['模型 API Key']);
});

test('project initialization requires model configuration but no global repository', () => {
  assert.deepEqual(missingModelSettings({ model: { base_url: 'https://example.com/v1', model: 'example', api_key_configured: true, max_steps: 6 } }), []);
  assert.deepEqual(missingModelSettings({ model: { base_url: '', model: ' ', api_key_configured: false, max_steps: 6 } }), ['模型名称', '模型 API Key']);
});
