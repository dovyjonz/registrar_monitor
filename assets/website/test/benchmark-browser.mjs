import { chromium } from '@playwright/test';

const [url, coldText = '10', warmText = '20'] = process.argv.slice(2);
if (!url) {
    throw new Error('usage: node test/benchmark-browser.mjs URL [COLD] [WARM]');
}

const coldIterations = Number.parseInt(coldText, 10);
const warmIterations = Number.parseInt(warmText, 10);
if (coldIterations <= 0 || warmIterations <= 0) {
    throw new Error('iteration counts must be positive');
}

const summarize = (samples, unit = 'ns') => {
    const ordered = [...samples].sort((a, b) => a - b);
    const middle = Math.floor(ordered.length / 2);
    const median = ordered.length % 2
        ? ordered[middle]
        : (ordered[middle - 1] + ordered[middle]) / 2;
    return {
        unit,
        samples,
        median,
        p95: ordered[Math.ceil(ordered.length * 0.95) - 1],
    };
};

const browser = await chromium.launch({ headless: true });
const browserVersion = browser.version();

async function measure(context) {
    const page = await context.newPage();
    const start = process.hrtime.bigint();
    await page.goto(url, { waitUntil: 'load' });
    await page.locator('body').waitFor({ state: 'visible' });
    await page.waitForFunction(() => {
        const grid = document.querySelector('#courseGrid');
        return grid
            && getComputedStyle(grid).display !== 'none'
            && !document.body.textContent.includes('Loading enrollment data...');
    });
    const readyNs = Number(process.hrtime.bigint() - start);
    const navigation = await page.evaluate(() => {
        const entry = performance.getEntriesByType('navigation')[0];
        const resources = performance.getEntriesByType('resource');
        return {
            domContentLoadedNs: Math.round(entry.domContentLoadedEventEnd * 1e6),
            loadNs: Math.round(entry.loadEventEnd * 1e6),
            transferredBytes: Math.round(
                resources.reduce((total, resource) => total + resource.transferSize, 0),
            ),
            encodedBytes: Math.round(
                resources.reduce((total, resource) => total + resource.encodedBodySize, 0),
            ),
        };
    });
    const beforeCourseBytes = navigation.encodedBytes;
    const courseStart = process.hrtime.bigint();
    await page.locator('.course-cell').first().click();
    await page.locator('#modalOverlay.active').waitFor({ state: 'visible' });
    await page.waitForFunction(() => {
        const canvas = document.querySelector('#enrollment-chart');
        return canvas && !canvas.classList.contains('chart-hidden');
    });
    await page.evaluate(() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
    ));
    const courseReadyNs = Number(process.hrtime.bigint() - courseStart);
    const afterCourseBytes = await page.evaluate(() =>
        Math.round(
            performance.getEntriesByType('resource')
                .reduce((total, resource) => total + resource.encodedBodySize, 0),
        ),
    );
    await page.close();
    return {
        readyNs,
        ...navigation,
        courseReadyNs,
        courseBytes: Math.max(0, afterCourseBytes - beforeCourseBytes),
    };
}

const cold = [];
for (let index = 0; index < coldIterations; index += 1) {
    const context = await browser.newContext();
    cold.push(await measure(context));
    await context.close();
}

const warmContext = await browser.newContext();
await measure(warmContext);
const warm = [];
for (let index = 0; index < warmIterations; index += 1) {
    warm.push(await measure(warmContext));
}
await warmContext.close();
await browser.close();

const aggregate = (samples) => ({
    navigation_to_ready: summarize(samples.map(sample => sample.readyNs)),
    dom_content_loaded: summarize(samples.map(sample => sample.domContentLoadedNs)),
    load_complete: summarize(samples.map(sample => sample.loadNs)),
    transferred_resources: summarize(
        samples.map(sample => sample.transferredBytes),
        'bytes',
    ),
    course_open: summarize(samples.map(sample => sample.courseReadyNs)),
    course_open_bytes: summarize(
        samples.map(sample => sample.courseBytes),
        'bytes',
    ),
});

process.stdout.write(`${JSON.stringify({
    browser_version: browserVersion,
    cold: aggregate(cold),
    warm: aggregate(warm),
})}\n`);
