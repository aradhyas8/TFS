import { defineConfig } from "@playwright/test";
import path from "node:path";

const python = process.env.TEST_PYTHON || path.resolve("../.venv/Scripts/python.exe");

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3100", headless: true, channel: "msedge", trace: "retain-on-failure" },
  webServer: [
    { command: `"${python}" -m uvicorn tests.e2e_server:app --app-dir ../backend --host 127.0.0.1 --port 8100`,
      url: "http://127.0.0.1:8100/api/health", reuseExistingServer: false, timeout: 30_000 },
    { command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100", reuseExistingServer: false, timeout: 60_000,
      env: { BACKEND_URL: "http://127.0.0.1:8100", NEXT_TELEMETRY_DISABLED: "1" } },
  ],
});
