import { test, expect, type Page } from '@playwright/test';

const OPENROUTER = [
  { slug: 'deepseek/deepseek-v4.1-flash', display_name: 'DeepSeek V4.1 Flash' },
  { slug: 'nvidia/nemotron-3-super-120b-a12b:free', display_name: 'Nemotron 3 Super Free' },
];

async function mockConnection(page: Page, refuse?: string) {
  const status = { state: 'connected', chatgpt_state: 'connected', provider: 'chatgpt_plan', model: 'gpt-test', account: 'investor@example.test',
    models: [{ slug: 'gpt-test', display_name: 'GPT test' }], api_key_available: false, api_provider: 'openai',
    openrouter_models: OPENROUTER, openrouter_available: true, message: 'ChatGPT connected.' };
  const sent: object[] = [];
  await page.route('**/api/ai/**', async route => {
    if (route.request().url().endsWith('/provider')) {
      const body = route.request().postDataJSON();
      sent.push(body);
      if (refuse) return route.fulfill({ status: 422, json: { detail: refuse } });
      Object.assign(status, { provider: body.provider, model: body.model });
    }
    await route.fulfill({ json: status });
  });
  return sent;
}

test('composer picker switches between ChatGPT and OpenRouter models', async ({ page }) => {
  const sent = await mockConnection(page);
  await page.goto('/');
  const picker = page.getByLabel('AI model');
  await expect(picker).toHaveValue('chatgpt_plan|gpt-test');
  await expect(picker.locator('optgroup[label="OpenRouter"] option')).toHaveText(['DeepSeek V4.1 Flash', 'Nemotron 3 Super Free']);
  await picker.selectOption('openrouter|deepseek/deepseek-v4.1-flash');
  await expect(picker).toHaveValue('openrouter|deepseek/deepseek-v4.1-flash');
  expect(sent).toEqual([{ provider: 'openrouter', model: 'deepseek/deepseek-v4.1-flash' }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // Keys stay in the backend: the page never asks for one.
  await expect(page.locator('input[type="password"], input[name*="key" i]')).toHaveCount(0);
});

test('a refused model shows why and keeps the current model', async ({ page }) => {
  const reason = 'nvidia/nemotron-3-super-120b-a12b:free does not support structured_outputs on OpenRouter, which TFS analysis needs. Choose another model.';
  await mockConnection(page, reason);
  await page.goto('/');
  const picker = page.getByLabel('AI model');
  await picker.selectOption('openrouter|nvidia/nemotron-3-super-120b-a12b:free');
  await expect(page.getByRole('alert').filter({ hasText: reason })).toBeVisible();
  await expect(picker).toHaveValue('chatgpt_plan|gpt-test');
});
