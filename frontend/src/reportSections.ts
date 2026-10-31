export interface ReportSection { title: string; body: string }

// Split at the highest-level ATX headings present, preserving fenced code and
// subordinate headings. Reports without headings remain readable as one card.
export function reportSections(report: string): ReportSection[] {
  const lines = report.split('\n');
  let fence: string | null = null;
  let hasReferenceDefinitions = false;
  const headings: { index: number; level: number; title: string }[] = [];
  lines.forEach((line, index) => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
      return;
    }
    if (fence) return;
    if (/^ {0,3}\[[^\]]+\]:/.test(line)) hasReferenceDefinitions = true;
    const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*$/);
    if (heading) headings.push({ index, level: heading[1].length, title: heading[2].replace(/\s+#+\s*$/, '') });
  });
  // Reference definitions have document scope; keep these reports in one Markdown
  // document so definitions in another section (including multiline ones) resolve.
  if (hasReferenceDefinitions) return [{ title: '完整分析', body: report.trim() }];
  if (!headings.length) return report.trim() ? [{ title: '分析摘要', body: report.trim() }] : [];
  let level = Math.min(...headings.map(h => h.level));
  // A single document title should not swallow all subordinate sections.
  if (headings.filter(h => h.level === level).length === 1 && headings.some(h => h.level > level)) {
    level = Math.min(...headings.filter(h => h.level > level).map(h => h.level));
  }
  const selected = headings.filter(h => h.level === level);
  const sections: ReportSection[] = [];
  const intro = lines.slice(0, selected[0].index).join('\n').trim();
  if (intro) sections.push({ title: '报告导读', body: intro });
  selected.forEach((heading, index) => sections.push({
    title: heading.title,
    body: lines.slice(heading.index + 1, selected[index + 1]?.index ?? lines.length).join('\n').trim(),
  }));
  return sections;
}
