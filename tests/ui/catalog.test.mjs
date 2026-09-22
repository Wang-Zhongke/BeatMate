import test from 'node:test';
import assert from 'node:assert/strict';
import { createCatalogLoader } from '../../beatmate/ui/catalog.js';

test('late responses cannot replace the newest search',async()=>{
    const waiting=new Map(),applied=[];
    const load=createCatalogLoader(q=>new Promise(resolve=>waiting.set(q.search,resolve)),p=>applied.push(p));
    const old=load({search:'old'}),latest=load({search:'new'});
    await Promise.resolve();
    waiting.get('new')('new results');await latest;
    waiting.get('old')('old results');await old;
    assert.deepEqual(applied,['new results']);
});
test('polling coalesces identical in-flight page requests and recovers after errors',async()=>{
    let resolve,calls=0;
    const load=createCatalogLoader(()=>{calls++;return new Promise(r=>resolve=r)},()=>{});
    const a=load({offset:0}),b=load({offset:0});await Promise.resolve();resolve({});await Promise.all([a,b]);
    assert.equal(calls,1);
    const c=load({offset:0});await Promise.resolve();resolve({});await c;assert.equal(calls,2);
    let fail=true;
    const recover=createCatalogLoader(()=>{if(fail)throw Error('offline');return 'ok'},()=>{});
    await assert.rejects(recover({}),/offline/);fail=false;await recover({});
});
test('a failed obsolete search does not surface an error over the current page',async()=>{
    let rejectOld;
    const applied=[];
    const load=createCatalogLoader(q=>q.search==='old'?new Promise((_,reject)=>rejectOld=reject):'current',p=>applied.push(p));
    const old=load({search:'old'});await Promise.resolve();
    await load({search:'new'});rejectOld(Error('old request failed'));await old;
    assert.deepEqual(applied,['current']);
});
