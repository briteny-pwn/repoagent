import { reportSections } from './reportSections.ts';

export const categories = ['仓库定位', '关键结构', 'Issue / PR 现状', '管理关注点'];
export function categoryReports(report: string): string[] {
  const groups: string[][] = [[], [], [], []];
  for (const section of reportSections(report)) {
    const title = section.title;
    const index = /issue|\bpr\b|pull request|议题|拉取请求/i.test(title) ? 2
      : /结构|目录|文件|架构/.test(title) ? 1
      : /关注|建议|风险|管理|后续|行动/.test(title) ? 3 : 0;
    groups[index].push(`### ${title}\n\n${section.body}`);
  }
  return groups.map(parts => parts.join('\n\n'));
}
export function compactReport(text: string): string {
  const plain = text.replace(/^#{1,6}\s+.*$/gm, '').replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[`*_>#]/g, '').replace(/^\s*[-+]\s+/gm, '').replace(/\s+/g, ' ').trim();
  return plain.length > 260 ? plain.slice(0, 260) + '…' : plain;
}
export function referencesIssue(body: unknown, number: number, repository: string): boolean {
  if (typeof body !== 'string') return false;
  // Exclude code examples and foreign repository references from local shorthand.
  const text = body.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`/g, '');
  const escaped = repository.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`https://github\\.com/${escaped}/issues/${number}(?![\\d/])(?:\\b)`).test(text)
    || new RegExp(`(?:^|[\\s(])${escaped}#${number}(?!\\d)`).test(text)
    || new RegExp(`(?:^|[\\s(])#${number}(?!\\d)`).test(text);
}
