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

async function waitForScrollToSettle(page) {
    await page.evaluate(() => new Promise(resolveScrollSettled => {
        let settleTimer;
        const finish = () => {
            window.removeEventListener('scroll', scheduleFinish);
            resolveScrollSettled();
        };
        const scheduleFinish = () => {
            clearTimeout(settleTimer);
            settleTimer = setTimeout(finish, 100);
        };
        window.addEventListener('scroll', scheduleFinish, { passive: true });
        scheduleFinish();
    }));
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

    const semesterResponse = await page.goto('/semesters/fall-2026/');
    expect(semesterResponse?.ok()).toBe(true);
    await expect(page).toHaveURL(/\/semesters\/fall-2026\/$/);
    await expect(page.locator('body')).not.toContainText('Loading enrollment data...');
    await expect(page.locator('#courseGrid')).toBeVisible();
    await expect(page.locator('#toastContainer')).toHaveAttribute('role', 'status');
    await expect(page.locator('#toastContainer')).toHaveAttribute('aria-live', 'polite');
    await expect(page.locator('#courseSearch')).toHaveAccessibleName('Search courses');
    await expect(page.locator('#sortSelect')).toHaveAccessibleName('Sort courses');
    await expect(page.locator('.milestone-details')).not.toHaveAttribute('open', '');
    await expect(page.locator('#milestoneSummaryValue')).not.toHaveText('View milestones');
    await page.locator('.milestone-details summary').click();
    await expect(page.locator('.milestone-details')).toHaveAttribute('open', '');
    const milestoneLabels = await page.locator('.mp-dot-label').allTextContents();
    expect(milestoneLabels).toContain('P1 · Y4+');
    expect(milestoneLabels).toContain('P2 · Y4+');
    expect(milestoneLabels).toContain('P3 · ALL');
    const timelineColors = await page.locator('.mp-fill').evaluate(element => (
        getComputedStyle(element).backgroundImage
    ));
    const timelineReveal = await page.locator('.mp-fill').evaluate(element => ({
        clipPath: element.style.clipPath,
        transform: getComputedStyle(element).transform,
    }));
    expect(timelineReveal.clipPath).toMatch(/^inset\(/);
    expect(timelineReveal.transform).toBe('none');
    const passedDotColors = await page.locator('.mp-dot.passed').evaluateAll(dots => (
        dots.map(dot => getComputedStyle(dot).backgroundColor)
    ));
    for (const dotColor of passedDotColors) expect(timelineColors).toContain(dotColor);

    const departmentToggle = page.locator('#departmentToggle');
    await expect(departmentToggle).toBeVisible();
    await expect(departmentToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#jumpToNav')).toBeHidden();
    await departmentToggle.click();
    await expect(departmentToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#jumpToNav')).toBeVisible();
    expect(await page.locator('#jumpToNav a').count()).toBeGreaterThan(0);
    const departmentSearch = page.locator('#departmentSearch');
    await expect(departmentSearch).toBeVisible();
    await expect(departmentSearch).toBeFocused();
    await departmentSearch.fill('math');
    await expect(page.locator('#jumpToNav a:visible')).toHaveCount(1);
    await expect(page.locator('#jumpToNav a:visible')).toHaveText('MATH');
    await page.locator('#jumpToNav a:visible').click();
    await expect(page.locator('#departmentPanel')).toBeHidden();
    await waitForScrollToSettle(page);
    await expect(page.locator('#dept-MATH')).toBeInViewport();
    const departmentHeadingY = (await page.locator('#dept-MATH').boundingBox())?.y;
    expect(departmentHeadingY).toBeGreaterThanOrEqual(12);
    expect(departmentHeadingY).toBeLessThanOrEqual(32);

    await page.keyboard.press('/');
    await expect(page.locator('#courseSearch')).toBeFocused();
    await page.locator('#courseSearch').fill('ANT');
    await expect(page.locator('#clearSearch')).toBeVisible();
    const clearButtonGeometry = await page.locator('#clearSearch').evaluate(element => {
        const inputRect = document.querySelector('#courseSearch').getBoundingClientRect();
        const buttonRect = element.getBoundingClientRect();
        const visual = getComputedStyle(element, '::before');
        return {
            contained: buttonRect.top >= inputRect.top
                && buttonRect.right <= inputRect.right
                && buttonRect.bottom <= inputRect.bottom,
            hitTarget: Math.round(buttonRect.width),
            rightInset: Math.round(inputRect.right - buttonRect.right),
            visualWidth: Math.round(buttonRect.width
                - parseFloat(visual.left) - parseFloat(visual.right)),
        };
    });
    expect(clearButtonGeometry).toEqual({
        contained: true,
        hitTarget: 44,
        rightInset: 2,
        visualWidth: 34,
    });
    await page.locator('#clearSearch').click();
    await expect(page.locator('#courseSearch')).toHaveValue('');
    await expect(page.locator('#clearSearch')).toBeHidden();

    const manifestPointerUrl = await page.locator('body').getAttribute('data-manifest-url');
    expect(manifestPointerUrl).toBeTruthy();
    expect(manifestPointerUrl).toMatch(/^\/data\/[^/]+\/manifest\.json$/);
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
    await expect(firstCourse).toHaveJSProperty('tagName', 'BUTTON');
    const firstCode = await firstCourse.getAttribute('data-course');
    const firstDepartment = firstCode.split(' ')[0];
    await firstCourse.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page).toHaveURL(/\/courses\/fall-2026\/.+\/\?v=[A-Za-z0-9_-]{8}$/);
    await expect.poll(() => page.evaluate(() => (
        document.querySelector('#modalOverlay')?.contains(document.activeElement)
    ))).toBe(true);
    expect(blobRequests()).toHaveLength(2);
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/\/semesters\/fall-2026\/$/);
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
    const semesterReminder = page.locator('#modalSemester');
    const openCourseCode = await page.locator('#modalCourseCode').textContent();
    await expect(semesterReminder).toHaveText('Fall 2026');
    await expect(page.locator('#modalOverlay')).toHaveAccessibleName(
        new RegExp(`${openCourseCode}.*Fall 2026`),
    );
    expect(await semesterReminder.evaluate(element => ({
        followsCourseName: element.previousElementSibling?.id === 'modalCourseName',
        isVisuallySecondary: (
            Number.parseFloat(getComputedStyle(element).fontSize)
            < Number.parseFloat(getComputedStyle(element.previousElementSibling).fontSize)
        ),
    }))).toEqual({ followsCourseName: true, isVisuallySecondary: true });
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'aria-describedby',
        'chartSummary',
    );
    await expect(page.locator('#chartSummary')).toContainText(/observations\. Latest:/);

    const firstSection = page.locator('.section-item').first();
    await expect(firstSection).toHaveJSProperty('tagName', 'BUTTON');
    await firstSection.click();
    await expect(firstSection).toHaveAttribute('aria-pressed', 'true');

    await page.keyboard.press('Escape');

    const allFilter = page.locator('.filter-btn[data-filter="all"]');
    const openFilter = page.locator('.filter-btn[data-filter="open"]');
    await openFilter.click();
    await expect(openFilter).toHaveClass(/active/);
    await expect(openFilter).toHaveAttribute('aria-pressed', 'true');
    await expect(allFilter).not.toHaveClass(/active/);
    await expect(allFilter).toHaveAttribute('aria-pressed', 'false');
    await allFilter.click();
    await expect(allFilter).toHaveClass(/active/);
    await expect(allFilter).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#courseSearch').fill(firstCode);
    await expect(page.locator('.course-cell:not(.hidden)')).toHaveCount(1);
    await page.locator('#courseSearch').fill('');

    await page.locator('.filter-btn[data-filter="starred"]').click();
    await expect(page.locator('.empty-state')).toContainText('No bookmarked courses yet');
    await expect(page.locator('#jumpToNav a')).toHaveCount(0);
    await expect(departmentToggle).toBeHidden();

    await page.locator('.filter-btn[data-filter="all"]').click();
    await page.locator('.course-cell').first().click();
    await page.locator('#modalBookmark').click();
    await page.keyboard.press('Escape');
    const telegramAction = page.locator('#telegramBookmarkImport');
    await expect(telegramAction).toBeVisible();
    await expect(telegramAction).toHaveText('Copy for bot');
    await expect(telegramAction).toHaveAccessibleName(
        'Copy 1 starred course for the Telegram bot',
    );
    expect(await telegramAction.evaluate(element => (
        !element.closest('.filter-buttons') && element.getBoundingClientRect().height >= 44
    ))).toBe(true);
    await telegramAction.focus();
    await expect(telegramAction).toBeFocused();

    expect(failedRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});

test('starred-course Telegram export is contextual, portable, and responsive', async ({ browser }) => {
    const context = await browser.newContext({
        permissions: ['clipboard-read', 'clipboard-write'],
    });
    const page = await context.newPage();
    await page.goto('/semesters/summer-2026/');
    const courseCode = await page.locator('.course-cell').first().getAttribute('data-course');
    await page.evaluate(code => {
        localStorage.setItem('courseBookmarks', JSON.stringify([code]));
    }, courseCode);
    await page.reload();

    const action = page.locator('#telegramBookmarkImport');
    await expect(action).toBeVisible();
    await expect(action).toHaveText('Copy for bot');
    await expect(action).toHaveAccessibleName('Copy 1 starred course for the Telegram bot');
    expect(await action.evaluate(element => !element.closest('.filter-buttons'))).toBe(true);
    await action.focus();
    await expect(action).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#toastContainer')).toContainText(
        'Copied. Paste this into Registrar Monitor on Telegram.',
    );
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
        `/import\nSummer 2026\n${courseCode}`,
    );

    await page.setViewportSize({ width: 390, height: 844 });
    expect(await action.evaluate(element => ({
        outsideFilters: !element.closest('.filter-buttons'),
        height: element.getBoundingClientRect().height,
    }))).toEqual({ outsideFilters: true, height: 44 });
    await context.close();
});

test('clean live course route opens the existing modal on a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const response = await page.goto('/courses/fall-2026/ant-101/');
    expect(response?.ok()).toBe(true);
    await expect(page).toHaveURL(/\/courses\/fall-2026\/ant-101\/\?v=[A-Za-z0-9_-]{8}$/);
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#modalCourseCode')).toHaveText('ANT 101');
    await expect(page.locator('#courseAvailability')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('Nazarbayev University');
});

test('explicit Share copies the same current state identity as the visible live URL', async ({ browser }) => {
    const context = await browser.newContext({
        permissions: ['clipboard-read', 'clipboard-write'],
    });
    const page = await context.newPage();
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    await page.locator('.course-cell[data-course="ANT 101"]').click();
    await expect(page).toHaveURL(/\/courses\/fall-2026\/ant-101\/\?v=[A-Za-z0-9_-]{8}$/);
    const visibleUrl = page.url();

    await page.locator('#modalShareLink').click();

    await expect(page.locator('#toastContainer')).toContainText('Share link copied');
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(visibleUrl);
    await context.close();
});

test('elective filters compose and either-category courses appear in both groups', async ({ page }) => {
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    const electiveFilter = page.locator('#electiveFilter');
    await expect(electiveFilter).toHaveAccessibleName('Filter by elective requirement');
    await expect(page.locator('.elective-disclaimer')).toContainText(
        'Double-check the electives against your degree requirements (handbooks).',
    );

    await electiveFilter.selectOption('social-science');
    await expect(page.locator('.course-cell[data-course="LING 131"]')).toBeVisible();
    await expect(page.locator('.course-cell[data-course="HST 104"]')).toBeHidden();
    await expect(page.locator('.course-cell:not(.hidden)')).not.toHaveCount(0);

    await electiveFilter.selectOption('humanities');
    await expect(page.locator('.course-cell[data-course="LING 131"]')).toBeVisible();
    await expect(page.locator('.course-cell[data-course="HST 104"]')).toBeVisible();
    await expect(page.locator('.course-cell[data-course="HST 100"]')).toBeHidden();

    await electiveFilter.selectOption('natural-science');
    await expect(page.locator('.course-cell[data-course^="BIOL "]:not(.hidden)').first()).toBeVisible();
    await expect(page.locator('.course-cell[data-course="LING 131"]')).toBeHidden();

    await electiveFilter.selectOption('all');
    await expect(page.locator('.course-cell[data-course="HST 100"]')).toBeVisible();
});

test('archived clean course route stays unversioned and opens its final modal', async ({ page }) => {
    const response = await page.goto('/courses/summer-2026/ant-110/');
    expect(response?.ok()).toBe(true);
    await expect(page).toHaveURL(/\/courses\/summer-2026\/ant-110\/$/);
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#modalCourseCode')).toHaveText('ANT 110');
});

test('required-type-full courses use compact cards and an explained chart interval', async ({ page }) => {
    const response = await page.goto('/semesters/summer-2026/');
    expect(response?.ok()).toBe(true);
    await page.locator('#courseSearch').fill('CHME 403');
    const course = page.locator('.course-cell[data-course="CHME 403"]');
    await expect(course).toBeVisible();
    await expect(course.locator('.course-fill')).toHaveText('FULL');
    await expect(course).toHaveAttribute(
        'aria-label',
        'CHME 403: LAB + LECTURE FULL. No registration places - all Lab and Lecture sections are full.',
    );

    await course.click();
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#courseState')).toBeHidden();
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-registration-unavailable-intervals',
        /^[1-9]\d*$/,
    );
    await expect(page.locator('#chartSummary')).toContainText(
        /registration-unavailable interval/,
    );
    await expect(page.locator('#registrationUnavailableGuide')).toBeVisible();
    await expect(page.locator('#registrationUnavailableGuide')).toHaveText(
        'Required sections full',
    );

    await page.locator('#modalCloseBtn').click();
    await page.locator('#courseSearch').fill('ANT 110');
    const ordinaryFullCourse = page.locator('.course-cell[data-course="ANT 110"]');
    await expect(ordinaryFullCourse.locator('.course-fill')).toHaveText('FULL');
    await ordinaryFullCourse.click();
    await expect(page.locator('#courseState')).toBeHidden();
    await expect(page.locator('#registrationUnavailableGuide')).toBeHidden();
});

test('mobile dashboard keeps stats, timeline, and controls precisely aligned', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('#courseGrid')).toBeVisible();
    await page.locator('.milestone-details summary').click();

    const layout = await page.evaluate(() => {
        const centerX = element => {
            const rect = element.getBoundingClientRect();
            return rect.left + (rect.width / 2);
        };
        const detailsRect = document.querySelector('.milestone-details').getBoundingClientRect();
        const labels = [...document.querySelectorAll('.mp-dot-label')]
            .filter(element => getComputedStyle(element).display !== 'none')
            .map(element => element.getBoundingClientRect());
        const statsAligned = [...document.querySelectorAll('.stat')].every(stat => (
            Math.abs(centerX(stat.querySelector('.stat-value'))
                - centerX(stat.querySelector('.stat-label'))) < 1
        ));
        const timelineTitle = document.querySelector('.milestone-details summary > span');
        const firstDot = document.querySelector('.mp-dot');
        const departmentToggle = document.querySelector('#departmentToggle');
        const departmentCount = document.querySelector('#departmentCount');
        const toolbarControls = [
            document.querySelector('#courseSearch'),
            document.querySelector('.filter-btn'),
            document.querySelector('#electiveFilter'),
            document.querySelector('#sortSelect'),
            departmentToggle,
        ];
        const feedbackControls = [
            document.querySelector('.filter-btn'),
            document.querySelector('#electiveFilter'),
            document.querySelector('#sortSelect'),
            departmentToggle,
            document.querySelector('.course-cell'),
            document.querySelector('.milestone-details summary'),
        ];
        return {
            controlsAligned: toolbarControls.every(control => (
                Math.abs(control.getBoundingClientRect().height - 44) < 1
            )),
            controlsMove: feedbackControls.every(control => (
                getComputedStyle(control).transitionProperty.includes('transform')
            )),
            departmentFontMatches: getComputedStyle(departmentToggle).fontSize
                === getComputedStyle(departmentCount).fontSize,
            labelsContained: labels.every(rect => (
                rect.left >= detailsRect.left
                && rect.right <= detailsRect.right
                && rect.top >= detailsRect.top
                && rect.bottom <= detailsRect.bottom
            )),
            statsAligned,
            timelineAligned: Math.abs(
                timelineTitle.getBoundingClientRect().left - centerX(firstDot),
            ) <= 4,
        };
    });

    expect(layout).toEqual({
        controlsAligned: true,
        controlsMove: true,
        departmentFontMatches: true,
        labelsContained: true,
        statsAligned: true,
        timelineAligned: true,
    });

    await page.locator('#departmentToggle').click();
    await page.locator('#departmentSearch').fill('math');
    await page.locator('#jumpToNav a:visible').click();
    await waitForScrollToSettle(page);
    const mobileDepartmentHeadingY = (await page.locator('#dept-MATH').boundingBox())?.y;
    expect(mobileDepartmentHeadingY).toBeGreaterThanOrEqual(12);
    expect(mobileDepartmentHeadingY).toBeLessThanOrEqual(32);

    await expect(page.locator('.course-cell').first()).toBeVisible();
    for (const filter of ['full', 'near', 'open']) {
        const button = page.locator(`.filter-btn[data-filter="${filter}"]`);
        await button.click();
        await expect.poll(() => button.evaluate(element => (
            getComputedStyle(element, '::before').backgroundColor
                === getComputedStyle(element).color
        ))).toBe(true);
        await expect(button).toHaveAttribute('aria-pressed', 'true');
    }
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

    const response = await page.goto('/semesters/summer-2026/');
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

    await page.route('**/data/summer-2026/manifest.json', async route => {
        await route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({ ...pointer, previous: null }),
        });
    });
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
    const response = await page.goto('/semesters/summer-2026/');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.error-state')).toContainText(
        'Failed to load enrollment data',
    );
    await expect(page.locator('#courseGrid .course-cell')).toHaveCount(0);
    expect(pageErrors).toEqual([]);
});

test('course deep links open the modal after the verified summary loads', async ({ page }) => {
    const response = await page.goto('/semesters/summer-2026/');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.course-cell').first()).toBeVisible();

    const firstCourse = page.locator('.course-cell').first();
    const firstCode = await firstCourse.getAttribute('data-course');
    const deepLink = firstCode.replace(/\s+/g, '-');
    await page.goto(`/semesters/summer-2026/#${deepLink}`);

    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#modalCourseCode')).toHaveText(firstCode);
    await expect(page.locator('#modalCourseName')).not.toBeEmpty();
    await expect(page.locator('#modalTitle')).toHaveAccessibleName(
        new RegExp(`^${firstCode}.*Summer 2026$`),
    );
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

    const response = await page.goto('/semesters/summer-2026/');
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

    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('.course-cell[data-course="MATH 161"]')).toBeVisible();

    const historicalDepartmentPaths = readHistoricalDepartmentPaths('MATH', 'fall-2026');
    expect(jsonRequests.some(pathname => /\/data\/(spring-2026|summer-2026|summer-2025)\//.test(pathname))).toBe(false);

    await page.locator('.course-cell[data-course="MATH 161"]').click();
    await expect(page.locator('#enrollment-chart')).toBeVisible();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'idle',
    );
    await expect(page.locator('#historicalComparisonControls')).toBeVisible();
    const chartWrapperBeforeComparison = await page.locator('.chart-wrapper').boundingBox();
    const chartAreaBeforeComparison = await page.locator('#enrollment-chart')
        .getAttribute('data-chart-area');
    expect(jsonRequests.some(pathname => /\/data\/(spring-2026|summer-2026|summer-2025)\//.test(pathname))).toBe(false);
    expect(jsonRequests.filter(pathname => historicalDepartmentPaths.includes(pathname))).toHaveLength(0);

    const toggle = page.locator('#historicalComparisonToggle');
    await expect(toggle).toHaveAccessibleName(/find an earlier semester/i);
    await toggle.click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(toggle).toContainText(/Fall 2025/);
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('1');
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-synthetic-points',
        '0',
    );
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-end-kind',
        'observation',
    );
    await expect.poll(async () => {
        const current = await page.locator('.chart-wrapper').boundingBox();
        return Math.max(
            Math.abs(current.x - chartWrapperBeforeComparison.x),
            Math.abs(current.width - chartWrapperBeforeComparison.width),
            Math.abs(current.height - chartWrapperBeforeComparison.height),
        );
    }).toBeLessThanOrEqual(2);
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-chart-area',
        chartAreaBeforeComparison,
    );
    expect(jsonRequests.some(pathname => /\/data\/(summer-2026|summer-2025)\//.test(pathname))).toBe(false);
    expect(jsonRequests.filter(pathname => historicalDepartmentPaths.includes(pathname))).toHaveLength(1);

    await page.locator('.chart-mode-btn[data-mode="timeline"]').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');

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

test('continuous phased time works on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    const response = await page.goto('/semesters/fall-2025/#MATH-161');
    expect(response?.ok()).toBe(true);
    await expect(page.locator('#enrollment-chart')).toBeVisible();

    const canvas = page.locator('#enrollment-chart');
    await canvas.scrollIntoViewIfNeeded();
    await expect(page.locator('.chart-wrapper')).toHaveCSS(
        'touch-action',
        'pan-y',
    );
    const box = await canvas.boundingBox();
    expect(box).toBeTruthy();
    await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.5);
    await expect(canvas).toHaveAttribute('data-hover-timestamp', /\d+/);
    const firstTimestamp = Number(await canvas.getAttribute('data-hover-timestamp'));
    await page.mouse.move(
        box.x + box.width * 0.75,
        box.y + box.height * 0.5,
        { steps: 8 },
    );
    await expect.poll(async () => Number(await canvas.getAttribute('data-hover-timestamp')))
        .toBeGreaterThan(firstTimestamp);

    await expect(canvas).toHaveAttribute('data-tooltip-density', 'compact');
    await expect.poll(async () => Number(await canvas.getAttribute('data-tooltip-width')))
        .toBeGreaterThan(0);
    expect(Number(await canvas.getAttribute('data-tooltip-width'))).toBeLessThanOrEqual(260);

    const pinnedX = box.x + box.width * 0.75;
    const pinnedY = box.y + box.height * 0.5;
    const chartAreaBeforePin = await canvas.getAttribute('data-chart-area');
    const canvasBeforePin = await canvas.boundingBox();
    await page.mouse.click(pinnedX, pinnedY);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'true');
    await expect(page.locator('.chart-readout-pinned')).toHaveText('Pinned');
    await expect(page.locator('.chart-readout-context')).toContainText(/P\d · /);
    expect(await page.locator('.chart-readout-pinned').evaluate(element => (
        element.getBoundingClientRect().right
            <= element.parentElement.getBoundingClientRect().right
            && element.parentElement.getBoundingClientRect().right
                - element.getBoundingClientRect().right < 1
    ))).toBe(true);
    await expect(canvas).toHaveAttribute('data-chart-area', chartAreaBeforePin);
    expect(await canvas.boundingBox()).toEqual(canvasBeforePin);
    const pinnedReadout = await page.locator('#chartReadout').innerText();
    const pinnedTimestamp = await canvas.getAttribute('data-hover-timestamp');
    await page.mouse.move(box.x + box.width * 0.2, pinnedY);
    await expect.poll(() => page.locator('#chartReadout').innerText()).toBe(pinnedReadout);
    await expect(canvas).toHaveAttribute('data-hover-timestamp', pinnedTimestamp);
    await page.mouse.move(box.x, box.y - 10);
    await expect(page.locator('#chartReadout')).not.toHaveAttribute('hidden', '');
    await page.mouse.click(pinnedX, pinnedY);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'false');
    await expect(page.locator('#chartReadout')).toHaveAttribute('hidden', '');

    await expect(page.locator('#chartZoomReset')).toBeVisible();
    await expect(page.locator('#chartZoomReset')).toBeDisabled();
    await expect(page.locator('#chartLegend')).toBeHidden();
    const chartControlHeights = await page.locator('.chart-mode-btn').evaluateAll(buttons => (
        buttons.map(button => Math.round(button.getBoundingClientRect().height))
    ));
    expect(chartControlHeights).toEqual([44, 44, 44]);
    await expect(page.locator('#modalCloseBtn')).toBeVisible();
    expect(await page.locator('#modalOverlay').evaluate(element => (
        element.scrollWidth <= element.clientWidth
    ))).toBe(true);
});

test('narrow full-capacity graph states keep the readout stable and unclipped', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 });
    const response = await page.goto('/courses/summer-2026/bus-101/');
    expect(response?.ok()).toBe(true);

    const canvas = page.locator('#enrollment-chart');
    const readout = page.locator('#chartReadout');
    const chartWrapper = page.locator('.chart-wrapper');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    await expect(canvas).toHaveAttribute('data-enrollment-line-style', 'solid');

    const initialReadoutBox = await readout.boundingBox();
    const initialWrapperBox = await chartWrapper.boundingBox();
    expect(Math.round(initialReadoutBox.height)).toBe(84);
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox).toBeTruthy();

    let fullCapacityX = null;
    for (const fraction of [0.55, 0.65, 0.75, 0.85, 0.95]) {
        const x = canvasBox.x + canvasBox.width * fraction;
        await page.mouse.move(x, canvasBox.y + canvasBox.height * 0.5);
        await expect(canvas).toHaveAttribute('data-hover-timestamp', /\d+/);
        await expect(readout).toBeVisible();
        expect(Math.round((await readout.boundingBox()).height)).toBe(84);
        expect((await chartWrapper.boundingBox()).y).toBeCloseTo(initialWrapperBox.y, 0);
        expect(await readout.evaluate(element => ({
            contentFitsVertically: element.scrollHeight <= element.clientHeight,
            contentFitsHorizontally: element.scrollWidth <= element.clientWidth,
        }))).toEqual({
            contentFitsVertically: true,
            contentFitsHorizontally: true,
        });
        const text = await readout.innerText();
        if (text.includes('100% full') && text.includes('100% opening')) fullCapacityX = x;
    }
    expect(fullCapacityX).not.toBeNull();

    await page.mouse.click(fullCapacityX, canvasBox.y + canvasBox.height * 0.5);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'true');
    const pinned = readout.locator('.chart-readout-pinned');
    await expect(pinned).toBeVisible();
    expect(Math.round((await readout.boundingBox()).height)).toBe(84);
    expect((await chartWrapper.boundingBox()).y).toBeCloseTo(initialWrapperBox.y, 0);
    expect(await readout.evaluate(element => {
        const badge = element.querySelector('.chart-readout-pinned').getBoundingClientRect();
        const card = element.getBoundingClientRect();
        const content = [
            ...element.querySelectorAll('.chart-readout-title, .chart-readout-context'),
        ].flatMap(item => [...item.getClientRects()]);
        const overlapsBadge = content.some(rect => !(
            rect.right <= badge.left
            || rect.left >= badge.right
            || rect.bottom <= badge.top
            || rect.top >= badge.bottom
        ));
        return {
            badgeInsideCard: badge.top >= card.top
                && badge.right <= card.right
                && badge.bottom <= card.bottom,
            contentFitsVertically: element.scrollHeight <= element.clientHeight,
            overlapsBadge,
        };
    })).toEqual({
        badgeInsideCard: true,
        contentFitsVertically: true,
        overlapsBadge: false,
    });

    await page.mouse.click(fullCapacityX, canvasBox.y + canvasBox.height * 0.5);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'false');
    await expect(readout).toHaveAttribute('hidden', '');
    expect(Math.round((await readout.boundingBox()).height)).toBe(84);
    expect((await chartWrapper.boundingBox()).y).toBeCloseTo(initialWrapperBox.y, 0);

    for (const mode of ['snapshots', 'timeline', 'phased']) {
        await page.locator(`.chart-mode-btn[data-mode="${mode}"]`).click();
        expect(Math.round((await readout.boundingBox()).height)).toBe(84);
        expect((await chartWrapper.boundingBox()).y).toBeCloseTo(initialWrapperBox.y, 0);
    }
});

test('touch dragging inspects the chart without trapping vertical scrolling', async ({ browser }) => {
    const context = await browser.newContext({
        viewport: { width: 360, height: 740 },
        hasTouch: true,
        isMobile: true,
    });
    const page = await context.newPage();
    await page.goto('/semesters/fall-2025/#MATH-161');

    const canvas = page.locator('#enrollment-chart');
    const chartWrapper = page.locator('.chart-wrapper');
    const readout = page.locator('#chartReadout');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    const wrapperTopBeforeTouch = (await chartWrapper.boundingBox()).y;
    expect(box).toBeTruthy();
    const canvasTopBeforeTouch = box.y;

    const client = await context.newCDPSession(page);
    const y = box.y + box.height * 0.5;
    const startX = box.x + box.width * 0.2;
    const middleX = box.x + box.width * 0.55;
    const endX = box.x + box.width * 0.8;
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x: startX, y }],
    });
    await expect(canvas).toHaveAttribute('data-hover-timestamp', /\d+/);
    const firstTimestamp = Number(await canvas.getAttribute('data-hover-timestamp'));
    await expect(readout).toBeVisible();
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: middleX, y: box.y - 20 }],
    });
    await expect.poll(async () => Number(await canvas.getAttribute('data-hover-timestamp')))
        .toBeGreaterThan(firstTimestamp);
    const middleTimestamp = Number(await canvas.getAttribute('data-hover-timestamp'));
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: endX, y: box.y - 20 }],
    });
    await expect.poll(async () => Number(await canvas.getAttribute('data-hover-timestamp')))
        .toBeGreaterThan(middleTimestamp);
    const readoutTime = await page.locator('.chart-readout-title').textContent();
    const expectedReadoutTime = await canvas.evaluate(element => (
        new Date(Number(element.dataset.hoverTimestamp)).toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        })
    ));
    expect(readoutTime).toBe(expectedReadoutTime);
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchEnd',
        touchPoints: [],
    });

    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'false');
    await expect(page.locator('.chart-readout-pinned')).toHaveCount(0);
    await expect(readout).toBeVisible();
    await expect(readout).toContainText(/Enrollment|Capacity/);
    await expect(readout.locator('.chart-readout-context')).toContainText(/→|Until|Since/);
    await expect(readout).toHaveCSS('position', 'relative');
    await expect.poll(async () => (await chartWrapper.boundingBox()).y)
        .toBeCloseTo(wrapperTopBeforeTouch, 0);
    await expect.poll(async () => (await canvas.boundingBox()).y)
        .toBeCloseTo(canvasTopBeforeTouch, 0);
    const readoutBox = await readout.boundingBox();
    const canvasBox = await canvas.boundingBox();
    const wrapperBox = await chartWrapper.boundingBox();
    expect(canvasBox.y).toBeCloseTo(wrapperBox.y, 0);
    expect(canvasBox.y + canvasBox.height)
        .toBeLessThanOrEqual(wrapperBox.y + wrapperBox.height);
    expect(readoutBox.width).toBeLessThanOrEqual(canvasBox.width);
    expect(readoutBox.height).toBeLessThanOrEqual(85);
    expect(readoutBox.y + readoutBox.height).toBeLessThanOrEqual(canvasBox.y);
    await expect(readout).toHaveCSS('align-content', 'center');
    expect(await readout.locator('.chart-readout-context').evaluate(element => (
        getComputedStyle(element).textOverflow
    ))).toBe('clip');
    expect((await readout.locator('.chart-readout-label').allTextContents())
        .every(text => !text.includes('…'))).toBe(true);
    const enrollmentRow = readout.locator('.chart-readout-line').filter({ hasText: 'Enrollment' });
    await expect(enrollmentRow.locator('.chart-readout-label')).toHaveText('Enrollment');
    await expect(enrollmentRow.locator('.chart-readout-value')).toContainText(/\d+\/\d+/);
    expect(await readout.evaluate(element => {
        const style = getComputedStyle(element);
        return style.borderLeftWidth === style.borderRightWidth;
    })).toBe(true);
    await expect(canvas).toHaveAttribute('data-touch-mode', 'inspect');
    await expect(page.locator('.chart-container')).toHaveCSS('user-select', 'none');
    expect(await page.evaluate(() => window.getSelection()?.toString() || '')).toBe('');

    await page.locator('.modal-course-name').tap();
    await expect(readout).toHaveAttribute('hidden', '');

    await expect(page.locator('.chart-wrapper')).toHaveCSS(
        'touch-action',
        'pan-y',
    );

    for (const mode of ['snapshots', 'timeline']) {
        await page.locator(`.chart-mode-btn[data-mode="${mode}"]`).click();
        await expect(canvas).toHaveAttribute('data-touch-mode', 'inspect');
        const modeBox = await canvas.boundingBox();
        await canvas.evaluate(element => {
            delete element.dataset.hoverTimestamp;
        });
        await client.send('Input.dispatchTouchEvent', {
            type: 'touchStart',
            touchPoints: [{
                x: modeBox.x + modeBox.width * 0.5,
                y: modeBox.y + modeBox.height * 0.5,
            }],
        });
        await expect(canvas).toHaveAttribute('data-hover-timestamp', /\d+/);
        await client.send('Input.dispatchTouchEvent', {
            type: 'touchEnd',
            touchPoints: [],
        });
        await expect(readout).toBeVisible();
    }
    await context.close();
});

test('pinch zoom enables one-finger chart panning on mobile', async ({ browser }) => {
    const context = await browser.newContext({
        viewport: { width: 360, height: 740 },
        hasTouch: true,
        isMobile: true,
    });
    const page = await context.newPage();
    await page.goto('/semesters/fall-2025/#MATH-161');

    const canvas = page.locator('#enrollment-chart');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    expect(box).toBeTruthy();
    const client = await context.newCDPSession(page);
    const centerX = box.x + box.width / 2;
    const centerY = box.y + box.height / 2;
    const wrapperTopBeforeZoom = (await page.locator('.chart-wrapper').boundingBox()).y;

    await client.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [
            { x: centerX - 20, y: centerY },
            { x: centerX + 20, y: centerY },
        ],
    });
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [
            { x: centerX - 80, y: centerY },
            { x: centerX + 80, y: centerY },
        ],
    });
    await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

    await expect(canvas).toHaveAttribute('data-touch-mode', 'pan');
    await expect(page.locator('.chart-wrapper')).toHaveCSS('touch-action', 'none');
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'false');
    await expect(page.locator('#chartZoomReset')).toBeVisible();
    await expect(page.locator('#chartZoomReset')).toBeEnabled();
    await expect.poll(async () => (await page.locator('.chart-wrapper').boundingBox()).y)
        .toBeCloseTo(wrapperTopBeforeZoom, 0);
    const readout = page.locator('#chartReadout');
    const readoutBeforePan = await readout.boundingBox();
    const beforePan = Number(await canvas.getAttribute('data-viewport-min'));

    await client.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x: centerX, y: centerY }],
    });
    await client.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: centerX - 70, y: centerY }],
    });
    await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(async () => Number(await canvas.getAttribute('data-viewport-min')))
        .not.toBe(beforePan);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'false');
    expect(await page.evaluate(() => window.getSelection()?.toString() || '')).toBe('');
    const readoutAfterPan = await readout.boundingBox();
    expect(readoutAfterPan.x).toBeCloseTo(readoutBeforePan.x, 0);
    expect(readoutAfterPan.y).toBeCloseTo(readoutBeforePan.y, 0);
    await expect.poll(async () => (await page.locator('.chart-wrapper').boundingBox()).y)
        .toBeCloseTo(wrapperTopBeforeZoom, 0);

    await context.close();
});

test('desktop pinned readout follows the data beneath its fixed cursor while panning', async ({ page }) => {
    await page.goto('/semesters/fall-2025/#MATH-161');
    const canvas = page.locator('#enrollment-chart');
    await expect(canvas).toBeVisible();
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    expect(box).toBeTruthy();
    const cursorX = box.x + box.width * 0.6;
    const cursorY = box.y + box.height * 0.5;

    await page.mouse.click(cursorX, cursorY);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'true');
    await page.mouse.move(cursorX, cursorY);
    await page.mouse.wheel(0, -500);
    await expect(page.locator('#chartZoomReset')).toBeEnabled();
    const beforePan = Number(await canvas.getAttribute('data-viewport-min'));
    const readoutBeforePan = await page.locator('#chartReadout').innerText();

    await page.mouse.move(cursorX, cursorY);
    await page.mouse.down();
    await page.mouse.move(cursorX - 100, cursorY, { steps: 8 });
    await page.mouse.up();

    await expect.poll(async () => Number(await canvas.getAttribute('data-viewport-min')))
        .not.toBe(beforePan);
    await expect(canvas).toHaveAttribute('data-tooltip-pinned', 'true');
    await expect.poll(() => page.locator('#chartReadout').innerText())
        .not.toBe(readoutBeforePan);
});

test('historical comparison prefers the prior year of the same semester', async ({ page }) => {
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    const course = page.locator('.course-cell[data-course="KAZ 368"]');
    await expect(course).toBeVisible();
    await course.click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'idle',
    );

    await page.locator('#historicalComparisonToggle').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalComparisonToggle')).toHaveAttribute(
        'aria-pressed',
        'true',
    );
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-datasets',
        '1',
    );
    await expect(page.locator('#historicalAlignmentNote')).toHaveCount(0);
});

test('historical course comparison renders when the current course has no chart history', async ({ page }) => {
    const current = readSemesterManifestFixture('fall-2026');
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

    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    await page.locator(`.course-cell[data-course="${courseCode}"]`).click();
    await expect(page.locator('#enrollment-chart')).toBeVisible();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'idle',
    );
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-datasets',
        '0',
    );

    await page.locator('#historicalComparisonToggle').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'enabled',
    );
    await expect(page.locator('#historicalComparisonToggle')).toHaveAttribute(
        'aria-pressed',
        'true',
    );
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('1');
});

test('historical comparison reports no history instead of remaining in a fetching state', async ({ page }) => {
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);

    await page.locator('.course-cell[data-course="ANT 233"]').click();
    const controls = page.locator('#historicalComparisonControls');
    const toggle = page.locator('#historicalComparisonToggle');
    await expect(controls).toHaveAttribute('data-state', 'idle');

    await toggle.click();

    await expect(controls).toHaveAttribute('data-state', 'unavailable');
    await expect(toggle).toHaveText('No history');
    await expect(toggle).toBeDisabled();
});

test('professor history reports no history when the course is absent from earlier semesters', async ({ page }) => {
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);

    await page.locator('.course-cell[data-course="ANT 233"]').click();
    const section = page.locator('#section-001');
    await expect(section).toBeVisible();
    await section.click();

    const controls = page.locator('#historicalComparisonControls');
    const toggle = page.locator('#historicalComparisonToggle');
    await expect(controls).toHaveAttribute('data-state', 'unavailable');
    await expect(toggle).toHaveText('No history');
    await expect(toggle).toBeDisabled();
});

test('professor no-history state returns to the current course view without reload', async ({ page }) => {
    const current = readSemesterManifestFixture('fall-2026');
    const currentDepartmentUrl = new URL(
        current.manifest.departments.MATH.url,
        `http://127.0.0.1${current.manifestPath}`,
    );
    const currentPayload = readSiteJson(currentDepartmentUrl.pathname.slice(1));
    currentPayload.courses['MATH 161'].sections['3L'].instructor = 'Never Taught Before';
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
    const response = await page.goto('/semesters/fall-2026/');
    expect(response?.ok()).toBe(true);
    await page.locator('.course-cell[data-course="MATH 161"]').click();
    const section = page.locator('#section-3L');
    await expect(section).toBeVisible();
    await section.click();
    const controls = page.locator('#historicalComparisonControls');
    const toggle = page.locator('#historicalComparisonToggle');
    await expect(controls).toHaveAttribute('data-state', 'unavailable');
    await expect(toggle).toHaveText('No history');

    await section.click();

    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(controls).toHaveAttribute('data-state', 'available');
    await expect(toggle).toHaveText('Fall 2025');
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-datasets',
        '0',
    );
});

test('chart drag ending on the backdrop does not dismiss or activate the page', async ({ page }) => {
    await page.goto('/semesters/fall-2025/#MATH-161');
    const canvas = page.locator('#enrollment-chart');
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    expect(box).toBeTruthy();
    const selectedBefore = await page.locator('.modal-course-code').textContent();

    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(2, 2, { steps: 8 });
    await page.mouse.up();

    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('.modal-course-code')).toHaveText(selectedBefore);

    await page.locator('#modalOverlay').click({ position: { x: 2, y: 2 } });
    await expect(page.locator('#modalOverlay')).not.toHaveClass(/active/);
});

test('full course styling and mobile selects preserve semantic and accessible sizing @webkit', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/semesters/fall-2026/');
    const fullCourse = page.locator('.course-cell').first();
    await expect(fullCourse).toBeVisible();
    await fullCourse.evaluate(element => element.classList.add('full'));
    const resting = await fullCourse.evaluate(element => getComputedStyle(element).backgroundImage);
    await fullCourse.focus();
    const focused = await fullCourse.evaluate(element => ({
        background: getComputedStyle(element).backgroundImage,
        focus: getComputedStyle(element).boxShadow,
    }));
    expect(resting).toContain('linear-gradient');
    expect(focused.background).toBe(resting);
    expect(focused.focus).not.toBe('none');

    for (const selector of ['#sortSelect', '#electiveFilter']) {
        const metrics = await page.locator(selector).evaluate(element => {
            const style = getComputedStyle(element);
            return {
                height: element.getBoundingClientRect().height,
                fontSize: Number.parseFloat(style.fontSize),
            };
        });
        expect(metrics.height).toBeGreaterThanOrEqual(44);
        expect(metrics.fontSize).toBeLessThanOrEqual(16);
    }
});

test('selecting a section switches to professor comparison and deselecting returns to course mode', async ({ page }) => {
    const current = readSemesterManifestFixture('fall-2026');
    const historical = readSemesterManifestFixture('fall-2025');
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
    historicalPayload.courses[courseCode].events.push(
        { eventType: 'section_removed', sectionCode: '1L', timestampIdx: 291 },
        { eventType: 'section_removed', sectionCode: '1R', timestampIdx: 291 },
    );

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

    const response = await page.goto('/semesters/fall-2026/');
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
    await expect(page.locator('#historicalComparisonToggle')).toHaveAccessibleName(
        /Hide Fall 2025: Jane Smith/,
    );
    await expect.poll(() => page.locator('#enrollment-chart').getAttribute('data-historical-datasets'))
        .toBe('1');
    await expect(page.locator('#enrollment-chart')).toHaveAttribute(
        'data-historical-end-kind',
        'removal',
    );

    await page.locator('#section-1L').click();
    await expect(page.locator('#historicalComparisonControls')).toHaveAttribute(
        'data-state',
        'available',
    );
    await expect(page.locator('#historicalComparisonToggle')).toHaveAttribute('aria-pressed', 'false');
});
