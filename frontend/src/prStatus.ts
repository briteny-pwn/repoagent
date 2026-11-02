export function prStatus(item: Record<string, unknown>): { key: string; label: string } {
  if (typeof item.merged_at === 'string' && item.merged_at) return {key:'merged',label:'已合并'};
  if (item.state === 'open') return item.draft === true ? {key:'draft',label:'草稿'} : {key:'open',label:'开放'};
  if (item.state === 'closed') {
    if (!Object.hasOwn(item,'merged_at') || item.merged_at !== null) return {key:'unknown',label:'已关闭 · 合并状态未采集'};
    if (typeof item.closed_by === 'string' && item.closed_by && typeof item.author === 'string' && item.author) return {key:'closed',label:item.closed_by === item.author ? '作者自行关闭 · 未合并' : '他人关闭 · 未合并'};
    return {key:'closed',label:'已关闭 · 未合并（操作者未知）'};
  }
  return {key:'unknown',label:'状态未采集'};
}
