import test from 'node:test';
import assert from 'node:assert/strict';
import { loopRange } from '../../beatmate/ui/listening.js';
import { clock } from '../../beatmate/ui/common.js';

test('loop range stays inside the currently loaded track', () => {
  assert.deepEqual(loopRange('1.25', '3.5', 4), { start: 1.25, end: 3.5 });
  for (const [start, end, duration] of [
    ['', '3', 4],
    ['1', '', 4],
    [-1, 3, 4],
    [3, 3, 4],
    [3, 2, 4],
    [1, 5, 4],
    [NaN, 3, 4],
    [0, Infinity, 4],
    [0, 4, 0],
  ]) {
    assert.equal(loopRange(start, end, duration), null);
  }
});
test('time labels handle unready metadata and long tracks', () => {
  assert.equal(clock(NaN), '0:00');
  assert.equal(clock(-1), '0:00');
  assert.equal(clock(81.5), '1:21');
  assert.equal(clock(3600), '60:00');
});

import { setupListening } from '../../beatmate/ui/listening.js';
function fixture(api = async () => []) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        value: '',
        disabled: false,
        textContent: '',
        title: '',
        classList: { toggle() {} },
        setAttribute() {},
        replaceChildren() {},
        append() {},
      });
    return nodes.get(id);
  };
  globalThis.document = {
    getElementById: node,
    createElement: () => ({ append() {}, setAttribute() {} }),
  };
  const events = {};
  let plays = 0;
  const audio = {
    currentTime: 2,
    duration: 4,
    paused: true,
    addEventListener: (name, fn) => (events[name] = fn),
    play: async () => {
      plays++;
    },
  };
  const asset = { id: 'track', available: true, duration_seconds: 4 };
  setupListening({
    api,
    audio,
    getWorks: () => [{ id: 'work', tracks: [asset] }],
    getCurrent: () => ({ asset, workId: 'work' }),
    playAsset() {},
    toast() {},
  });
  return { node, audio, events, plays: () => plays };
}
test('a loop ending at the file boundary restarts and changing a marker disables it', async () => {
  const f = fixture();
  f.node('loop-start').value = '1';
  f.node('loop-end').value = '4';
  f.node('loop-toggle').onclick();
  f.audio.currentTime = 4;
  await f.events.ended();
  assert.equal(f.audio.currentTime, 1);
  assert.equal(f.plays(), 1);
  f.audio.currentTime = 2;
  f.node('loop-start-now').onclick();
  f.audio.currentTime = 4;
  await f.events.ended();
  assert.equal(f.plays(), 1);
  delete globalThis.document;
});
test('rapid repeated save clicks persist a listening note only once', async () => {
  let resolve;
  let calls = 0;
  const f = fixture(() => {
    calls++;
    return new Promise((r) => (resolve = r));
  });
  f.node('note-text').value = 'Reduce drums here';
  const first = f.node('note-save').onclick();
  await f.node('note-save').onclick();
  assert.equal(calls, 1);
  resolve([]);
  await first;
  assert.equal(f.node('note-save').disabled, false);
  delete globalThis.document;
});
