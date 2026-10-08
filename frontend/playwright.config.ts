import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8001",
    trace: "retain-on-failure",
    launchOptions: existsSync("/usr/bin/chromium")
      ? { executablePath: "/usr/bin/chromium" }
      : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `../.venv/bin/file-recommender serve --port 8001 --data-dir /tmp/folio-playwright-${Date.now()}`,
    url: "http://127.0.0.1:8001/health",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
