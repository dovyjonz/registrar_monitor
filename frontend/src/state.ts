import { createCourseRouteResolver } from './courseRoutes.mjs';
import { updateTelegramBookmarkImport } from './bookmarks';

export const departmentPayloads = new Map<string, any>();

export const summaryCourses = new Map<string, any>();

export const hydratedCourses = new Map<string, any>();

export const resolveCourseRoute = createCourseRouteResolver();

export const dataLoadController = new AbortController();

window.addEventListener('pagehide', () => dataLoadController.abort(), { once: true });

export const historicalSemesterManifests = new Map<string, any>();

export const historicalSemesterSummaries = new Map<string, any>();

export const historicalDepartmentPayloads = new Map<string, any>();

export const resolvedCourseComparisons = new Map<string, any>();

export const resolvedProfessorComparisons = new Map<string, any>();

// Bookmarks/Favorites State
export const bookmarks = new Set<string>(JSON.parse(localStorage.getItem('courseBookmarks') || '[]'));

/**
 * Get the current semester data.
 */
export function getData() {
    return appState.DATA;
}

/**
 * Keep the startup summary and lazily loaded course details in separate maps.
 * The static manifest's summary objects intentionally contain no section or
 * history data, so they must never be replaced with department details.
 */
export function refreshCourseMaps() {
    const data = getData();
    if (appState.mappedData === data) return;

    summaryCourses.clear();
    hydratedCourses.clear();
    appState.mappedData = data;

    if (!data?.cr) {
        updateTelegramBookmarkImport();
        return;
    }

    for (const [code, course] of Object.entries(data.cr)) {
        summaryCourses.set(code, course);
        // Course details hydrate lazily from the v3 department blob.
    }
    updateTelegramBookmarkImport();
}

export function getSummaryCourse(courseCode) {
    refreshCourseMaps();
    return summaryCourses.get(courseCode) || null;
}

export function getHydratedCourse(courseCode) {
    refreshCourseMaps();
    return hydratedCourses.get(courseCode) || null;
}

export function getCourseDepartment(course, courseCode) {
    return course?.department || course?.d || courseCode.split(' ')[0] || 'Other';
}

export function getCourseTitle(course) {
    return course?.title ?? course?.ti ?? '';
}

export function getCourseSections(course) {
    return course?.sections || course?.s || {};
}

export function getCourseSnapshots(course) {
    const data = getData();
    return course?.sn || data?.sn || [];
}

/**
 * Get the current semester milestones.
 */
export function getMilestones() {
    return appState.MILESTONES;
}

export function getCurrentSemesterLabel() {
    return appState.staticManifest?.semester
        || getData()?.semester
        || getData()?.sem
        || '';
}

export interface State {
    chartJsLoaded: boolean;
    Chart: any;
    chart: any;
    selectedCourse: string | null;
    selectedSection: string | null;
    selectedCourseShareUrl: string | null;
    currentEnrollmentData: any[];
    chartMode: string;
    staticManifest: any;
    staticManifestUrl: string | null;
    staticManifestStale: boolean;
    courseRequestVersion: number;
    mappedData: any;
    lastRenderArgs: any;
    historicalComparisonEnabled: boolean;
    historicalComparisonStatus: string;
    historicalComparisonMode: string;
    historicalComparisonDescriptor: any;
    historicalComparisonData: any;
    comparisonRequestVersion: number;
    DATA: any;
    MILESTONES: any[];
    _currentEvents: any[];
    _eventSortOrder: 'newest' | 'oldest';
    _eventFilterType: string;
    _eventFilterSection: string;
    backdropGesture: any;
    modalOpener: HTMLElement | null;
    currentFilter: string;
}

/** Mutable page state shared by every module. */
export const appState: State = {
    chartJsLoaded: false,
    Chart: null,
    chart: null,
    selectedCourse: null,
    selectedSection: null,
    selectedCourseShareUrl: null,
    currentEnrollmentData: [],
    chartMode: localStorage.getItem('chartMode') || 'phased',
    staticManifest: null,
    staticManifestUrl: null,
    staticManifestStale: false,
    courseRequestVersion: 0,
    mappedData: null,
    lastRenderArgs: null,
    historicalComparisonEnabled: false,
    historicalComparisonStatus: 'hidden',
    historicalComparisonMode: 'course',
    historicalComparisonDescriptor: null,
    historicalComparisonData: null,
    comparisonRequestVersion: 0,
    DATA: null,
    MILESTONES: [],
    _currentEvents: [],
    _eventSortOrder: 'newest',
    _eventFilterType: 'all',
    _eventFilterSection: 'all',
    backdropGesture: null,
    modalOpener: null,
    currentFilter: 'all',
};
