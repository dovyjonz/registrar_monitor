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
    await page.waitForFunction(() => (
        performance.getEntriesByName('registrar:summary-ready').length > 0
        && performance.getEntriesByName('registrar:grid-dom-complete').length > 0
        && performance.getEntriesByName('registrar:grid-rendered').length > 0
    ));
    const readyNs = Number(process.hrtime.bigint() - start);
    const navigation = await page.evaluate(() => {
        const entry = performance.getEntriesByType('navigation')[0];
        const resources = performance.getEntriesByType('resource');
        const allEntries = [entry, ...resources].filter(Boolean);
        return {
            domContentLoadedNs: Math.round(entry.domContentLoadedEventEnd * 1e6),
            loadNs: Math.round(entry.loadEventEnd * 1e6),
            transferredBytes: Math.round(
                allEntries.reduce((total, resource) => total + resource.transferSize, 0),
            ),
            encodedBytes: Math.round(
                allEntries.reduce((total, resource) => total + resource.encodedBodySize, 0),
            ),
        };
    });

    const courseStart = process.hrtime.bigint();
    await page.evaluate(() => performance.mark('benchmark:course-click'));
    await page.locator('.course-cell').first().click();
    await page.locator('#modalOverlay.active').waitFor({ state: 'visible' });
    await page.waitForFunction(() => (
        performance.getEntriesByName('registrar:course-detail-ready').length > 0
        && performance.getEntriesByName('registrar:course-rendered').length > 0
    ));
    const courseReadyNs = Number(process.hrtime.bigint() - courseStart);
    const measured = await page.evaluate(() => {
        const latestMark = name => {
            const marks = performance.getEntriesByName(name);
            return marks[marks.length - 1] || null;
        };
        const navigationEntry = performance.getEntriesByType('navigation')[0];
        const resourceEntries = [
            navigationEntry,
            ...performance.getEntriesByType('resource'),
        ].filter(Boolean);
        const pathname = name => {
            try {
                return new URL(name, window.location.href).pathname;
            } catch {
                return name;
            }
        };
        const isJson = entry => pathname(entry.name).endsWith('.json');
        const isDataBlob = entry => (
            isJson(entry) && pathname(entry.name).includes('/data/blobs/')
        );
        const responseEnd = entry => entry.responseEnd || entry.startTime + entry.duration;
        const completedBetween = (startTime, endTime) => resourceEntries.filter(entry => (
            entry.startTime >= startTime
            && responseEnd(entry) > 0
            && responseEnd(entry) <= endTime
        ));
        const sum = (entries, field) => Math.round(
            entries.reduce((total, entry) => total + (entry[field] || 0), 0),
        );

        const summaryReady = latestMark('registrar:summary-ready');
        const gridDomComplete = latestMark('registrar:grid-dom-complete');
        const gridRendered = latestMark('registrar:grid-rendered');
        const courseClick = latestMark('benchmark:course-click');
        const courseDetailReady = latestMark('registrar:course-detail-ready');
        const courseRendered = latestMark('registrar:course-rendered');
        if (!summaryReady || !gridDomComplete || !gridRendered || !courseClick
            || !courseDetailReady || !courseRendered) {
            throw new Error('benchmark performance marks are incomplete');
        }

        const initialEntries = completedBetween(0, gridRendered.startTime);
        const courseEntries = completedBetween(
            courseClick.startTime,
            courseRendered.startTime,
        );
        const summaryEntry = initialEntries.find(isDataBlob);

        return {
            initialTransferBytes: sum(initialEntries, 'transferSize'),
            initialRequestCount: initialEntries.length,
            initialJsonRequestCount: initialEntries.filter(isJson).length,
            summaryBytes: summaryEntry ? Math.round(summaryEntry.encodedBodySize) : 0,
            gridRenderTimeNs: Math.round((gridRendered.startTime - summaryReady.startTime) * 1e6),
            navigationToGridReadyNs: Math.round(gridRendered.startTime * 1e6),
            courseOpenBytes: sum(courseEntries, 'encodedBodySize'),
            courseOpenDataBytes: sum(
                courseEntries.filter(isDataBlob),
                'encodedBodySize',
            ),
            courseOpenRequestCount: courseEntries.length,
        };
    });

    await page.close();
    return {
        readyNs,
        ...navigation,
        courseReadyNs,
        courseBytes: measured.courseOpenBytes,
        ...measured,
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

const aggregate = samples => ({
    // Existing metrics remain in the result for compatibility with prior reports.
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
    initial_transfer_bytes: summarize(
        samples.map(sample => sample.initialTransferBytes),
        'bytes',
    ),
    initial_request_count: summarize(
        samples.map(sample => sample.initialRequestCount),
        'requests',
    ),
    initial_json_request_count: summarize(
        samples.map(sample => sample.initialJsonRequestCount),
        'requests',
    ),
    summary_bytes: summarize(
        samples.map(sample => sample.summaryBytes),
        'bytes',
    ),
    grid_render_time: summarize(samples.map(sample => sample.gridRenderTimeNs)),
    navigation_to_grid_ready: summarize(
        samples.map(sample => sample.navigationToGridReadyNs),
    ),
    course_open_data_bytes: summarize(
        samples.map(sample => sample.courseOpenDataBytes),
        'bytes',
    ),
    course_open_request_count: summarize(
        samples.map(sample => sample.courseOpenRequestCount),
        'requests',
    ),
});

process.stdout.write(`${JSON.stringify({
    browser_version: browserVersion,
    cold: aggregate(cold),
    warm: aggregate(warm),
})}\n`);
