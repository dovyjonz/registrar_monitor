import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const siteDirectory = process.env.REGISTRAR_SITE_DIR || 'public';

function readSiteJson(relativePath) {
    return JSON.parse(readFileSync(resolve(siteDirectory, relativePath), 'utf8'));
}

function readManifestFixture() {
    const pointer = readSiteJson('data/summer-2026/manifest.json');
    const manifest = readSiteJson(`data/summer-2026/${pointer.current}`);
    return { pointer, manifest };
}

function readSemesterManifestFixture(semesterSlug) {
    const pointer = readSiteJson(`data/${semesterSlug}/manifest.json`);
    const manifestPath = `data/${semesterSlug}/${pointer.current}`;
    return {
        pointer,
        manifest: readSiteJson(manifestPath),
        manifestPath: `/${manifestPath}`,
    };
}

function readHistoricalDepartmentPaths(department, excludeSemester = '') {
    return ['spring-2026', 'fall-2025', 'summer-2025']
        .filter(semesterSlug => semesterSlug !== excludeSemester)
        .flatMap(semesterSlug => {
        const pointer = readSiteJson(`data/${semesterSlug}/manifest.json`);
        const manifest = readSiteJson(`data/${semesterSlug}/${pointer.current}`);
        const url = new URL(
            manifest.departments[department].url,
            `http://127.0.0.1/data/${semesterSlug}/${pointer.current}`,
        );
        return [url.pathname];
    });
}

function sha256Hex(body) {
    return createHash('sha256').update(body).digest('hex');
}

test('generated production site serves a working semester dashboard', async ({ page }) => {
    const failedRequests = [];
    const pageErrors = [];
    const jsonRequests = [];
    page.on('requestfailed', request => failedRequests.push(request.url()));
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('request', request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname.endsWith('.json')) jsonRequests.push(pathname);
    });

    const semesterResponse = await page.goto('/summer2026.html');
    expect(semesterResponse?.ok()).toBe(true);
    await expect(page).toHaveURL(/\/summer2026\.html$/);
    await expect(page.locator('body')).not.toContainText('Loading enrollment data...');
    await expect(page.locator('#courseGrid')).toBeVisible();

    const manifestPointerUrl = await page.locator('body').getAttribute('data-manifest-url');
    expect(manifestPointerUrl).toBeTruthy();
    expect(manifestPointerUrl).toMatch(/^data\/[^/]+\/manifest\.json$/);
    const manifestPointerResponse = await page.request.get(
        new URL(manifestPointerUrl, page.url()).href,
    );
    expect(manifestPointerResponse.ok()).toBe(true);
    expect(await manifestPointerResponse.json()).toBeTruthy();

    expect(jsonRequests.some(url => /\/data\/[^/]+\/manifest\.json$/.test(url))).toBe(true);
    expect(jsonRequests.some(url => /\/data\/[^/]+\/manifests\/.+\.json$/.test(url))).toBe(true);
    const blobRequests = () => jsonRequests.filter(
        url => /\/data\/blobs\/.+\.json$/.test(url),
    );
    // The first blob is the summary. No department history is fetched at startup.
    expect(blobRequests()).toHaveLength(1);
    expect(jsonRequests.some(url => /^\/[^/]+\.json$/.test(url))).toBe(false);

    const firstCourse = page.locator('.course-cell').first();
    const firstCode = await firstCourse.getAttribute('data-course');
    const firstDepartment = firstCode.split(' ')[0];
    await firstCourse.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect.poll(() => page.evaluate(() => (
        document.querySelector('#modalOverlay')?.contains(document.activeElement)
    ))).toBe(true);
    expect(blobRequests()).toHaveLength(2);
    await page.keyboard.press('Escape');
    await expect(firstCourse).toBeFocused();

    const sameDepartmentCourses = page.locator(
        `.course-cell[data-course^="${firstDepartment} "]`,
    );
    if (await sameDepartmentCourses.count() > 1) {
        await sameDepartmentCourses.nth(1).click();
        await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    } else {
        await firstCourse.click();
        await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    }
    expect(blobRequests()).toHaveLength(2);

    const duplicateIds = await page.locator('[id]').evaluateAll(elements => {
        const counts = new Map();
        for (const element of elements) {
            counts.set(element.id, (counts.get(element.id) || 0) + 1);
        }
        return [...counts.entries()].filter(([, count]) => count > 1);
    });
    expect(duplicateIds).toEqual([]);
    await expect(page.locator('#modalOverlay')).toHaveAttribute('role', 'dialog');
    await expect(page.locator('#modalCloseBtn')).toHaveAccessibleName(/close/i);

    await page.keyboard.press('Escape');

    const allFilter = page.locator('.filter-btn[data-filter="all"]');
    const openFilter = page.locator('.filter-btn[data-filter="open"]');
    await openFilter.click();
    await expect(openFilter).toHaveClass(/active/);
    await expect(allFilter).not.toHaveClass(/active/);
    await allFilter.click();
    await expect(allFilter).toHaveClass(/active/);
    await page.locator('#courseSearch').fill(firstCode);
    await expect(page.locator('.course-cell:not(.hidden)')).toHaveCount(1);
    await page.locator('#courseSearch').fill('');

    expect(failedRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});

test('department 404 keeps the modal open and retry loads the selected course', async ({ page }) => {
    const { pointer, manifest } = readManifestFixture();
    const manifestUrl = new URL(
        `data/summer-2026/${pointer.current}`,
        'http://127.0.0.1:4173/',
    );
    const summaryPath = new URL(manifest.summary.url, manifestUrl).pathname;
    let departmentRequests = 0;

    await page.route('**/data/blobs/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === summaryPath) {
            await route.continue();
            return;
        }
        departmentRequests += 1;
        if (departmentRequests === 1) {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'not found' }),
            });
            return;
        }
        await route.continue();
    });

    const response = await page.goto('/summer2026.html');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('#courseGrid .course-cell').first()).toBeVisible();

    const firstCourse = page.locator('.course-cell').first();
    await firstCourse.click();
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#modalDetailState[role="alert"]')).toContainText(
        'Missing department data',
    );
    await expect(page.locator('#retryDepartment')).toBeVisible();

    await page.locator('#retryDepartment').click();
    await expect(page.locator('#modalDetailState')).toHaveCount(0);
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    expect(departmentRequests).toBe(2);
});

test('malformed summary produces a visible page-level error', async ({ page }) => {
    const { pointer, manifest } = readManifestFixture();
    const malformedSummary = JSON.stringify({
        schemaVersion: 1,
        kind: 'semester-summary',
        semester: manifest.semester,
        lastReportTime: null,
        snapshotCount: 0,
        currentSnapshot: null,
        milestones: [],
        courses: [],
    });
    const malformedManifest = {
        ...manifest,
        summary: {
            ...manifest.summary,
            url: '../../blobs/phase5-malformed-summary.json',
            sha256: sha256Hex(malformedSummary),
            bytes: Buffer.byteLength(malformedSummary),
        },
    };

    await page.route(`**/data/summer-2026/${pointer.current}`, async route => {
        await route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify(malformedManifest),
        });
    });
    await page.route('**/data/blobs/phase5-malformed-summary.json', async route => {
        await route.fulfill({
            contentType: 'application/json',
            body: malformedSummary,
        });
    });

    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const response = await page.goto('/summer2026.html');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.error-state')).toContainText(
        'Failed to load enrollment data',
    );
    await expect(page.locator('#courseGrid .course-cell')).toHaveCount(0);
    expect(pageErrors).toEqual([]);
});

test('course deep links open the modal after the verified summary loads', async ({ page }) => {
    const response = await page.goto('/summer2026.html');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.course-cell').first()).toBeVisible();

    const firstCourse = page.locator('.course-cell').first();
    const firstCode = await firstCourse.getAttribute('data-course');
    const deepLink = firstCode.replace(/\s+/g, '-');
    await page.goto(`/summer2026.html#${deepLink}`);

    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#modalTitle')).toContainText(firstCode);
    await page.keyboard.press('Escape');
    await expect(page.locator('#modalOverlay')).not.toHaveClass(/active/);
});

test('broken current manifest falls back with a visible stale-data state', async ({ page }) => {
    const { pointer } = readManifestFixture();
    await page.route('**/data/summer-2026/manifest.json', async route => {
        await route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({
                ...pointer,
                current: 'manifests/broken-current.json',
                previous: pointer.current,
            }),
        });
    });

    const response = await page.goto('/summer2026.html');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('#courseGrid')).toBeVisible();
    await expect(page.locator('#lastUpdated')).toContainText('Stale data');
});

test('historical course comparison is lazy, optional, aligned, and reset per modal', async ({ page }) => {
    const jsonRequests = [];
    page.on('request', request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname.endsWith('.json')) jsonRequests.push(pathname);
    });

    const response = await page.goto('/fall2025.html');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.course-cell[data-course="MATH 161"]')).toBeVisible();

    const historicalDepartmentPaths = readHistoricalDepartmentPaths('MATH', 'fall-2025');
    expect(jsonRequests.some(pathname => /\/data\/(spring-2026|summer-2025)\//.test(pathname))).toBe(false);

    await page.locator('.course-cell[data-course="MATH 161"]').click();
    await expect(page.locator('#enrollment-chart')).toBeVisible();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'idle',
    );
    expect(jsonRequests.some(pathname => /\/data\/(spring-2026|summer-2025)\//.test(pathname))).toBe(false);
    expect(jsonRequests.filter(pathname => historicalDepartmentPaths.includes(pathname))).toHaveLength(0);

    const toggle = page.locator('#historicalComparisonToggle');
    await expect(toggle).toHaveAccessibleName(/find an earlier semester/i);
    await toggle.click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalLegendItem')).toBeVisible();
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('2');
    expect(jsonRequests.filter(pathname => historicalDepartmentPaths.includes(pathname))).toHaveLength(1);

    await page.locator('.chart-mode-btn[data-mode="timeline"]').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalLegendItem')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('#modalOverlay')).not.toHaveClass(/active/);
    await page.locator('.course-cell[data-course="MATH 161"]').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'available',
    );
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(jsonRequests.filter(pathname => historicalDepartmentPaths.includes(pathname))).toHaveLength(1);
});

test('historical course comparison renders when the current course has no chart history', async ({ page }) => {
    const current = readSemesterManifestFixture('fall-2025');
    const courseCode = 'MATH 161';
    const currentDepartmentUrl = new URL(
        current.manifest.departments.MATH.url,
        `http://127.0.0.1${current.manifestPath}`,
    );
    const currentPayload = readSiteJson(currentDepartmentUrl.pathname.slice(1));
    const currentCourse = currentPayload.courses[courseCode];
    currentCourse.averageHistory = [];
    currentCourse.sectionHistory = Object.fromEntries(
        Object.keys(currentCourse.sectionHistory).map(sectionCode => [sectionCode, []]),
    );
    const currentBody = JSON.stringify(currentPayload);
    const currentManifest = {
        ...current.manifest,
        departments: {
            ...current.manifest.departments,
            MATH: {
                ...current.manifest.departments.MATH,
                sha256: sha256Hex(currentBody),
                bytes: Buffer.byteLength(currentBody),
            },
        },
    };

    await page.route('**/*.json', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === current.manifestPath) {
            await route.fulfill({
                contentType: 'application/json',
                body: JSON.stringify(currentManifest),
            });
            return;
        }
        if (pathname === currentDepartmentUrl.pathname) {
            await route.fulfill({ contentType: 'application/json', body: currentBody });
            return;
        }
        await route.continue();
    });

    const response = await page.goto('/fall2025.html');
    expect(response?.ok()).toBe(true);
    await page.locator(`.course-cell[data-course="${courseCode}"]`).click();
    await expect(page.locator('#enrollment-chart')).toBeVisible();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'idle',
    );
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-datasets',
        '1',
    );

    await page.locator('#historicalComparisonToggle').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalLegendItem')).toBeVisible();
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('2');
});

test('selecting a section switches to professor comparison and deselecting returns to course mode', async ({ page }) => {
    const current = readSemesterManifestFixture('fall-2025');
    const historical = readSemesterManifestFixture('summer-2025');
    const courseCode = 'MATH 161';

    const currentDepartmentUrl = new URL(
        current.manifest.departments.MATH.url,
        `http://127.0.0.1${current.manifestPath}`,
    );
    const historicalDepartmentUrl = new URL(
        historical.manifest.departments.MATH.url,
        `http://127.0.0.1${historical.manifestPath}`,
    );
    const currentPayload = readSiteJson(currentDepartmentUrl.pathname.slice(1));
    const historicalPayload = readSiteJson(historicalDepartmentUrl.pathname.slice(1));
    currentPayload.courses[courseCode].sections['1L'].instructor = 'Jane Smith';
    historicalPayload.courses[courseCode].sections['1L'].instructor = 'Jane Smith';
    historicalPayload.courses[courseCode].sections['1R'].instructor = 'Jane Smith';

    const currentBody = JSON.stringify(currentPayload);
    const historicalBody = JSON.stringify(historicalPayload);
    const currentManifest = {
        ...current.manifest,
        departments: {
            ...current.manifest.departments,
            MATH: {
                ...current.manifest.departments.MATH,
                sha256: sha256Hex(currentBody),
                bytes: Buffer.byteLength(currentBody),
            },
        },
    };
    const historicalManifest = {
        ...historical.manifest,
        departments: {
            ...historical.manifest.departments,
            MATH: {
                ...historical.manifest.departments.MATH,
                sha256: sha256Hex(historicalBody),
                bytes: Buffer.byteLength(historicalBody),
            },
        },
    };

    await page.route('**/*.json', async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === current.manifestPath) {
            await route.fulfill({
                contentType: 'application/json',
                body: JSON.stringify(currentManifest),
            });
            return;
        }
        if (pathname === historical.manifestPath) {
            await route.fulfill({
                contentType: 'application/json',
                body: JSON.stringify(historicalManifest),
            });
            return;
        }
        if (pathname === currentDepartmentUrl.pathname) {
            await route.fulfill({ contentType: 'application/json', body: currentBody });
            return;
        }
        if (pathname === historicalDepartmentUrl.pathname) {
            await route.fulfill({ contentType: 'application/json', body: historicalBody });
            return;
        }
        await route.continue();
    });

    const response = await page.goto('/fall2025.html');
    expect(response?.ok()).toBe(true);
    await page.locator(`.course-cell[data-course="${courseCode}"]`).click();
    await expect(page.locator('#section-1L')).toBeVisible();
    await page.locator('#section-1L').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'available',
    );
    await page.locator('#historicalComparisonToggle').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalLegendLabel')).toHaveText('Summer 2025 · Jane Smith');
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('2');

    await page.locator('#section-1L').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'available',
    );
    await expect(page.locator('#historicalComparisonToggle')).toHaveAttribute('aria-pressed', 'false');
});
