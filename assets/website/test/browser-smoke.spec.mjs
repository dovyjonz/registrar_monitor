import { expect, test } from '@playwright/test';

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
    expect(jsonRequests.filter(url => /\/data\/blobs\/.+\.json$/.test(url))).toHaveLength(1);
    expect(jsonRequests.some(url => /^\/[^/]+\.json$/.test(url))).toBe(false);

    const firstCourse = page.locator('.course-cell').first();
    const firstCode = await firstCourse.getAttribute('data-course');
    const firstDepartment = firstCode.split(' ')[0];
    await firstCourse.click();
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await page.locator('#modalCloseBtn').click();

    const sameDepartmentCourses = page.locator(
        `.course-cell[data-course^="${firstDepartment} "]`,
    );
    if (await sameDepartmentCourses.count() > 1) {
        await sameDepartmentCourses.nth(1).click();
        await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    }
    expect(jsonRequests.filter(url => /\/data\/blobs\/.+\.json$/.test(url))).toHaveLength(2);

    expect(failedRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});
