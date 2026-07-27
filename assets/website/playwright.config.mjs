import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: './test',
    testMatch: 'browser-smoke.spec.mjs',
    use: {
        baseURL: 'http://127.0.0.1:4173',
        browserName: 'chromium',
        headless: true,
    },
    webServer: {
        command: 'python3 -m http.server 4173 --bind 127.0.0.1 --directory public',
        port: 4173,
        reuseExistingServer: !process.env.CI,
    },
});
