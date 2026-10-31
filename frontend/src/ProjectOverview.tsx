import type { ComponentType } from 'react';
import { BookOpen, FileText, GitPullRequest, ListTodo, ArrowRight } from 'lucide-react';
import type { ProjectDetail } from './api';
import { reportSections } from './reportSections';

type Tab = 'summary' | 'files' | 'issues' | 'prs' | 'commands';
export default function ProjectOverview({ project, Markdown, navigate }: {
  project: ProjectDetail; Markdown: ComponentType<{text: string}>; navigate: (tab: Tab) => void;
}) {
  const snapshot = project.snapshot;
  const sections = reportSections(project.summary?.report || '');
  const limitations = [...new Set([...(project.summary?.limitations || []), ...(snapshot?.limitations || [])])];
  const files = (snapshot?.files.length || 0) + (snapshot?.readme ? 1 : 0);
  const stats = [
    { label: '已采集文件', value: files, icon: FileText, tab: 'files' as const },
    { label: '已采集 Issues', value: snapshot?.issues.items.length ?? 0, icon: ListTodo, tab: 'issues' as const },
    { label: '已采集 PR', value: snapshot?.pull_requests.items.length ?? 0, icon: GitPullRequest, tab: 'prs' as const },
  ];
  return <div className="overview-dashboard">
    {snapshot && <><div className="overview-description"><BookOpen size={18}/><span>{typeof snapshot.metadata.description === 'string' && snapshot.metadata.description ? snapshot.metadata.description : '仓库资料已保存，可基于本次快照继续分析。'}</span></div>
    <div className="overview-stats">{stats.map(({label,value,icon:Icon,tab}) => <button key={tab} onClick={() => navigate(tab)}><Icon size={19}/><span><strong>{value}</strong><small>{label}</small></span><ArrowRight size={16}/></button>)}</div><p className="overview-caption">以上为本次快照的采集数量，可能受分页和采集范围限制。</p></>}
    <div className="overview-section-title"><div><span className="eyebrow">AI OVERVIEW</span><h3>初始化分析</h3><p>按主题阅读报告；事实、推断与建议保留模型原有标注。</p></div><button className="button secondary" onClick={() => navigate('commands')}>继续分析 <ArrowRight size={15}/></button></div>
    {!sections.length ? <div className="empty-inline">初始化完成后，这里会按主题展示项目分析。</div> : <>
      <nav className="report-index" aria-label="报告目录">{sections.map((section,index) => <a key={index} href={'#report-section-'+index}><span>{String(index+1).padStart(2,'0')}</span>{section.title}</a>)}</nav>
      <div className="report-card-grid">{sections.map((section,index) => <section className="report-card" id={'report-section-'+index} key={index}><div className="report-card-heading"><span>{String(index+1).padStart(2,'0')}</span><h3>{section.title}</h3></div><Markdown text={section.body}/></section>)}</div>
    </>}
    {(project.summary?.evidence.length || limitations.length) ? <details className="analysis-basis"><summary>分析依据与覆盖范围 <span>{project.summary?.evidence.length || 0} 个引用 · {limitations.length} 条说明</span></summary><div className="analysis-basis-body">{project.summary?.evidence.map((item,index) => {
      let safe=false;try { const url=new URL(item.url);safe=url.protocol==='https:' && url.hostname==='github.com' && !url.username && !url.password; } catch { /* Render untrusted URL as text. */ }
      return <p key={index}>{safe ? <a href={item.url} target="_blank" rel="noreferrer">{item.title} ↗</a> : item.title}</p>;
    })}<ul>{limitations.map((item,index) => <li key={index}>{item}</li>)}</ul></div></details> : null}
  </div>;
}
