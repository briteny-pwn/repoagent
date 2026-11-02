import { useState, type ComponentType } from 'react';
import type { Snapshot } from './api';
import { prStatus } from './prStatus';
import { referencesIssue } from './overviewViews';

function Source({url, children}: {url: unknown; children: React.ReactNode}) {
  try { const parsed = new URL(String(url));
    if(parsed.protocol === 'https:' && parsed.hostname === 'github.com' && !parsed.username && !parsed.password)
      return <a href={parsed.href} target="_blank" rel="noreferrer">{children} ↗</a>;
  } catch { /* Untrusted links remain text. */ }
  return <span>{children}</span>;
}
export default function OverviewRecords({snapshot, repository, Markdown}: {
 snapshot: Snapshot; repository: string; Markdown: ComponentType<{text:string}>;
}) {
 const [query,setQuery]=useState('');
 const [state,setState]=useState('all');
 return <div className="overview-records">
  <p className="overview-caption">采集时间：{snapshot.captured_at}。以下为保存的快照，非实时状态；正文引用不等同于 GitHub 正式关联。</p>
  <div className="overview-filters"><input aria-label="搜索 Issue 或 PR" placeholder="搜索编号、标题或正文" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="筛选状态" value={state} onChange={e=>setState(e.target.value)}><option value="all">全部状态</option><option value="open">开放</option><option value="closed">已关闭（PR 未合并）</option><option value="merged">已合并 PR</option><option value="draft">草稿 PR</option><option value="unknown">PR 合并状态未采集</option></select></div>
  {([['Issues',snapshot.issues],['Pull Requests',snapshot.pull_requests]] as const).map(([label,collection]) => {
    const items=collection.items.filter(item=>(state==='all'||(label==='Pull Requests'?prStatus(item).key===state:item.state===state)) && `${item.number} ${item.title} ${item.body}`.toLowerCase().includes(query.toLowerCase()));
    return <section key={label}><h3>{label} · {items.length} / {collection.items.length}</h3>
     {(collection.coverage.truncated || collection.coverage.text_truncated) ? <p className="overview-caption">采集列表或正文有截断，详情可能不完整。</p> : null}
     {!items.length && <p className="empty-inline">{collection.coverage.available===false ? '仓库未启用 Issue。' : collection.items.length ? '没有符合筛选条件的记录。' : '快照未采集到记录。'}</p>}
     {items.map((item,index)=>{
       const related=label==='Issues' ? snapshot.pull_requests.items.filter(pr=>referencesIssue(pr.body,Number(item.number),repository)) : [];
       return <article className="overview-record" key={String(item.number ?? index)}>
        <div className="overview-record-heading"><h4><Source url={item.html_url}>#{String(item.number)} {String(item.title || '无标题')}</Source></h4><span className={'record-state '+(item.state==='open'?'is-open':'')}>{label==='Pull Requests'?prStatus(item).label:item.state==='open'?'开放':item.state==='closed'?'已关闭':'状态未采集'}</span></div>
        <p className="overview-caption">更新时间：{String(item.updated_at || '未采集')}{label==='Pull Requests' && <>{item.merged_at ? ` · 合并时间：${String(item.merged_at)}` : item.closed_at ? ` · 关闭时间：${String(item.closed_at)}` : ''}{item.closed_by ? ` · 关闭者：${String(item.closed_by)}` : ''}{prStatus(item).key==='unknown' ? ' · 请刷新项目资料补充状态' : ''}</>}</p>
        <Markdown text={typeof item.body==='string' && item.body ? item.body : '未提供正文。'}/>
        {label==='Issues' && <div className="overview-related"><strong>关联 PR</strong>{related.length ? <><p>以下已采集 PR 的正文引用了此 Issue，正式关联尚未核实：</p>{related.map(pr=><p key={String(pr.number)}><Source url={pr.html_url}>#{String(pr.number)} {String(pr.title)}</Source> · {prStatus(pr).label}</p>)}</> : <p>正式关联未采集；已保存的 PR 正文中未找到此 Issue 的引用。</p>}</div>}
       </article>;
     })}
    </section>;
  })}
 </div>;
}
