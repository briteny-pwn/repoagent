export type TaskStatus = 'queued' | 'running' | 'waiting_confirmation' | 'waiting_network' | 'waiting_rate_limit' | 'completed' | 'failed' | 'cancelled';
export type DraftStatus = 'draft' | 'pending' | 'executing' | 'succeeded' | 'failed' | 'unknown' | 'discarded';
export type TaskKind = 'initialization' | 'analysis' | 'diagnostic' | 'subscription' | 'node';
export interface Event { id: string; kind: string; message: string; created_at: string }
export interface Draft { id: string; task_id: string; kind: 'issue' | 'comment'; repository: string; issue_number: number | null; title: string; body: string; version: number; status: DraftStatus; external_url: string | null; error: string | null }
export interface Task { project_id: string | null; id: string; kind: TaskKind; prompt: string; status: TaskStatus; repository: string | null; created_at: string; updated_at: string; error: string | null }
export interface TaskDetail extends Task { result: { report: string; evidence: { title: string; url: string }[]; limitations: string[] } | null; events: Event[]; drafts: Draft[] }
export interface Report { report: string; evidence: { title: string; url: string }[]; limitations: string[] }
export interface Snapshot { metadata: Record<string, unknown>; readme: Record<string, unknown> | null; files: Record<string, unknown>[]; directory: Record<string, unknown>[]; issues: { items: Record<string, unknown>[]; coverage: Record<string, unknown> }; pull_requests: { items: Record<string, unknown>[]; coverage: Record<string, unknown> }; captured_at: string; limitations: string[] }
export interface Project { id: string; repository: string; name: string; status: 'pending' | 'initializing' | 'ready' | 'failed' | 'waiting_rate_limit' | 'waiting_network'; retry_at?: number | null; initialization_task_id: string | null; initialized_at: string | null; created_at: string; updated_at: string; last_error: string | null }
export interface ProjectDetail extends Project { summary: Report | null; snapshot: Snapshot | null }
export interface Settings {
  model: { base_url: string; model: string; api_key_configured: boolean; max_steps: number };
  github: { token_configured: boolean; repository_url: string; repository: string | null };
  network: { mode: 'direct' | 'proxy' | 'auto'; proxy_url: string; proxy_configured: boolean };
  subscription: { configured: boolean; nodes: { name: string; type: string }[]; selected_node: string | null; updated_at: string | null; status: string };
  runtime: { worker_alive: boolean };
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.name = 'ApiError'; this.status = status; }
}

export class ApiClient {
  csrfToken = '';
  private fetcher: typeof fetch;
  constructor(fetcher: typeof fetch = (...args) => fetch(...args)) { this.fetcher = fetcher; }

  private async request<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
    const headers = new Headers();
    if (data !== undefined) headers.set('Content-Type', 'application/json');
    if (method !== 'GET' && path !== '/auth/login' && this.csrfToken) headers.set('X-CSRF-Token', this.csrfToken);
    let response: Response;
    try {
      response = await this.fetcher(`/api${path}`, { method, credentials: 'include', headers, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
    } catch {
      throw new ApiError('无法连接服务器，请检查网络后重试。', 0);
    }
    let body: unknown = null;
    try { body = await response.json(); } catch { /* Empty response or unavailable JSON. */ }
    if (!response.ok) {
      if (response.status === 401) this.csrfToken = '';
      const detail = body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`;
      throw new ApiError(detail, response.status);
    }
    return body as T;
  }

  async login(password: string) {
    const data = await this.request<{ authenticated: true; csrf_token: string }>('/auth/login', 'POST', { password });
    this.csrfToken = data.csrf_token;
    return data;
  }
  async session() {
    try {
      const data = await this.request<{ authenticated: true; csrf_token: string }>('/auth/session');
      this.csrfToken = data.csrf_token;
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return false;
      throw error;
    }
  }
  async logout() { const result = await this.request<{ ok: true }>('/auth/logout', 'POST'); this.csrfToken = ''; return result; }
  health() { return this.request<{ status: string; worker_alive: boolean }>('/health'); }
  settings() { return this.request<Settings>('/settings'); }
  updateModel(values: { base_url: string; model: string; api_key?: string; max_steps?: number }) { return this.request<Settings>('/settings/model', 'PUT', values); }
  testModel(values: { base_url?: string; model?: string; api_key?: string }) { return this.request<{ ok: boolean; message: string }>('/settings/model/test', 'POST', values); }
  updateGithub(values: { token?: string; repository_url?: string }) { return this.request<Settings>('/settings/github', 'PUT', values); }
  validateRepository(url?: string) { return this.request<{ full_name: string; html_url: string; description: string | null; private: false; default_branch: string }>('/repositories/validate', 'POST', url ? { url } : {}); }
  updateNetwork(values: { mode: 'direct' | 'proxy' | 'auto'; proxy_url?: string }) { return this.request<Settings>('/settings/network', 'PUT', values); }
  testNetwork() { return this.request<{ id: string; status: 'queued' }>('/settings/network/test', 'POST'); }
  updateSubscription(url: string) { return this.request<Settings>('/settings/subscription', 'PUT', { url }); }
  refreshSubscription() { return this.request<Settings>('/settings/subscription/refresh', 'POST'); }
  selectNode(name: string) { return this.request<{ id: string; status: 'queued' }>('/settings/subscription/node', 'PUT', { name }); }
  deleteCredential(kind: 'model' | 'github' | 'proxy' | 'subscription') { return this.request<Settings>(`/settings/credentials/${kind}`, 'DELETE'); }
  projects() { return this.request<Project[]>('/projects'); }
  createProject(repository_url: string) { return this.request<ProjectDetail>('/projects', 'POST', { repository_url }); }
  project(id: string) { return this.request<ProjectDetail>(`/projects/${encodeURIComponent(id)}`); }
  initializeProject(id: string) { return this.request<{ id: string; status: TaskStatus }>(`/projects/${encodeURIComponent(id)}/initialize`, 'POST'); }
  projectTasks(id: string) { return this.request<Task[]>(`/projects/${encodeURIComponent(id)}/tasks`); }
  createProjectTask(id: string, prompt: string, request_id: string) { return this.request<{ id: string; status: TaskStatus }>(`/projects/${encodeURIComponent(id)}/tasks`, 'POST', { prompt, request_id }); }
  tasks() { return this.request<Task[]>('/tasks'); }
  createTask(prompt: string, request_id: string) { return this.request<{ id: string; status: TaskStatus }>('/tasks', 'POST', { prompt, request_id }); }
  task(id: string) { return this.request<TaskDetail>(`/tasks/${encodeURIComponent(id)}`); }
  cancelTask(id: string) { return this.request<{ ok: true }>(`/tasks/${encodeURIComponent(id)}/cancel`, 'POST'); }
  resumeTask(id: string) { return this.request<{ ok: true }>(`/tasks/${encodeURIComponent(id)}/resume`, 'POST'); }
  editDraft(id: string, version: number, title: string, body: string) { return this.request<Draft>(`/drafts/${encodeURIComponent(id)}`, 'PATCH', { version, title, body }); }
  confirmDraft(id: string, version: number) { return this.request<Draft>(`/drafts/${encodeURIComponent(id)}/confirm`, 'POST', { version }); }
  discardDraft(id: string, version: number) { return this.request<Draft>(`/drafts/${encodeURIComponent(id)}/discard`, 'POST', { version }); }
}
