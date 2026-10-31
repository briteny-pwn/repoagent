import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reportSections } from './reportSections.ts';

test('keeps intro, subordinate headings and source text', () => {
  const sections = reportSections('Intro\n## Overview\nText\n### Detail\nMore\n## Priorities\nNext');
  assert.deepEqual(sections.map(s => s.title), ['报告导读', 'Overview', 'Priorities']);
  assert.equal(sections[1].body, 'Text\n### Detail\nMore');
});
test('ignores headings within fenced code', () => {
  const sections = reportSections('## Files\n```md\n# not a section\n```\n## Issues\nNone');
  assert.equal(sections.length, 2);
  assert.match(sections[0].body, /# not a section/);
});
test('supports unstructured reports and document titles', () => {
  assert.deepEqual(reportSections('plain report'), [{title:'分析摘要',body:'plain report'}]);
  assert.deepEqual(reportSections(''), []);
  assert.deepEqual(reportSections('# Report\n## Overview\nA\n## Risks\nB').map(s => s.title), ['报告导读','Overview','Risks']);
});

test('preserves meaningful hashes and document-scoped reference links', () => {
  assert.equal(reportSections('## C#\nLanguage')[0].title, 'C#');
  assert.equal(reportSections('## Topic ##\nText')[0].title, 'Topic');
  const report = '## A\nSee [source][repo]\n## B\n[repo]: https://github.com/a/b';
  assert.deepEqual(reportSections(report), [{title:'完整分析', body:report}]);
});
