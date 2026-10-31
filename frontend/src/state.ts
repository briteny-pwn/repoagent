import type { TaskKind, TaskStatus } from './api';

export function reconcileForm<T>(current: T, saved: T, hasUnsavedChanges: boolean): T {
  return hasUnsavedChanges ? current : saved;
}

export function shouldRefreshSettingsForTask(
  previous: TaskStatus | undefined,
  task: { kind: TaskKind; status: TaskStatus },
): boolean {
  return (task.kind === 'subscription' || task.kind === 'node') && task.status === 'completed' && previous !== 'completed';
}
