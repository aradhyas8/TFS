import { test, expect } from '@playwright/test';

test('ChatGPT connection can be started without blocking the local portfolio', async ({ page }) => {
  let connecting = false;
  await page.route('**/api/ai/**', async route => {
    if (route.request().url().endsWith('/login')) connecting = true;
    await route.fulfill({ json: { state: connecting ? 'connecting' : 'disconnected', provider: 'chatgpt_plan', model: '', account: null, models: [], api_key_available: false, api_provider: 'openai', message: connecting ? 'Complete sign-in in the browser window.' : 'Connect your ChatGPT plan for AI questions.' } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Continue with ChatGPT', exact: true }).click();
  await expect(page.getByText('Complete sign-in in the browser window.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel sign-in' })).toBeVisible();
  await page.getByRole('button', { name: 'Portfolio', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Portfolio', exact: true })).toBeVisible();
});

for (const state of ['disconnected', 'connecting', 'connected', 'expired', 'unavailable', 'failed'] as const) {
  test(`connection ${state} state (mocked OAuth)`, async ({ page }) => {
    const messages = {
      disconnected: 'Connect your ChatGPT plan for AI questions.',
      connecting: 'Complete sign-in in the browser window.',
      connected: 'ChatGPT connected. Select a supported model for AI questions.',
      expired: 'The access token expired. Reconnect, or refresh the connection to renew the session.',
      unavailable: 'ChatGPT sign-in needs native Windows and DPAPI. Run TFS in Windows, or use the API-key provider here.',
      failed: 'Plan permission was not granted. Reconnect and allow ChatGPT plan usage.',
    };
    let selected = '';
    await page.route('**/api/ai/**', async route => {
      if (route.request().url().endsWith('/provider')) selected = route.request().postDataJSON().model;
      await route.fulfill({ json: { state, provider: 'chatgpt_plan', model: selected, account: state === 'connected' ? 'investor@example.test' : null,
        models: state === 'connected' ? [{ slug: 'supported-test-model', display_name: 'Supported model' }] : [],
        api_key_available: true, api_provider: 'openai', message: messages[state] } });
    });
    await page.goto('/');
    await page.getByText(`AI connection · ${state}`, { exact: true }).click();
    await expect(page.getByText(messages[state], { exact: true })).toBeVisible();
    if (state === 'connected') {
      await page.getByLabel('ChatGPT model').selectOption('supported-test-model');
      await page.getByRole('button', { name: 'Use ChatGPT plan', exact: true }).click();
      await expect(page.getByText('Selected model:', { exact: false })).toContainText('supported-test-model');
    }
    const { mkdir } = await import('node:fs/promises');
    await mkdir('../docs/dev-chatgpt/screenshots', { recursive: true });
    await page.screenshot({ path: `../docs/dev-chatgpt/screenshots/${state}.png`, fullPage: true });
    const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }));
    expect(storage).not.toMatch(/access_token|refresh_token|id_token|code_verifier|dummy-access/);
  });
}

test('connection controls fit a phone and preserve API-key choice', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/ai/**', route => route.fulfill({ json: { state: 'connected', provider: 'openai', model: 'api-model', account: 'investor@example.test', models: [], api_key_available: true, api_provider: 'openai', message: 'ChatGPT connected.' } }));
  await page.goto('/');
  await page.getByText('AI connection · connected', { exact: true }).click();
  await expect(page.getByRole('button', { name: 'Use API-key provider' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../docs/dev-chatgpt/screenshots/mobile.png', fullPage: true });
});
