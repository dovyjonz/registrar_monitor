import { defineConfig } from '@playwright/test';

const siteDirectory = process.env.REGISTRAR_SITE_DIR || 'public';

export default defineConfig({
    testDir: './test',
    testMatch: 'browser-smoke.spec.mjs',
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    reporter: [
        ['line'],
        ['html', { outputFolder: '../../output/playwright/report', open: 'never' }],
    ],
    outputDir: '../../output/playwright/test-results',
    use: {
        baseURL: 'http://127.0.0.1:4173',
        browserName: process.env.PLAYWRIGHT_BROWSER || 'chromium',
        headless: true,
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
            ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
            : {},
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
    },
    webServer: {
        command: `python3 -m http.server 4173 --bind 127.0.0.1 --directory ${JSON.stringify(siteDirectory)}`,
        port: 4173,
        reuseExistingServer: !process.env.CI,
    },
});
