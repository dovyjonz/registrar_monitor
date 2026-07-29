import { expect, test } from '@playwright/test';

test('generated production site serves a working semester dashboard', async ({ page }) => {
    const failedRequests = [];
    const pageErrors = [];
    page.on('requestfailed', request => failedRequests.push(request.url()));
    page.on('pageerror', error => pageErrors.push(error.message));

    const indexResponse = await page.goto('/');
    expect(indexResponse?.ok()).toBe(true);

    const semesterLink = page
        .locator('a[href$=".html"]:not([href="index.html"])')
        .first();
    await expect(semesterLink).toBeVisible();
    await semesterLink.click();

    await expect(page).toHaveURL(/\/[^/]+\.html$/);
    await expect(page.locator('body')).not.toContainText('Loading enrollment data...');
    await expect(page.locator('#courseGrid')).toBeVisible();

    const payloadUrl = await page.locator('body').getAttribute('data-json-url');
    expect(payloadUrl).toBeTruthy();
    const payloadResponse = await page.request.get(new URL(payloadUrl, page.url()).href);
    expect(payloadResponse.ok()).toBe(true);
    expect(await payloadResponse.json()).toBeTruthy();

    expect(failedRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});
