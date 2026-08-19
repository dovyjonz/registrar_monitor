import { courseToSlug } from './urlSlugs.mjs';

const PREVIEW_TOKEN = /^[A-Za-z0-9_-]{8}$/;

async function loadPreviewToken(cleanPath) {
    const response = await fetch(cleanPath, { cache: 'no-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    return page.body.dataset.previewHash;
}

export function createCourseRouteResolver(loadToken = loadPreviewToken) {
    const identities = new Map();

    return async function resolveCourseRoute({
        semesterSlug,
        courseCode,
        archived = false,
        initialCourse = null,
        initialToken = null,
    }) {
        const cacheKey = `${semesterSlug}:${courseCode}:${archived}:${initialToken || ''}`;
        if (identities.has(cacheKey)) return identities.get(cacheKey);

        const request = (async () => {
            const cleanPath = `/courses/${semesterSlug}/${courseToSlug(courseCode)}/`;
            if (archived) return { cleanPath, sharePath: cleanPath, archived: true };

            const token = initialCourse === courseCode
                ? initialToken
                : await loadToken(cleanPath);
            if (!PREVIEW_TOKEN.test(token || '')) {
                throw new Error('Course route has no valid preview identity');
            }
            return {
                cleanPath,
                sharePath: `${cleanPath}?v=${token}`,
                archived: false,
            };
        })();
        identities.set(cacheKey, request);
        try {
            return await request;
        } catch (error) {
            identities.delete(cacheKey);
            throw error;
        }
    };
}
