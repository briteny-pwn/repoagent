import { useCallback, useEffect, useRef, useState, type ComponentType, type FormEvent, type ReactNode } from 'react';
import ProjectOverview from './ProjectOverview';
import { createPortal } from 'react-dom';
import { ArrowLeft, GitBranch, LoaderCircle, Plus, RefreshCw, Send } from 'lucide-react';
import { ApiClient, type Project, type ProjectDetail, type Task, type TaskDetail } from './api';

export type Run = <T,>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
export type TaskPanelProps = { detail: TaskDetail | null; loading: boolean; detailError: string | null; refresh: () => Promise<void>; run: Run };
const projectStatuses = { pending: '等待初始化', initializing: '初始化中', ready: '已就绪', failed: '初始化失败', waiting_rate_limit: '等待限流恢复', waiting_network: '等待网络恢复' };
const taskStatuses = { queued: '排队中', running: '执行中', waiting_confirmation: '待确认', waiting_network: '等待网络', waiting_rate_limit: '等待限流恢复', completed: '已完成', failed: '失败', cancelled: '已取消' };
function text(value: unknown) { return typeof value === 'string' || typeof value === 'number' ? String(value) : ''; }
function Link({ url, children }: { url: unknown; children: ReactNode }) {
  let safe: string | null = null;
  try { const parsed = new URL(text(url)); if (parsed.protocol === 'https:' && parsed.hostname === 'github.com' && !parsed.username && !parsed.password) safe = parsed.href; } catch { /* Untrusted snapshot URL. */ }
  return safe ? <a href={safe} target="_blank" rel="noreferrer">{children} ↗</a> : <span>{children}</span>;
}
function SnapshotItems({ items, empty }: { items: Record<string, unknown>[]; empty: string }) {
  return items.length ? <div className="snapshot-items">{items.map((item, i) => <article key={text(item.number) || text(item.path) || i}><strong><Link url={item.html_url ?? item.url}>{item.number ? `#${text(item.number)} ` : ''}{text(item.title) || text(item.path) || text(item.name) || '未命名记录'}</Link></strong>{!!item.state && <span className="muted small"> · {text(item.state)}</span>}{typeof item.text === 'string' && <details><summary>查看保存的内容</summary><pre className="snapshot-source">{item.text}</pre></details>}{typeof item.body === 'string' && <details><summary>查看正文</summary><p className="snapshot-excerpt">{item.body}</p></details>}</article>)}</div> : <div className="empty-inline">{empty}</div>;
}

export default function ProjectWorkspace({ api, run, checkConfig, TaskPanel, Markdown, onSelect, active }: { onSelect: () => void; active: boolean; api: ApiClient; run: Run; checkConfig: () => Promise<boolean>; TaskPanel: ComponentType<TaskPanelProps>; Markdown: ComponentType<{ text: string }> }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [repository, setRepository] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const result = await run(() => api.projects()); setError(!result); if (result) setProjects(result); setLoading(false); }, [api, run]);
  useEffect(() => { void load(); const timer = window.setInterval(load, 5000); return () => window.clearInterval(timer); }, [load]);
  const importProject = async (event: FormEvent) => { event.preventDefault(); if (busy) return; setBusy(true); try { if (!await checkConfig()) return; const result = await run(() => api.createProject(repository.trim()), '项目已导入，正在自动初始化'); if (result) { setSelected(result.id); setRepository(''); await load(); } } finally { setBusy(false); } };
  const navigation = <><button className={active && !selected ? 'active' : ''} onClick={() => { setSelected(null); onSelect(); }}><Plus size={18} />导入项目</button><div className="workspace-projects" aria-label="项目列表">{loading ? <p className="muted small">正在读取项目…</p> : error ? <button onClick={load}>读取失败，重试</button> : !projects.length ? <p className="muted small">尚未导入项目</p> : projects.map(project => <button key={project.id} className={active && selected === project.id ? 'active' : ''} onClick={() => { setSelected(project.id); onSelect(); }} title={project.repository}><GitBranch size={17} /><span><strong>{project.repository}</strong><small>{projectStatuses[project.status]}</small></span></button>)}</div></>;
  const target = document.getElementById('workspace-project-navigation');
  return <div className="project-workspace">{target && createPortal(navigation, target)}{!selected ? <><div className="page-heading"><span className="eyebrow">YOUR PROJECTS</span><h1>导入仓库项目</h1><p>导入公开仓库，保存关键资料、Issue 与 PR，后续从左侧工作空间打开项目。</p></div><form className="project-import" onSubmit={importProject}><label htmlFor="project-url">GitHub 仓库地址</label><div><input id="project-url" type="url" placeholder="https://github.com/owner/repository" value={repository} onChange={e => setRepository(e.target.value)} required /><button className="button primary" disabled={busy || !repository.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}导入项目</button></div></form></> : <section className="project-content"><ProjectContent key={selected} id={selected} api={api} run={run} checkConfig={checkConfig} TaskPanel={TaskPanel} Markdown={Markdown} /></section>}</div>;
}

function ProjectContent({ id, api, run, checkConfig, TaskPanel, Markdown }: { id: string; api: ApiClient; run: Run; checkConfig: () => Promise<boolean>; TaskPanel: ComponentType<TaskPanelProps>; Markdown: ComponentType<{ text: string }> }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [tab, setTab] = useState<'summary' | 'files' | 'issues' | 'prs' | 'commands'>('summary');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const alive = useRef(true);
  const taskRef = useRef(selectedTask); taskRef.current = selectedTask;
  const load = useCallback(async () => { const result = await run(() => Promise.all([api.project(id), api.projectTasks(id)])); if (!alive.current) return; setLoadError(!result); if (result) { setProject(result[0]); setTasks(result[1]); } }, [api, id, run]);
  const refreshTask = useCallback(async () => { const taskId = taskRef.current; if (!taskId) return; const result = await run(() => api.task(taskId)); if (!alive.current || taskRef.current !== taskId) return; if (result && result.project_id === id) { setDetail(result); setDetailError(null); } else setDetailError('任务详情读取失败，请重试。'); }, [api, id, run]);
  useEffect(() => { alive.current = true; void load(); const timer = window.setInterval(() => { void load(); void refreshTask(); }, 3000); return () => { alive.current = false; window.clearInterval(timer); }; }, [load, refreshTask]);
  useEffect(() => { setDetail(null); setDetailError(null); if (selectedTask) void refreshTask(); }, [selectedTask, refreshTask]);
  const initialize = async () => { setBusy(true); try { if (!await checkConfig()) return; const result = await run(() => api.initializeProject(id), '已请求初始化项目资料'); if (result && alive.current) { setSelectedTask(result.id); await load(); } } finally { if (alive.current) setBusy(false); } };
  const create = async (event: FormEvent) => { event.preventDefault(); if (busy || !prompt.trim()) return; setBusy(true); try { if (!await checkConfig()) return; const result = await run(() => api.createProjectTask(id, prompt.trim(), crypto.randomUUID()), '项目指令已创建'); if (result && alive.current) { setPrompt(''); setSelectedTask(result.id); await load(); } } finally { if (alive.current) setBusy(false); } };
  if (!project) return <div className="panel-state">{loadError ? <><p>读取项目失败</p><button className="button secondary" onClick={load}>重试加载</button></> : <><LoaderCircle className="spin" /><p>正在读取项目…</p></>}</div>;
  const snapshot = project.snapshot;
  return <><div className="project-header"><div><span className="eyebrow">{projectStatuses[project.status]}</span><h2>{project.repository}</h2><p className="muted small">{snapshot ? `资料采集于 ${new Date(snapshot.captured_at).toLocaleString('zh-CN')}` : '尚未保存项目资料'}</p></div><button className="button secondary" disabled={busy || project.status === 'initializing'} onClick={initialize}><RefreshCw size={15} />{snapshot ? '刷新项目资料' : '初始化项目'}</button></div>{loadError && <div role="alert" className="notice error">更新项目失败，显示上次读取结果。<button onClick={load} className="text-button">重试</button></div>}{project.last_error && <div className={project.status.startsWith('waiting_') ? 'inline-callout' : 'notice error'} role="status">{project.status === 'waiting_rate_limit' ? <>GitHub API 配额已用尽，后台将自动重试。{project.retry_at ? `预计恢复：${new Date(project.retry_at * 1000).toLocaleString('zh-CN')}。` : ''}未配置令牌时配额由出口 IP 共享；可在设置中配置 GitHub 令牌，再重新初始化。</> : project.last_error}</div>}{project.status !== 'ready' && <div className="inline-callout"><span>{project.status === 'initializing' ? '正在采集仓库资料并生成初始化报告。' : '请完成初始化后再发送项目指令。已有资料仍可查看。'}</span>{project.initialization_task_id && <button className="text-button" onClick={() => setSelectedTask(project.initialization_task_id)}>查看初始化任务</button>}</div>}
    {selectedTask ? <><button className="back-link" onClick={() => setSelectedTask(null)}><ArrowLeft size={16} />返回项目</button><TaskPanel detail={detail} loading={!detail && !detailError} detailError={detailError} refresh={refreshTask} run={run} /></> : <><nav className="project-tabs" aria-label="项目内容">{([['summary', '项目概览'], ['files', '仓库资料'], ['issues', 'Issues'], ['prs', 'Pull Requests'], ['commands', '项目指令']] as const).map(([value, label]) => <button key={value} className={tab === value ? 'selected' : ''} onClick={() => setTab(value)}>{label}</button>)}</nav>
      {tab === 'summary' && <ProjectOverview project={project} Markdown={Markdown} navigate={setTab}/>}
      {tab === 'files' && <div className="repository-materials"><div className="section-intro"><h3>仓库资料</h3><p>初始化时保存的文件与目录。展开查看原文，或通过链接访问 GitHub。</p></div>{snapshot ? <><h4>关键文件 <span className="count-badge">{snapshot.files.length + (snapshot.readme ? 1 : 0)}</span></h4><SnapshotItems items={[...(snapshot.readme ? [snapshot.readme] : []), ...snapshot.files]} empty="本次未采集到关键文件。"/><details className="directory-details"><summary>根目录 <span className="count-badge">{snapshot.directory.length}</span></summary><SnapshotItems items={snapshot.directory} empty="未采集到目录。"/></details></> : <div className="empty-inline">初始化完成后显示仓库资料。</div>}</div>}
      {(tab === 'issues' || tab === 'prs') && <SnapshotCollection key={tab} label={tab === 'issues' ? 'Issues' : 'Pull Requests'} data={(tab === 'issues' ? snapshot?.issues.items : snapshot?.pull_requests.items) ?? []} initialized={!!snapshot}/>}
      {tab === 'commands' && <><section className="composer-card"><h3>向这个项目发送指令</h3><p className="muted small">基于已保存资料继续分析；Issue 和评论草稿仍需逐条确认后发布。</p><form onSubmit={create}><label className="sr-only" htmlFor="project-prompt">项目指令</label><textarea id="project-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="例如：根据现有 Issue 总结优先事项，并起草一条改进建议…" rows={5} maxLength={4000} disabled={project.status !== 'ready'} /><div className="composer-footer"><span>{project.repository}</span><button className="button primary" disabled={busy || project.status !== 'ready' || !prompt.trim()}><Send size={16} />发送指令</button></div></form></section><h3>项目任务</h3>{tasks.length ? tasks.map(task => <button className="task-row" key={task.id} onClick={() => setSelectedTask(task.id)}><span className="task-row-main"><strong>{task.kind === 'initialization' ? '项目初始化' : task.prompt}</strong><small>{new Date(task.created_at).toLocaleString('zh-CN')}</small></span><span className={`status status-${task.status}`}>{taskStatuses[task.status]}</span></button>) : <div className="empty-inline">尚无项目任务。</div>}</>}
    </>}
  </>;
}


function SnapshotCollection({label,data,initialized}: {label:string;data:Record<string,unknown>[];initialized:boolean}) {
  const [query,setQuery]=useState('');
  const [state,setState]=useState('all');
  const items=data.filter(item => (state==='all' || item.state===state) && [item.number,item.title,item.body].some(value => text(value).toLowerCase().includes(query.toLowerCase())));
  return <div className="snapshot-collection"><div className="section-intro"><h3>{label} <span className="count-badge">{data.length}</span></h3><p>保存的快照，非实时列表。刷新项目资料可更新状态；closed 表示已关闭，不区分 PR 是否合并。</p></div><div className="snapshot-toolbar"><input aria-label="搜索记录" placeholder="搜索标题、编号或正文…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="筛选状态" value={state} onChange={e=>setState(e.target.value)}><option value="all">全部状态</option><option value="open">未关闭</option><option value="closed">已关闭</option></select></div><p className="overview-caption">显示 {items.length} / {data.length} 条已采集记录</p><SnapshotItems items={items} empty={!initialized ? '初始化完成后显示记录。' : data.length ? '没有符合筛选条件的记录。' : '本次快照未采集到记录。'}/></div>;
}
