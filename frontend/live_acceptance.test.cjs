const { test } = require('node:test');
const assert = require('node:assert/strict');
const { bounded, acceptancePassed } = require('./run_live_acceptance.cjs');
test('whole-operation deadline bounds an unresolved promise', async () => {
  await assert.rejects(bounded(() => new Promise(() => {}), 20, 'stock'), /stock exceeded 20ms/);
});
test('deadline preserves failures and clears timers after completion', async () => {
  assert.equal(await bounded(async () => 42, 60000, 'review'), 42);
  await assert.rejects(bounded(async () => { throw new Error('validation'); }, 60000, 'review'), /validation/);
});
test('a timeout reaches finally cleanup even when the operation never resolves', async () => {
  let cleaned = false;
  await assert.rejects((async () => {
    try { await bounded(() => new Promise(() => {}), 20, 'rebalance'); }
    finally { cleaned = true; }
  })(), /rebalance exceeded/);
  assert.equal(cleaned, true);
});

test('acceptance requires all nine distinct cases inside the existing deadline', () => {
  const report = ['1', '2', '3a', '3b', '3c', '4', '5', '6', '7'].map(id =>
    ({ workflow: `${id} workflow`, status: 'PASS', duration_ms: 169999 }));
  assert.equal(acceptancePassed(report), true);
  assert.equal(acceptancePassed([]), false);
  assert.equal(acceptancePassed(report.slice(1)), false);
  assert.equal(acceptancePassed([...report.slice(1), report[1]]), false);
  assert.equal(acceptancePassed(report.map((r, i) => i ? r : { ...r, duration_ms: 170001 })), false);
  assert.equal(acceptancePassed(report.map((r, i) => i ? r : { ...r, status: 'FAIL' })), false);
});
