import type { Settings } from './api';

type AnalysisSettings = {
  model: Pick<Settings['model'], 'model' | 'api_key_configured'>;
  github: Pick<Settings['github'], 'repository'>;
};

export function missingAnalysisSettings(settings: AnalysisSettings): string[] {
  const missing: string[] = [];
  if (!settings.model.model.trim()) missing.push('模型名称');
  if (!settings.model.api_key_configured) missing.push('模型 API Key');
  if (!settings.github.repository?.trim()) missing.push('GitHub 目标仓库');
  return missing;
}

export function missingModelSettings(settings: Pick<Settings, 'model'>): string[] {
  const missing: string[] = [];
  if (!settings.model.model.trim()) missing.push('模型名称');
  if (!settings.model.api_key_configured) missing.push('模型 API Key');
  return missing;
}
