// Pure helpers for the Telegram Mini App: launch detection, request building, and
// the watch buttons shown for a course or section. No DOM or network access.

export function isTelegramLaunch(hash) {
    return new URLSearchParams(String(hash).replace(/^#/, '')).has('tgWebAppData');
}

export function initDataFromHash(hash) {
    return new URLSearchParams(String(hash).replace(/^#/, '')).get('tgWebAppData') || '';
}

export function normalizeApiUrl(value) {
    try {
        const url = new URL(String(value || '').trim());
        if (url.protocol !== 'https:' || url.search || url.hash) return '';
        return url.href.replace(/\/+$/, '');
    } catch {
        return '';
    }
}

export function buildApiRequest(apiUrl, initData, method, path, body) {
    const headers = { Authorization: `tma ${initData}` };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    return {
        url: `${apiUrl}${path}`,
        init: {
            method,
            headers,
            body: body === undefined ? undefined : JSON.stringify(body),
            cache: 'no-store',
            credentials: 'omit',
        },
    };
}

export function errorMessage(status) {
    if (status === 401) return 'Telegram session expired. Reopen the app from the bot.';
    if (status === 404) return 'That course is not in the latest enrollment snapshot.';
    return 'Could not update your watches. Try again.';
}

// Buttons for the open course (and selected section). Returns null when the page
// is not for the semester the bot watches, since watches only cover that semester.
export function watchControls({ watches, activeSemester, semester, course, section }) {
    if (!course || !activeSemester || semester !== activeSemester) return null;
    const mine = watches.filter(watch => watch.semester === semester && watch.course === course);
    const courseWatch = mine.find(watch => !watch.section);
    const controls = [courseWatch
        ? control('remove', 'Unwatch course', { id: courseWatch.id })
        : control('add', 'Watch course', { target: { semester, course } })];
    if (section) {
        const sectionWatch = mine.find(watch => watch.section === section);
        if (sectionWatch) {
            controls.push(control('remove', `Unwatch section ${section}`, { id: sectionWatch.id }));
        } else {
            controls.push(control('add', `Watch section ${section}`, {
                target: { semester, course, section },
                disabled: Boolean(courseWatch),
                hint: courseWatch ? 'Covered by your course watch' : '',
            }));
        }
    }
    return controls;
}

function control(action, label, extra) {
    return { action, label, disabled: false, hint: '', ...extra };
}
