import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prStatus } from './prStatus.ts';
test('PR lifecycle separates merge, draft, author closure and unknown legacy state',()=>{
 assert.equal(prStatus({state:'closed',merged_at:'2026-11-02'}).key,'merged');
 assert.equal(prStatus({state:'open',draft:true}).key,'draft');
 assert.equal(prStatus({state:'open',draft:false}).key,'open');
 assert.equal(prStatus({state:'closed'}).key,'unknown');
 assert.match(prStatus({state:'closed',merged_at:null,closed_by:'a',author:'a'}).label,/作者自行/);
 assert.match(prStatus({state:'closed',merged_at:null,closed_by:'b',author:'a'}).label,/他人/);
 assert.match(prStatus({state:'closed',merged_at:null}).label,/操作者未知/);
});
