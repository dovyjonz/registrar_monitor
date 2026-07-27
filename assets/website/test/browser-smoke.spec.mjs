import { expect, test } from '@playwright/test';

test('generated semester dashboard loads its data payload', async ({ page }) => {
    await page.goto('/fall2026.html');

    await expect(page.locator('body')).not.toContainText('Loading enrollment data...');
    await expect(page.locator('#courseGrid')).toBeVisible();
});
