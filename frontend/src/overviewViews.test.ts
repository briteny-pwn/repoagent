import { test } from 'node:test';
import assert from 'node:assert/strict';
import { categoryReports, compactReport, referencesIssue } from './overviewViews.ts';
test('four fixed categories preserve every report section', () => {
 const groups=categoryReports('## 仓库定位\n用途\n## 关键结构\n目录\n## Issue / PR 现状\n状态\n## 管理关注点\n风险\n## 其他\n补充');
 assert.equal(groups.length,4); assert.match(groups[0],/补充/); assert.match(groups[2],/状态/);
});
test('compact summary is a bounded plain paragraph', () => {
 assert.equal(compactReport('### 标题\n- **事实**\n- [链接](https://github.com)'), '事实 链接');
 assert.equal(compactReport('字'.repeat(300)).length,261);
});
test('references never infer from titles, foreign repositories, or code examples', () => {
 assert.ok(referencesIssue('Closes #6',6,'a/b'));
 assert.ok(referencesIssue('https://github.com/a/b/issues/6',6,'a/b'));
 assert.ok(referencesIssue('Fixes a/b#6',6,'a/b'));
 for(const body of ['Fixes #60','other/repo#6','https://github.com/other/repo/issues/6','`#6`']) assert.equal(referencesIssue(body,6,'a/b'),false);
});
