import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync, rmSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const windows = process.platform === 'win32';
const python = path.join(root, '.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const next = path.join(root, 'frontend/node_modules/next/dist/bin/next');
const children = [];
let stopping = false;
let stopDirectory;
let stopFile;
const log = (label, message) => console.log(`[${label}] ${message}`);

async function stop(code) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  log('dev', 'Stopping both services…');
  for (const child of children) {
    if (child.exitCode !== null) continue;
    if (child.label === 'backend') writeFileSync(stopFile, 'stop');
    else if (windows) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else { try { process.kill(-child.pid, 'SIGTERM'); } catch {} }
  }
  await Promise.all(children.map(child => new Promise(resolve => {
    if (child.exitCode !== null) return resolve();
    const timer = setTimeout(() => {
      if (windows) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      else { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }
      resolve();
    }, 5000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  })));
  if (stopDirectory) { rmSync(stopFile, { force: true }); rmdirSync(stopDirectory); }
}
process.on('SIGINT', () => void stop(0));
process.on('SIGTERM', () => void stop(0));

function start(label, executable, args, cwd) {
  const child = spawn(executable, args, {
    cwd, windowsHide: true, detached: !windows, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, BACKEND_URL: 'http://127.0.0.1:8000', PYTHONUNBUFFERED: '1' },
  });
  child.label = label;
  children.push(child);
  for (const stream of [child.stdout, child.stderr]) {
    createInterface({ input: stream }).on('line', line => log(label, line));
  }
  child.on('error', error => { console.error(`[${label}] Startup failed: ${error.message}`); void stop(1); });
  child.on('exit', (code, signal) => {
    if (!stopping) { console.error(`[${label}] Exited unexpectedly (${signal || code}).`); void stop(1); }
  });
  return child;
}
async function portAvailable(port, host) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error(`Port ${port} on ${host} is unavailable. Stop its owner or choose another session; no process was killed.`)));
    server.listen({ host, port, exclusive: true }, () => server.close(resolve));
  });
}
async function healthy(url, label) {
  const deadline = Date.now() + 90000;
  while (!stopping && Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok && (label !== 'backend' || (await response.json()).status === 'ok')) {
        log(label, `Ready: ${url}`);
        return;
      }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!stopping) throw new Error(`${label} did not become healthy within 90 seconds.`);
}
try {
  if (!existsSync(python)) throw new Error('Missing Python virtual environment. Follow first-time setup in README.md.');
  if (!existsSync(next)) throw new Error('Missing frontend dependencies. Run npm --prefix frontend ci; see README.md.');
  const check = spawnSync(python, ['-c', 'import fastapi, uvicorn, openai, pandas, numpy, dotenv, httpx, yfinance'], {
    cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'backend') }, encoding: 'utf8', windowsHide: true, timeout: 30000,
  });
  if (check.error) throw new Error(`Python could not start (${check.error.code}). Check OS permissions and the virtual environment; see README.md.`);
  if (check.status !== 0) throw new Error('Backend dependency/configuration check failed. Follow README.md; run .venv Python -m uvicorn analyst.api:app --app-dir backend to diagnose.');
  await portAvailable(8000, '127.0.0.1');
  await portAvailable(3000, '127.0.0.1');
  await portAvailable(3000, '::1');
  if (!stopping) {
    stopDirectory = mkdtempSync(path.join(tmpdir(), 'tfs-dev-'));
    stopFile = path.join(stopDirectory, 'stop');
    start('backend', python, [path.join(root, 'scripts/backend_dev.py'), stopFile], root);
    await healthy('http://127.0.0.1:8000/api/health', 'backend');
  }
  if (!stopping) {
    start('frontend', process.execPath, [next, 'dev', '--webpack', '--hostname', 'localhost', '--port', '3000'], path.join(root, 'frontend'));
    await healthy('http://localhost:3000', 'frontend');
    if (!stopping) log('dev', 'TFS ready at http://localhost:3000 — Ctrl+C stops both services.');
  }
} catch (error) { console.error(`[dev] ${error.message}`); await stop(1); }

