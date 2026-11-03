import { expect, test } from '@playwright/test';
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
    await expect(page.locator('html')).not.toHaveClass(/modal-open/);

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
    await expect(departmentToggle).toBeDisabled();

    await page.locator('.filter-btn[data-filter="all"]').click();
    await page.locator('.course-cell').first().click();
    await page.locator('#modalBookmark').click();
    await page.keyboard.press('Escape');
    await expect(page.locator('html')).not.toHaveClass(/modal-open/);
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

test('catalog search explains title and instructor results and restores normal sort', async ({ page }) => {
    const requested = [];
    page.on('request', request => requested.push(new URL(request.url()).pathname));
    await page.goto('/semesters/fall-2026/');
    const cells = page.locator('.course-cell:not(.hidden)');
    const search = page.getByRole('searchbox', { name: 'Search courses', exact: true });
    await expect(cells.first()).toBeVisible();
    await page.locator('#sortSelect').selectOption('fill-desc');
    const normalOrder = await cells.evaluateAll(items => items.map(item => item.dataset.course));
    const beforeSearch = [...requested];
    await search.fill('test instructor');
    await expect(page.locator('#searchAnnouncement')).toContainText('Sorted by relevance');
    const codes = await cells.evaluateAll(items => items.map(item => item.dataset.course));
    expect(codes.length).toBeGreaterThan(1);
    expect(codes).toEqual([...codes].sort());
    await expect(cells.first().locator('.search-reason')).toHaveText('Prof. Test Instructor');
    expect(new Set(await cells.evaluateAll(items => items.map(item => Math.round(item.getBoundingClientRect().height))))).toEqual(new Set([76]));
    await page.locator('#electiveFilter').selectOption('humanities');
    await expect(page.locator('[data-course="LING 131"]')).toBeVisible();
    await page.locator('.filter-btn[data-filter="open"]').click();
    expect(await cells.evaluateAll(items => items.every(item => item.dataset.status === 'open'))).toBe(true);
    await page.locator('.filter-btn[data-filter="all"]').click();
    await page.locator('#electiveFilter').selectOption('all');
    await search.fill('ANT 101');
    await expect(page.locator('.course-cell:visible')).toHaveCount(await cells.count());
    await expect(cells.first()).toHaveAttribute('data-course', 'ANT 101');
    await expect(cells.first().locator('.search-reason')).toBeEmpty();
    await search.fill('anthropology');
    await expect(cells.first().locator('.search-reason')).toContainText(/anthropology/i);
    expect(requested.filter(path => !beforeSearch.includes(path))).toEqual([]);
    await page.locator('#clearSearch').click();
    expect(await cells.evaluateAll(items => items.map(item => item.dataset.course))).toEqual(normalOrder);
    await expect(page.locator('#courseGrid')).not.toHaveClass(/search-active/);
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
    await expect(page.locator('html')).not.toHaveClass(/modal-open/);
    await page.locator('#courseSearch').fill('ANT 110');
    const ordinaryFullCourse = page.locator('.course-cell[data-course="ANT 110"]');
    await expect(ordinaryFullCourse.locator('.course-fill')).toHaveText('FULL');
    await ordinaryFullCourse.click();
    await expect(page.locator('#courseState')).toBeHidden();
    await expect(page.locator('#registrationUnavailableGuide')).toBeHidden();

    await page.locator('#modalCloseBtn').click();
    await expect(page.locator('html')).not.toHaveClass(/modal-open/);
    await page.locator('#courseSearch').fill('ANT 111');
    const overCapacityCourse = page.locator('.course-cell[data-course="ANT 111"]');
    await expect(overCapacityCourse.locator('.course-fill')).toHaveText('125%');
    await expect(overCapacityCourse).toHaveAttribute('aria-label', 'ANT 111: 125% full');
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
        ];
        return {
            controlsAligned: toolbarControls.every(control => (
                Math.abs(control.getBoundingClientRect().height - 44) < 1
            )),
            controlsStayStill: feedbackControls.every(control => (
                !getComputedStyle(control).transitionProperty.includes('transform')
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
        controlsStayStill: true,
        departmentFontMatches: true,
        labelsContained: true,
        statsAligned: true,
        timelineAligned: true,
    });

    const primaryGeometry = () => page.locator('.controls-row').evaluate(element => {
        const bounds = element.getBoundingClientRect();
        return [...element.querySelectorAll('input, button, select')].map(control => {
            const rect = control.getBoundingClientRect();
            return [rect.x - bounds.x, rect.y - bounds.y, rect.width, rect.height];
        });
    });
    const beforeExport = await primaryGeometry();
    await page.locator('.course-cell').first().click();
    await page.locator('#modalBookmark').click();
    await page.locator('#modalCloseBtn').click();
    await expect(page.locator('html')).not.toHaveClass(/modal-open/);
    expect(await primaryGeometry()).toEqual(beforeExport);
    expect(await page.locator('#telegramBookmarkImport').evaluate(element => {
        const rect = element.getBoundingClientRect();
        const primary = document.querySelector('.controls-row').getBoundingClientRect();
        return rect.top >= primary.bottom && Math.abs(rect.width - primary.width) < 1;
    })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

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

test('course modal keeps the page stable until its close transition finishes', async ({ page }) => {
    await page.goto('/semesters/fall-2026/');
    const course = page.locator('.course-cell').first();
    await course.click();
    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    const firstSection = page.locator('#sectionTypeSelector .section-item').first();
    await expect(firstSection).toBeVisible();
    const sectionText = await firstSection.textContent();

    await page.evaluate(() => {
        document.documentElement.style.setProperty('--transition-normal', '1s linear');
    });
    await page.locator('#modalCloseBtn').click();

    await expect(page.locator('#modalOverlay')).not.toHaveClass(/active/);
    await expect(firstSection).toHaveText(sectionText);
    expect(await page.evaluate(() => ({
        scrollLocked: document.documentElement.classList.contains('modal-open'),
        mainInert: document.querySelector('#main-content')?.hasAttribute('inert'),
        openerFocused: document.activeElement?.classList.contains('course-cell'),
    }))).toEqual({
        scrollLocked: true,
        mainInert: true,
        openerFocused: false,
    });

    await expect(page.locator('html')).not.toHaveClass(/modal-open/, { timeout: 2_000 });
    await expect(page.locator('#main-content')).not.toHaveAttribute('inert', '');
    await expect(course).toBeFocused();
    await expect(page.locator('#sectionTypeSelector')).toBeHidden();
    await expect(page.locator('#sectionTypeSelector')).toBeEmpty();
    await expect(page).toHaveURL(/\/semesters\/fall-2026\/$/);
});

const MINIAPP_API = 'https://bot.smoke.test/miniapp';

async function stubMiniApp(page, calls) {
    const watches = [];
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
        contentType: 'text/javascript',
        body: 'window.Telegram = { WebApp: { initData: "user=%7B%22id%22%3A1%7D&hash=x", '
            + 'ready() { window.tgReady = true; }, expand() { window.tgExpanded = true; } } };',
    }));
    await page.route(`${MINIAPP_API}/**`, async route => {
        const request = route.request();
        const cors = {
            'access-control-allow-origin': 'http://127.0.0.1:4173',
            'access-control-allow-headers': 'authorization, content-type',
            'access-control-allow-methods': 'GET, POST, DELETE',
        };
        if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
        calls.push({ method: request.method(), authorization: request.headers().authorization });
        if (request.method() === 'POST') {
            watches.push({ id: 'w1', ...request.postDataJSON(), section: request.postDataJSON().section || '' });
        } else if (request.method() === 'DELETE') {
            watches.length = 0;
        }
        return route.fulfill({
            headers: cors,
            json: { semester: 'Fall 2026', watches },
        });
    });
}

test('Telegram launch shows Watch controls that call the bot API', async ({ page }) => {
    const calls = [];
    await stubMiniApp(page, calls);
    await page.goto('/semesters/fall-2026/#tgWebAppData=user%3D1&tgWebAppVersion=8.0');
    await page.locator('.course-cell').first().click();

    const watch = page.getByRole('button', { name: 'Watch course' });
    await expect(watch).toBeVisible();
    expect(await page.evaluate(() => [window.tgReady, window.tgExpanded])).toEqual([true, true]);
    await watch.click();
    await expect(page.getByRole('button', { name: 'Unwatch course' })).toBeVisible();

    await page.locator('.section-item').first().click();
    await expect(page.getByRole('button', { name: /^Watch section / })).toBeDisabled();
    await page.getByRole('button', { name: 'Unwatch course' }).click();
    await expect(page.getByRole('button', { name: /^Watch section / })).toBeEnabled();

    expect(calls.map(call => call.method)).toEqual(['GET', 'POST', 'DELETE']);
    expect(calls.every(call => call.authorization?.startsWith('tma user='))).toBe(true);
});

test('ordinary visitors never load Telegram or see Watch controls', async ({ page }) => {
    const telegramRequests = [];
    page.on('request', request => {
        const { hostname } = new URL(request.url());
        if (hostname === 'telegram.org' || hostname.endsWith('.telegram.org')) {
            telegramRequests.push(request.url());
        }
    });
    await page.goto('/semesters/fall-2026/');
    await page.locator('.course-cell').first().click();

    await expect(page.locator('#modalOverlay')).toHaveClass(/active/);
    await expect(page.locator('#watchBar')).toBeHidden();
    expect(telegramRequests).toEqual([]);
});
