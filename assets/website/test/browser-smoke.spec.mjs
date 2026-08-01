import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

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

    const payloadUrl = await page.locator('body').getAttribute('data-json-url');
    expect(payloadUrl).toBeTruthy();
    expect(payloadUrl).toMatch(/^data\/[^/]+\/manifest\.json$/);
    const payloadResponse = await page.request.get(new URL(payloadUrl, page.url()).href);
    expect(payloadResponse.ok()).toBe(true);
    expect(await payloadResponse.json()).toBeTruthy();

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

    expect(failedRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});

test('broken current manifest falls back with a visible stale-data state', async ({ page }) => {
    const siteDirectory = process.env.REGISTRAR_SITE_DIR || 'public';
    const pointer = JSON.parse(readFileSync(
        resolve(siteDirectory, 'data/summer-2026/manifest.json'),
        'utf8',
    ));
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
