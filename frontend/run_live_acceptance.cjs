const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function bounded(operation, ms, label) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} exceeded ${ms}ms`)), ms);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function acceptancePassed(report) {
  const ids = ['1', '2', '3a', '3b', '3c', '4', '5', '6', '7'];
  return report.length === ids.length && ids.every(id => report.some(r =>
    r.workflow.split(' ')[0] === id && r.status === 'PASS' && r.duration_ms <= 170_000));
}

async function run() {
  const url = process.env.ACCEPTANCE_URL || 'http://127.0.0.1:3001';
  const output = path.resolve(__dirname, '../.scratch', `live-${Date.now()}`);
  fs.mkdirSync(output, { recursive: true });
  const report = [], events = [];
  let browser, page, current, lastAction, lastRequest, lastHTTP, lastResult;
  let interrupt;
  const interrupted = new Promise((_, reject) => {
    interrupt = () => reject(new Error('Acceptance interrupted'));
  });
  interrupted.catch(() => {});
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);

  const readLog = () => {
    const logPath = process.env.ACCEPTANCE_BACKEND_LOG;
    if (!logPath || !fs.existsSync(logPath)) return '';
    try {
      const data = fs.readFileSync(logPath);
      return data.toString(data[0] === 0xff && data[1] === 0xfe ? 'utf16le' : 'utf8');
    } catch {
      return '';
    }
  };

  const persist = () => {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    fs.writeFileSync(path.join(output, 'events.json'), JSON.stringify(events, null, 2));
    fs.writeFileSync(path.join(output, 'state.json'), JSON.stringify({
      current,
      lastAction,
      lastRequest,
      lastHTTP,
      backend: readLog().slice(-16000),
    }, null, 2));
  };

  const hardStop = setTimeout(() => {
    persist();
    console.error('Hard acceptance deadline reached at', output);
    process.exit(1);
  }, 60 * 60_000);

  const only = (process.env.ACCEPTANCE_ONLY || '').split(',').map(s => s.trim()).filter(Boolean);

  async function workflow(name, operation, timeout = 170_000) {
    if (only.length && !only.includes(name.split(' ')[0])) {
      console.log(`SKIP ${name}`);
      return;
    }
    current = name;
    lastRequest = null;
    lastHTTP = null;
    const start = Date.now();
    const logOffset = readLog().length;
    const eventOffset = events.length;
    console.log(`\n================================================================`);
    console.log(`${new Date().toISOString()} START ${name}`);
    console.log(`================================================================`);

    let failure;
    try {
      await bounded(() => Promise.race([operation(), interrupted]), timeout, name);
    } catch (error) {
      failure = String(error);
      process.exitCode = 1;
      console.error(`FAILURE in ${name}: ${failure}`);
      persist();
      await bounded(async () => {
        if (page) {
          fs.writeFileSync(path.join(output, `${report.length}-state.html`), await page.content());
          await page.screenshot({ path: path.join(output, `${report.length}-failure.png`), fullPage: true, timeout: 5000 });
        }
      }, 7000, 'diagnostics').catch(console.error);
    }

    const backend = readLog().slice(logOffset);
    const tokenMatches = [...backend.matchAll(/usage=\(in=(\d+),\s*out=(\d+),\s*reasoning=(\d+)\)/g)];
    let inTokens = 0, outTokens = 0, reasoningTokens = 0;
    for (const m of tokenMatches) {
      inTokens += parseInt(m[1], 10) || 0;
      outTokens += parseInt(m[2], 10) || 0;
      reasoningTokens += parseInt(m[3], 10) || 0;
    }

    const entry = {
      workflow: name,
      status: failure ? 'FAIL' : 'PASS',
      duration_ms: Date.now() - start,
      failure,
      lastAction,
      lastRequest,
      http: lastHTTP,
      openai_turns: (backend.match(/MODEL TURN START/g) || []).length || (backend.match(/OPENAI RESPONSE/g) || []).length,
      tools: [...new Set([...backend.matchAll(/PIPELINE TOOL DISPATCH: name=([^,\s]+)/g)].map(m => m[1]))],
      tokens: { in: inTokens, out: outTokens, reasoning: reasoningTokens, total: inTokens + outTokens },
      corrections: [...backend.matchAll(/reprompting in loop|(?:Final|Review) answer rejected|reunderwrite_holding rejected|calculate_company_cases rejected/g)].map(m => m[0]),
      last_model_turn: backend.split(/\r?\n/).filter(line => /MODEL TURN|OPENAI RESPONSE/.test(line)).at(-1),
      backend_exceptions: backend.split(/\r?\n/).filter(line => /ERROR|Error:|InvalidReview|Traceback/.test(line)),
      browser_errors: events.slice(eventOffset).filter(event => ['error', 'pageerror', 'requestfailed'].includes(event.type)),
      backend_log: backend,
    };

    report.push(entry);
    persist();
    console.log(`${new Date().toISOString()} ${entry.status} ${name} ${entry.duration_ms}ms turns=${entry.openai_turns} tokens(in=${inTokens},out=${outTokens},reasoning=${reasoningTokens}) tools=${entry.tools.join(',')}`);
    if (failure && /exceeded|Timeout|interrupted/.test(failure)) {
      throw new Error(failure);
    }
  }

  try {
    browser = await bounded(
      () => chromium.launch({ channel: 'msedge', headless: true, timeout: 20000 }),
      25000,
      'launch'
    );
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    context.setDefaultTimeout(15000);
    context.setDefaultNavigationTimeout(30000);
    page = await context.newPage();
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });

    page.on('console', msg => events.push({ workflow: current, type: msg.type(), text: msg.text() }));
    page.on('pageerror', error => events.push({ workflow: current, type: 'pageerror', text: String(error) }));
    page.on('requestfailed', req => events.push({ workflow: current, type: 'requestfailed', url: req.url(), failure: req.failure() }));
    page.on('request', req => {
      if (new URL(req.url()).pathname === '/api/analyze') {
        lastRequest = { at: new Date().toISOString(), method: req.method(), url: req.url(), payload: req.postDataJSON() };
        persist();
      }
    });
    page.on('response', res => {
      if (new URL(res.url()).pathname.startsWith('/api/')) {
        events.push({ workflow: current, type: 'http', url: res.url(), status: res.status() });
      }
    });

    const askInput = () => page.locator('#ask');
    const submitBtn = () => page.getByRole('form', { name: 'Ask the analyst' }).locator('button[type="submit"]');

    async function sendQuestion(question) {
      lastAction = `submit question: ${question}`;
      await askInput().fill(question);
      if (/^\/\S+$/.test(question)) {
        await askInput().press('Enter');
      }
      const [res] = await Promise.all([
        page.waitForResponse(
          res => new URL(res.url()).pathname === '/api/analyze' && res.request().method() === 'POST',
          { timeout: 170000 }
        ),
        (async () => {
          const submit = submitBtn();
          try {
            await submit.waitFor({ state: 'visible', timeout: 2000 });
            await submit.click();
          } catch {
            await askInput().press('Enter');
          }
        })(),
      ]);
      const body = await res.text();
      lastHTTP = { status: res.status(), body };
      persist();
      assert.equal(res.status(), 200, `HTTP ${res.status()}: ${body}`);
      const result = JSON.parse(body);
      assert.equal(result.status, 'completed', `Expected completed result but got: ${result.status}`);
      lastResult = result;
      // Wait for memo rendering
      await expect(page.locator('main').locator('.memo').first()).toBeVisible({ timeout: 15000 });
      // Ensure failure row is not rendered
      await expect(page.locator('main').locator('.row.err')).not.toBeVisible();
      await page.screenshot({ path: path.join(output, `${report.length}-result.png`), fullPage: true, timeout: 5000 });
      return result;
    }

    // Verify backend is responsive and portfolio is loaded
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-testid="portfolio-date"]').first()).toBeVisible({ timeout: 15000 });
    const portfolio = await (await context.request.get(`${url}/api/portfolio`)).json();
    assert.deepEqual(portfolio.snapshot.positions.map(p => p.ticker).sort(), ['AVGO', 'CM', 'F', 'GOOG', 'HDB', 'IBN', 'INFY', 'NBIS', 'SHOP']);
    assert.ok(portfolio.benchmark, 'Set a benchmark before acceptance');
    assert.ok(!portfolio.settings || Object.entries(portfolio.settings).every(([key, value]) =>
      ['cash_is_deliberate_tilt', 'indirect_cap_policy'].includes(key) || value == null), 'Acceptance requires unset rules');
    console.log('App loaded successfully on Desk UI');

    // 1. Portfolio Review
    await workflow('1 Portfolio Review', async () => {
      const res = await sendQuestion('How concentrated is my portfolio across all accounts, and what exposures should I review?');
      assert.ok(res.recommendation, 'Expected recommendation object in Portfolio Review');
      await expect(page.locator('main').locator('.memo').first()).toBeVisible();
    });

    // 3a. Owned stock: US holding (AVGO)
    await workflow('3a Owned stock (US holding: AVGO)', async () => {
      const res = await sendQuestion('/stock AVGO');
      assert.ok(res.stock, 'Expected stock in AVGO analysis result');
      await expect(page.locator('main').locator('.memo').first()).toContainText(/AVGO/i);
    });

    // 3b. Owned stock: Foreign filer (HDB)
    await workflow('3b Owned stock (Foreign filer: HDB)', async () => {
      const res = await sendQuestion('/stock HDB');
      assert.ok(res.stock, 'Expected stock in HDB analysis result');
      await expect(page.locator('main').locator('.memo').first()).toContainText(/HDB/i);
    });

    // 3c. Owned stock: Canadian TSX (CM)
    await workflow('3c Owned stock (TSX: CM)', async () => {
      const res = await sendQuestion('/stock CM');
      assert.ok(res.stock, 'Expected stock in CM analysis result');
      await expect(page.locator('main').locator('.memo').first()).toContainText(/CM/i);
    });

    // 4. Unowned candidate (US: NVDA)
    await workflow('4 Unowned candidate (US: NVDA)', async () => {
      lastAction = 'submit question: /stock NVDA';
      const resPromise = page.waitForResponse(
        res => new URL(res.url()).pathname === '/api/analyze' && res.request().method() === 'POST',
        { timeout: 170000 }
      );
      await askInput().fill('/stock NVDA');
      await askInput().press('Enter');

      // NVDA matches US (XNAS) and Canadian CDR (XTSE); check if clarify choice appears
      const clarifyUsBtn = page.getByRole('button', { name: /NVDA \/ XNAS/i });
      try {
        await clarifyUsBtn.waitFor({ state: 'visible', timeout: 5000 });
      } catch {
        // Did not trigger multiple listings or already submitted
      }

      if (await clarifyUsBtn.isVisible()) {
        lastAction = 'select US listing NVDA / XNAS';
        await clarifyUsBtn.click();
      }

      const res = await resPromise;
      const body = await res.text();
      lastHTTP = { status: res.status(), body };
      persist();
      assert.equal(res.status(), 200, `HTTP ${res.status()}: ${body}`);
      const result = JSON.parse(body);
      assert.equal(result.status, 'completed', `Expected completed result but got: ${result.status}`);
      lastResult = result;
      assert.ok(result.stock, 'Expected stock in NVDA analysis result');

      await expect(page.locator('main').locator('.memo').first()).toBeVisible({ timeout: 15000 });
      await expect(page.locator('main').locator('.row.err')).not.toBeVisible();
      const memo = page.locator('main').locator('.memo').first();
      await expect(memo.getByTestId('stock-position')).toContainText(/You don't own NVDA today\./i);
      await page.screenshot({ path: path.join(output, `${report.length}-candidate-result.png`), fullPage: true, timeout: 5000 });
    });

    // 5. New Cash with an amount and loss tolerance
    await workflow('5 New Cash allocation', async () => {
      lastAction = 'open new cash form';
      await askInput().fill('/new-cash 5000');
      await submitBtn().click();
      await expect(page.getByLabel('Amount', { exact: true })).toBeVisible();

      lastAction = 'fill new cash inputs';
      await page.getByLabel('Loss tolerance or withdrawal plans').fill(
        'Long-term horizon; equity downside is tolerable and no near-term withdrawals are required.'
      );
      await page.getByRole('checkbox', { name: /is new money/i }).check();

      lastAction = 'submit new cash analysis';
      const analyzeBtn = page.getByRole('button', { name: 'Analyze', exact: true });
      await expect(analyzeBtn).toBeEnabled();

      const [res] = await Promise.all([
        page.waitForResponse(
          res => new URL(res.url()).pathname === '/api/analyze' && res.request().method() === 'POST',
          { timeout: 170000 }
        ),
        analyzeBtn.click(),
      ]);
      const body = await res.text();
      lastHTTP = { status: res.status(), body };
      persist();
      assert.equal(res.status(), 200, `HTTP ${res.status()}: ${body}`);
      const result = JSON.parse(body);
      assert.equal(result.status, 'completed');
      lastResult = result;
      await expect(page.locator('main')).toContainText(/Analyst memo|New cash|Add/i);
      await page.screenshot({ path: path.join(output, `${report.length}-newcash-result.png`), fullPage: true, timeout: 5000 });
    });

    // 6. Theme with 2 stocks
    await workflow('6 Theme discovery', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('portfolio-date').first()).toBeVisible();
      lastAction = 'open theme form';
      await askInput().fill('/theme Industrial Automation & Robotics');
      await submitBtn().click();
      await expect(page.getByRole('group', { name: 'Theme inputs' })).toBeVisible();

      lastAction = 'fill theme details';
      await page.getByLabel('Economic mechanism').fill(
        'Labor shortages and reshoring drive sustained capital investment in automated industrial systems.'
      );

      // Reuse two held positions for the agreed shortlist.
      const heldSelect = page.getByLabel('Add held position to shortlist');
      const avgoOption = await heldSelect.locator('option').filter({ hasText: 'AVGO' }).first().getAttribute('value');
      if (avgoOption) {
        await heldSelect.selectOption(avgoOption);
      } else {
        const firstOpt = await heldSelect.locator('option').nth(1).getAttribute('value');
        if (firstOpt) await heldSelect.selectOption(firstOpt);
      }

      const googOption = await heldSelect.locator('option').filter({ hasText: 'GOOG' }).first().getAttribute('value');
      assert.ok(googOption, 'GOOG must be available for the two-stock shortlist');
      await heldSelect.selectOption(googOption);
      await expect(page.getByRole('list', { name: 'Candidate chips' })).toContainText('GOOG');

      lastAction = 'submit initial unconfirmed theme';
      const analyzeBtn = page.getByRole('button', { name: 'Analyze', exact: true });
      await analyzeBtn.click();

      // Agreement card
      const memo = page.getByRole('region', { name: 'Analyst memo' });
      await expect(memo).toContainText('Needs your agreement');
      const agreeBtn = memo.getByRole('button', { name: 'Agree and research' });
      await expect(agreeBtn).toBeVisible();

      lastAction = 'submit agreed theme research';
      const [res] = await Promise.all([
        page.waitForResponse(
          res => new URL(res.url()).pathname === '/api/analyze' && res.request().method() === 'POST',
          { timeout: 170000 }
        ),
        agreeBtn.click(),
      ]);
      const body = await res.text();
      lastHTTP = { status: res.status(), body };
      persist();
      assert.equal(res.status(), 200, `HTTP ${res.status()}: ${body}`);
      const result = JSON.parse(body);
      assert.equal(result.status, 'completed');
      lastResult = result;
      await expect(memo).toContainText(/Completed theme research|Economic mechanism/i);
      await page.screenshot({ path: path.join(output, `${report.length}-theme-result.png`), fullPage: true, timeout: 5000 });
    });

    // 7. Save, reopen, re-run, and confirm
    await workflow('7 Save, reopen, re-run, and confirm', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('portfolio-date').first()).toBeVisible();
      await sendQuestion('Review my portfolio concentration and missing inputs.');
      assert.ok(lastResult, 'Need prior completed result to save decision');
      lastAction = 'click save decision';
      const saveBtn = page.getByRole('button', { name: 'Save decision', exact: true });
      await expect(saveBtn).toBeVisible();

      const [saveRes] = await Promise.all([
        page.waitForResponse(
          res => new URL(res.url()).pathname === '/api/decisions' && res.request().method() === 'POST',
          { timeout: 15000 }
        ),
        saveBtn.click(),
      ]);
      assert.equal(saveRes.status(), 200);
      const saved = await saveRes.json();
      assert.equal(saved.workflow, 'review');
      console.log(`Saved decision ID: ${saved.id}`);

      lastAction = `reopen decision ${saved.id} from rail`;
      const decisionItem = page.locator('[data-testid="decision-item"]').first();
      await expect(decisionItem).toBeVisible({ timeout: 10000 });
      await decisionItem.click();

      const historicalView = page.getByRole('region', { name: 'Reopened decision' });
      await expect(historicalView).toBeVisible({ timeout: 10000 });

      lastAction = 'confirm action on reopened decision';
      await historicalView.getByRole('radio', { name: 'Left the portfolio as it is' }).check();
      const noteInput = historicalView.getByPlaceholder(/Reviewed thesis|e\.g\./i);
      if (await noteInput.isVisible()) {
        await noteInput.fill('Acceptance verification: no action taken.');
      }
      const confirmBtn = historicalView.getByRole('button', { name: 'Record action' });
      const [confirmRes] = await Promise.all([
        page.waitForResponse(
          res => res.url().includes(`/api/decisions/${saved.id}/confirm`) && res.request().method() === 'POST',
          { timeout: 15000 }
        ),
        confirmBtn.click(),
      ]);
      assert.equal(confirmRes.status(), 200);
      assert.equal((await confirmRes.json()).confirmed_action.action, 'no_action');
      await expect(historicalView.getByRole('button', { name: 'Record action' })).toHaveCount(0);

      lastAction = 're-run decision with today portfolio';
      const rerunBtn = historicalView.getByRole('button', { name: "Re-run with today's portfolio" });
      await expect(rerunBtn).toBeVisible();

      // Trigger re-run and await response if it sends analyze or pre-fills form
      const rerunPromise = page.waitForResponse(
        res => new URL(res.url()).pathname === '/api/analyze' && res.request().method() === 'POST',
        { timeout: 170000 }
      );

      await rerunBtn.click();
      const rerunRes = await rerunPromise;
      assert.equal(rerunRes.status(), 200);
      const rerunResult = await rerunRes.json();
      assert.equal(rerunResult.status, 'completed');
      assert.ok(!lastRequest.payload.stock && !lastRequest.payload.new_cash && !lastRequest.payload.theme && !lastRequest.payload.portfolio_review);
      assert.deepEqual(lastRequest.payload.portfolio.positions.filter(p => p.id !== 'benchmark-fund').map(p => [p.id, p.shares]),
        portfolio.snapshot.positions.map(p => [p.id, p.shares]));
      await expect(page.locator('main .memo').first()).toBeVisible();
      await page.screenshot({ path: path.join(output, `${report.length}-rerun-result.png`), fullPage: true, timeout: 5000 });
    });

    // 2. Rebalance
    await workflow('2 Rebalance', async () => {
      const res = await sendQuestion('/rebalance');
      assert.deepEqual(res.reunderwriting.assessments.map(row => row.position_id).sort(),
        portfolio.snapshot.positions.map(row => row.id).sort(), 'Every holding must be assessed');
      assert.equal(res.reunderwriting.stocks.length, portfolio.snapshot.positions.length);
      await expect(page.locator('main').locator('.memo').first()).toContainText(/Rebalance|Change|rules|No change/i);
    });


  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
    persist();

    // Generate markdown report
    const mdLines = [
      '# Greedy V1 Acceptance Report: Real-Portfolio Hardening',
      '',
      `**Date:** ${new Date().toISOString()}`,
      `**Model:** \`gpt-6-luna\` (configured effort: \`max\`; synthesis capped at \`high\`, Rebalance \`medium\`; tool turns \`medium\`, Rebalance judgments/comparison \`none\`)`,
      `**Provider:** \`chatgpt_plan\` (Responses API streamed under 170s turn deadline)`,
      `**Portfolio:** Real 9-stock saved portfolio (AVGO, F, GOOG, HDB, IBN, INFY, NBIS, SHOP, CM)`,
      `**Benchmark:** Pre-configured ETF (\`XEQT.TO\`), portfolio rules unset`,
      '',
      '## Acceptance Summary Table',
      '',
      '| # | Workflow | Status | Duration | Turns | In Tokens | Out Tokens | Reasoning Tokens | Tools Dispatched |',
      '|---|---|---|---|---|---|---|---|---|',
    ];

    let allPass = acceptancePassed(report);
    for (let i = 0; i < report.length; i++) {
      const r = report[i];
      if (r.status !== 'PASS') allPass = false;
      const toolsStr = r.tools && r.tools.length ? `\`${r.tools.join('`, `')}\`` : 'None';
      mdLines.push(
        `| ${i + 1} | ${r.workflow} | **${r.status}** | ${(r.duration_ms / 1000).toFixed(1)}s | ${r.openai_turns} | ${r.tokens?.in ?? 0} | ${r.tokens?.out ?? 0} | ${r.tokens?.reasoning ?? 0} | ${toolsStr} |`
      );
    }

    mdLines.push('');
    mdLines.push(`**Overall Result:** **${allPass ? 'ACCEPTED (ALL PASSED)' : 'NOT ACCEPTED'}**`);
    mdLines.push('');
    mdLines.push('## Per-Workflow Observations');
    mdLines.push('');
    for (const r of report) {
      mdLines.push(`### ${r.workflow}`);
      mdLines.push(`- **Status:** ${r.status}`);
      mdLines.push(`- **Duration:** ${(r.duration_ms / 1000).toFixed(1)}s`);
      mdLines.push(`- **Turns:** ${r.openai_turns}`);
      mdLines.push(`- **Tools Dispatched:** ${r.tools && r.tools.length ? r.tools.join(', ') : 'None'}`);
      if (r.corrections && r.corrections.length) {
        mdLines.push(`- **In-flight Reprompts/Corrections:** ${r.corrections.join(', ')}`);
      }
      if (r.failure) {
        mdLines.push(`- **Failure:** ${r.http?.status ? `HTTP ${r.http.status}; inspect private local artifacts for details.` : 'Workflow did not complete inside its deadline or UI assertions failed; inspect private local artifacts.'}`);
      }
      mdLines.push('');
    }

    const reportMd = mdLines.join('\n');
    fs.writeFileSync(path.join(output, 'acceptance-report.md'), reportMd);
    fs.writeFileSync(path.resolve(__dirname, '../docs/greedy-v1-acceptance-report.md'), reportMd);
    console.log(`\nAcceptance report written to: docs/greedy-v1-acceptance-report.md`);

    const cleanupStop = setTimeout(() => {
      console.error('Cleanup hard deadline');
      process.exit(1);
    }, 10000);
    try {
      if (page) await bounded(() => page.context().tracing.stop({ path: path.join(output, 'trace.zip') }), 5000, 'trace cleanup').catch(console.error);
      if (browser) await bounded(() => browser.close(), 5000, 'browser cleanup');
      clearTimeout(hardStop);
      clearTimeout(cleanupStop);
    } catch (error) {
      console.error(error);
      process.exitCode = 1;
    }
    console.log('Acceptance run complete. Artifacts saved in:', output);
  }
}

module.exports = { bounded, acceptancePassed };
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
