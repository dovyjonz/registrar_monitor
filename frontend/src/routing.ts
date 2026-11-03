import { semesterToSlug } from './urlSlugs.mjs';
import { adaptPreviewCourseState } from './manifestData.mjs';
import { appState, getData, refreshCourseMaps, resolveCourseRoute, summaryCourses } from './state';
import { openCourse, openCourseModal, renderCourseDetails } from './modal';

export function getSemesterSlug() {
    return semesterToSlug(getData().sem || '');
}

async function getCourseRouteIdentity(courseCode) {
    return resolveCourseRoute({
        semesterSlug: getSemesterSlug(),
        courseCode,
        archived: document.body.dataset.pageArchived === 'true',
        initialCourse: document.body.dataset.initialCourse,
        initialToken: document.body.dataset.previewHash,
    });
}

export async function showCourseRoute(courseCode) {
    try {
        const identity = await getCourseRouteIdentity(courseCode);
        if (appState.selectedCourse !== courseCode) return identity;
        appState.selectedCourseShareUrl = new URL(identity.sharePath, window.location.origin).href;
        history.replaceState(null, '', identity.sharePath);
        return identity;
    } catch (error) {
        console.error(`Failed to resolve share identity for ${courseCode}:`, error);
        return null;
    }
}

export function handleHashNavigation() {
    if (document.body.dataset.initialCourse) return;
    const hash = window.location.hash.slice(1); // Remove '#'
    if (!hash) return;
    const courseCode = hash.replace(/-/g, ' '); // 'CSCI-101' -> 'CSCI 101'
    refreshCourseMaps();
    if (summaryCourses.has(courseCode)) {
        openCourse(courseCode);
    }
}

function replaceDisplayedVersion() {
    const hash = document.body.dataset.previewHash;
    if (!hash || document.body.dataset.pageArchived === 'true') return;
    const url = new URL(window.location.href);
    if (url.searchParams.get('v') === hash) return;
    url.searchParams.set('v', hash);
    history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

export async function handleInitialCourseNavigation() {
    const courseCode = document.body.dataset.initialCourse;
    if (!courseCode) return false;
    replaceDisplayedVersion();
    refreshCourseMaps();
    if (summaryCourses.has(courseCode)) {
        await openCourse(courseCode);
        return true;
    }

    const stateUrl = document.body.dataset.previewStateUrl;
    if (!stateUrl) return false;
    try {
        const response = await fetch(stateUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json();
        if (state.hash !== document.body.dataset.previewHash || state.code !== courseCode) {
            throw new Error('Preview state identity mismatch');
        }
        const course = adaptPreviewCourseState(state);
        course.previewState = state;
        const requestVersion = ++appState.courseRequestVersion;
        openCourseModal(courseCode, course);
        renderCourseDetails(courseCode, course, requestVersion);
        return true;
    } catch (error) {
        console.error(`Failed to open archived course ${courseCode}:`, error);
        return false;
    }
}

window.addEventListener('hashchange', handleHashNavigation);
