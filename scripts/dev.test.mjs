import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

test('missing virtualenv gives first-time setup instructions without starting services', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'tfs-start-'));
  mkdirSync(path.join(root, 'scripts'));
  copyFileSync(new URL('./dev.mjs', import.meta.url), path.join(root, 'scripts/dev.mjs'));
  const result = spawnSync(process.execPath, [path.join(root, 'scripts/dev.mjs')], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Missing Python virtual environment.*README/);
});
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { writeFileSync, symlinkSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../', import.meta.url));
function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'tfs-start-'));
  mkdirSync(path.join(root, 'scripts'));
  copyFileSync(new URL('./dev.mjs', import.meta.url), path.join(root, 'scripts/dev.mjs'));
  symlinkSync(path.join(project, '.venv'), path.join(root, '.venv'), process.platform === 'win32' ? 'junction' : 'dir');
  return root;
}
function run(root) {
  const child = spawn(process.execPath, [path.join(root, 'scripts/dev.mjs')], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  let output = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', bytes => { output += bytes; });
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill(); reject(new Error(output || 'Launcher stalled')); }, 15000);
    child.on('error', reject);
    child.on('exit', code => { clearTimeout(timeout); resolve({ code, output }); });
  });
}
function frontend(root, source = 'process.exit(23)') {
  mkdirSync(path.join(root, 'frontend/node_modules/next/dist/bin'), { recursive: true });
  writeFileSync(path.join(root, 'frontend/node_modules/next/dist/bin/next'), source);
}

test('missing frontend dependencies explains npm ci', async () => {
  const result = await run(fixture());
  assert.equal(result.code, 1);
  assert.match(result.output, /Missing frontend dependencies.*npm --prefix frontend ci/);
});

test('an occupied backend port is reported and its owner survives', async () => {
  const owner = createServer();
  await new Promise((resolve, reject) => owner.once('error', reject).listen(8000, '127.0.0.1', resolve));
  try {
    const root = fixture(); frontend(root);
    const result = await run(root);
    assert.equal(result.code, 1);
    assert.match(result.output, /Port 8000.*unavailable/);
    assert.ok(owner.listening);
  } finally { await new Promise(resolve => owner.close(resolve)); }
});

test('an unexpected backend exit stops startup with a labeled error', async () => {
  const root = fixture(); frontend(root);
  writeFileSync(path.join(root, 'scripts/backend_dev.py'), 'raise SystemExit(17)');
  const result = await run(root);
  assert.equal(result.code, 1);
  assert.match(result.output, /\[backend\] Exited unexpectedly \(17\)/);
});

test('frontend failure shuts down the healthy backend it owns', async () => {
  const root = fixture(); frontend(root);
  copyFileSync(new URL('./backend_dev.py', import.meta.url), path.join(root, 'scripts/backend_dev.py'));
  mkdirSync(path.join(root, 'backend/analyst'), { recursive: true });
  writeFileSync(path.join(root, 'backend/analyst/__init__.py'), '');
  writeFileSync(path.join(root, 'backend/analyst/api.py'), 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/api/health")\ndef health(): return {"status":"ok"}\n');
  const result = await run(root);
  assert.equal(result.code, 1);
  assert.match(result.output, /\[backend\] Ready/);
  assert.match(result.output, /\[frontend\] Exited unexpectedly \(23\)/);
  assert.match(result.output, /Application shutdown complete/);
});
