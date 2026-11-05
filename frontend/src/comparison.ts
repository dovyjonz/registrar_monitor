import { prioritizeHistoricalSemesters, semesterToSlug } from './urlSlugs.mjs';
import { loadDepartmentPayload, loadSemesterManifest } from './manifestData.mjs';
import { buildAverageChartPoints, buildCourseChartDomain, buildProfessorAverageChartPoints, courseHasProfessor, normalizeInstructorName } from './chartMapping.mjs';
import { appState, dataLoadController, getCourseDepartment, getCourseSections, getCurrentSemesterLabel, historicalDepartmentPayloads, historicalSemesterManifests, historicalSemesterSummaries, resolvedCourseComparisons, resolvedProfessorComparisons } from './state';
import { renderChart } from './chart';

function getHistoricalSemesterCandidates() {
    if (!appState.staticManifest) return [];
    const currentSemester = getCurrentSemesterLabel();
    const links = [...document.querySelectorAll<HTMLElement>('.semester-nav-link')];
    const currentIndex = links.findIndex(link => (
        link.classList.contains('active')
        || link.textContent.trim() === currentSemester
    ));
    const earlierLinks = currentIndex >= 0 ? links.slice(currentIndex + 1) : links;
    const earlierSemesters = earlierLinks
        .map(link => link.textContent.trim())
        .filter(semester => semester && semester !== currentSemester);
    return prioritizeHistoricalSemesters(currentSemester, earlierSemesters)
        .map(semester => ({ semester, slug: semesterToSlug(semester) }));
}

function getHistoricalSnapshots(course, payload) {
    if (Array.isArray(course?.sn) && course.sn.length > 0) return course.sn;
    return (payload?.timestamps || []).map(ts => ({ ts }));
}

function loadHistoricalSemester(candidate) {
    if (historicalSemesterManifests.has(candidate.semester)) {
        return historicalSemesterManifests.get(candidate.semester);
    }

    const request = Promise.resolve().then(async () => {
        const pointerUrl = new URL(
            `/data/${candidate.slug}/manifest.json`,
            window.location.href,
        ).href;
        const loaded = await loadSemesterManifest(pointerUrl, {
            signal: dataLoadController.signal,
        });
        historicalSemesterSummaries.set(candidate.semester, loaded.payload);
        return {
            ...candidate,
            ...loaded,
        };
    });
    historicalSemesterManifests.set(candidate.semester, request);
    request.catch(() => {
        historicalSemesterManifests.delete(candidate.semester);
        historicalSemesterSummaries.delete(candidate.semester);
    });
    return request;
}

function loadHistoricalDepartmentPayload(candidate, department) {
    const semesterCache = historicalDepartmentPayloads.get(candidate.semester) || new Map();
    historicalDepartmentPayloads.set(candidate.semester, semesterCache);
    return loadDepartmentPayload(
        department,
        candidate.manifest,
        candidate.manifestUrl,
        semesterCache,
        { signal: dataLoadController.signal },
    );
}

async function findEarlierCourseCandidate(courseCode) {
    let lastError = null;
    let sawCandidate = false;
    for (const candidate of getHistoricalSemesterCandidates()) {
        try {
            const loaded = await loadHistoricalSemester(candidate);
            const summaryCourse = loaded.payload?.data?.cr?.[courseCode];
            if (summaryCourse) {
                sawCandidate = true;
                return { ...loaded, summaryCourse };
            }
        } catch (error) {
            lastError = error;
        }
    }
    return { candidate: null, error: lastError, sawCandidate };
}

async function findEarlierProfessorCandidate(courseCode, professorIdentity) {
    let lastError = null;
    for (const candidate of getHistoricalSemesterCandidates()) {
        let loaded;
        try {
            loaded = await loadHistoricalSemester(candidate);
        } catch (error) {
            lastError = error;
            continue;
        }

        const summaryCourse = loaded.payload?.data?.cr?.[courseCode];
        if (!summaryCourse) continue;

        try {
            const department = getCourseDepartment(summaryCourse, courseCode);
            const payload = await loadHistoricalDepartmentPayload(loaded, department);
            const course = payload?.courses?.[courseCode];
            if (!course) continue;
            const snapshots = getHistoricalSnapshots(course, payload);
            if (courseHasProfessor(course, professorIdentity, snapshots)) {
                return {
                    ...loaded,
                    historicalPayload: payload,
                    historicalCourse: course,
                    summaryCourse,
                };
            }
        } catch (error) {
            lastError = error;
        }
    }
    return { candidate: null, error: lastError };
}

function displayInstructorName(value) {
    if (typeof value !== 'string') return '';
    return value.normalize('NFKC').trim().replace(/\s+/gu, ' ');
}

function getHistoricalComparisonLabel(descriptor, action = 'Show') {
    if (!descriptor) return `${action} an earlier semester comparison`;
    if (descriptor.mode === 'professor') {
        return `${action} ${descriptor.semester}: ${descriptor.professorDisplayName}`;
    }
    return `${action} ${descriptor.semester} course aggregate`;
}

export function updateHistoricalLegend() {
    const legend = document.getElementById('chartLegend');
    const capacityVisible = appState.currentEnrollmentData.some(point => Number.isFinite(point.capacityLevel));
    legend?.classList.toggle('visible', capacityVisible);
}

function setHistoricalComparisonState(status, descriptor = appState.historicalComparisonDescriptor) {
    appState.historicalComparisonStatus = status;
    appState.historicalComparisonDescriptor = descriptor;
    const controls = document.getElementById('historicalComparisonControls');
    const toggle = document.getElementById('historicalComparisonToggle');
    if (!controls || !toggle) return;

    controls.dataset.state = status;
    if (status === 'hidden') {
        controls.hidden = false;
        (toggle as HTMLButtonElement).disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Checking for an earlier semester comparison');
        toggle.textContent = 'Checking history…';
        return;
    }

    controls.hidden = false;
    if (status === 'loading') {
        (toggle as HTMLButtonElement).disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Historical comparison is loading');
        toggle.textContent = 'Finding earlier…';
        return;
    }

    if (status === 'idle') {
        controls.hidden = false;
        (toggle as HTMLButtonElement).disabled = false;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Find an earlier semester comparison');
        toggle.textContent = 'Compare semesters';
        return;
    }

    if (status === 'unavailable') {
        (toggle as HTMLButtonElement).disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'No qualifying earlier comparison is available');
        toggle.textContent = 'No history';
        return;
    }

    if (status === 'failed') {
        (toggle as HTMLButtonElement).disabled = false;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', getHistoricalComparisonLabel(descriptor, 'Retry'));
        toggle.textContent = 'Retry comparison';
        return;
    }

    const enabled = status === 'enabled';
    (toggle as HTMLButtonElement).disabled = false;
    toggle.setAttribute('aria-pressed', String(enabled));
    toggle.setAttribute(
        'aria-label',
        getHistoricalComparisonLabel(descriptor, enabled ? 'Hide' : 'Show'),
    );
    toggle.textContent = descriptor?.semester || 'Earlier semester';
}

export function resetHistoricalComparisonState() {
    appState.comparisonRequestVersion += 1;
    appState.historicalComparisonEnabled = false;
    appState.historicalComparisonStatus = 'hidden';
    appState.historicalComparisonMode = 'course';
    appState.historicalComparisonDescriptor = null;
    appState.historicalComparisonData = null;
    setHistoricalComparisonState('hidden', null);
    updateHistoricalLegend();
}

export function initializeHistoricalComparisonControl(course) {
    if (!appState.staticManifest) {
        setHistoricalComparisonState('hidden', null);
        return;
    }
    const mode = appState.selectedSection ? 'professor' : 'course';
    appState.historicalComparisonMode = mode;
    if (mode === 'professor') {
        const section = getCourseSections(course)[appState.selectedSection];
        const professorIdentity = normalizeInstructorName(section?.instructor ?? section?.in);
        const professorDisplayName = displayInstructorName(section?.instructor ?? section?.in);
        const descriptor = {
            mode,
            professorIdentity,
            professorDisplayName: professorDisplayName || 'selected professor',
        };
        if (!professorIdentity) {
            setHistoricalComparisonState('unavailable', descriptor);
        } else {
            setHistoricalComparisonState('idle', descriptor);
        }
        return;
    }
    setHistoricalComparisonState('idle', { mode: 'course' });
}

export function hasHistoricalCandidateCache(courseCode) {
    const identity = appState.historicalComparisonDescriptor?.professorIdentity;
    if (appState.selectedSection && identity) {
        return resolvedProfessorComparisons.has(
            `${getCurrentSemesterLabel()}|${courseCode}|${identity}`,
        );
    }
    return resolvedCourseComparisons.has(`${getCurrentSemesterLabel()}|${courseCode}`);
}

function isCurrentComparisonRequest(courseCode, requestVersion, token) {
    return appState.selectedCourse === courseCode
        && requestVersion === appState.courseRequestVersion
        && token === appState.comparisonRequestVersion;
}

async function resolveProfessorAvailability(courseCode, course, requestVersion, token) {
    const section = getCourseSections(course)[appState.selectedSection];
    const professorIdentity = normalizeInstructorName(section?.instructor ?? section?.in);
    const professorDisplayName = displayInstructorName(section?.instructor ?? section?.in);
    const descriptorBase = {
        mode: 'professor',
        professorIdentity,
        professorDisplayName: professorDisplayName || 'selected professor',
    };
    if (!professorIdentity) {
        setHistoricalComparisonState('unavailable', descriptorBase);
        updateHistoricalLegend();
        return;
    }
    setHistoricalComparisonState('loading', descriptorBase);
    const cacheKey = `${getCurrentSemesterLabel()}|${courseCode}|${professorIdentity}`;
    if (!resolvedProfessorComparisons.has(cacheKey)) {
        const request = findEarlierProfessorCandidate(courseCode, professorIdentity);
        resolvedProfessorComparisons.set(cacheKey, request);
        request.catch(() => resolvedProfessorComparisons.delete(cacheKey));
    }
    try {
        const result = await resolvedProfessorComparisons.get(cacheKey);
        if (!isCurrentComparisonRequest(courseCode, requestVersion, token)) return;
        if (result?.candidate === null) {
            if (result.error) resolvedProfessorComparisons.delete(cacheKey);
            setHistoricalComparisonState(
                result.error ? 'failed' : 'unavailable',
                descriptorBase,
            );
            return;
        }
        const descriptor = {
            ...descriptorBase,
            semester: result.semester,
            candidate: result,
        };
        appState.historicalComparisonDescriptor = descriptor;
        setHistoricalComparisonState('available', descriptor);
    } catch {
        if (isCurrentComparisonRequest(courseCode, requestVersion, token)) {
            setHistoricalComparisonState('failed', descriptorBase);
        }
    }
}

async function resolveCourseAvailability(courseCode, requestVersion, token) {
    setHistoricalComparisonState('loading', { mode: 'course' });
    const cacheKey = `${getCurrentSemesterLabel()}|${courseCode}`;
    if (!resolvedCourseComparisons.has(cacheKey)) {
        const request = findEarlierCourseCandidate(courseCode);
        resolvedCourseComparisons.set(cacheKey, request);
        request.catch(() => resolvedCourseComparisons.delete(cacheKey));
    }
    try {
        const result = await resolvedCourseComparisons.get(cacheKey);
        if (!isCurrentComparisonRequest(courseCode, requestVersion, token)) return;
        if (result?.candidate === null) {
            if (result.error) resolvedCourseComparisons.delete(cacheKey);
            setHistoricalComparisonState(
                result.error ? 'failed' : 'unavailable',
                { mode: 'course' },
            );
            return;
        }
        const descriptor = {
            mode: 'course',
            semester: result.semester,
            candidate: result,
        };
        appState.historicalComparisonDescriptor = descriptor;
        setHistoricalComparisonState('available', descriptor);
    } catch {
        if (isCurrentComparisonRequest(courseCode, requestVersion, token)) {
            setHistoricalComparisonState('failed', { mode: 'course' });
        }
    }
}

export async function resolveHistoricalAvailability(courseCode, course, requestVersion) {
    if (requestVersion !== appState.courseRequestVersion || appState.selectedCourse !== courseCode) return;
    const token = ++appState.comparisonRequestVersion;
    appState.historicalComparisonEnabled = false;
    appState.historicalComparisonData = null;
    appState.historicalComparisonMode = appState.selectedSection ? 'professor' : 'course';

    if (!appState.staticManifest) {
        setHistoricalComparisonState('hidden', null);
        updateHistoricalLegend();
        return;
    }
    if (appState.historicalComparisonMode === 'professor') {
        await resolveProfessorAvailability(courseCode, course, requestVersion, token);
    } else {
        await resolveCourseAvailability(courseCode, requestVersion, token);
    }
}

async function buildHistoricalComparisonData(descriptor, courseCode = appState.selectedCourse) {
    const candidate = descriptor?.candidate;
    if (!candidate) throw new Error('Historical comparison candidate is missing');

    let course = candidate.historicalCourse;
    let payload = candidate.historicalPayload;
    if (!course) {
        const department = getCourseDepartment(candidate.summaryCourse, courseCode);
        payload = await loadHistoricalDepartmentPayload(candidate, department);
        course = payload?.courses?.[courseCode];
    }
    if (!course) throw new Error(`Missing historical department data for ${courseCode}`);

    const snapshots = getHistoricalSnapshots(course, payload);
    const chartPoints = descriptor.mode === 'professor'
        ? buildProfessorAverageChartPoints(
            course,
            descriptor.professorIdentity,
            snapshots,
        )
        : buildAverageChartPoints(course, snapshots);
    if (chartPoints.length === 0) {
        throw new Error('Historical comparison has no usable enrollment history');
    }
    return {
        mode: descriptor.mode,
        semester: candidate.semester,
        professorDisplayName: descriptor.professorDisplayName,
        chartPoints,
        chartDomain: buildCourseChartDomain(course, snapshots),
        milestones: candidate.payload?.milestones || [],
        contributingSectionCounts: chartPoints.map(point => point.contributingSections ?? null),
    };
}

export async function enableHistoricalComparison() {
    if (!appState.selectedCourse || !appState.historicalComparisonDescriptor) return;
    const requestVersion = appState.courseRequestVersion;
    const token = appState.comparisonRequestVersion;
    if (appState.historicalComparisonEnabled) {
        appState.historicalComparisonEnabled = false;
        setHistoricalComparisonState('available', appState.historicalComparisonDescriptor);
        updateHistoricalLegend();
        if (appState.lastRenderArgs) {
            const args = appState.lastRenderArgs;
            await renderChart(
                args.chartLabel,
                args.chartPoints,
                args.chartDomain,
                { requestVersion: args.requestVersion },
            );
        }
        return;
    }

    setHistoricalComparisonState('loading', appState.historicalComparisonDescriptor);
    try {
        if (!appState.historicalComparisonData) {
            appState.historicalComparisonData = await buildHistoricalComparisonData(
                appState.historicalComparisonDescriptor,
                appState.selectedCourse,
            );
        }
        if (!isCurrentComparisonRequest(appState.selectedCourse, requestVersion, token)) return;
        appState.historicalComparisonEnabled = true;
        setHistoricalComparisonState('enabled', appState.historicalComparisonDescriptor);
        updateHistoricalLegend();
        if (appState.lastRenderArgs) {
            const args = appState.lastRenderArgs;
            await renderChart(
                args.chartLabel,
                args.chartPoints,
                args.chartDomain,
                {
                    requestVersion: args.requestVersion,
                    historicalComparison: appState.historicalComparisonData,
                },
            );
        }
    } catch (error) {
        if (!isCurrentComparisonRequest(appState.selectedCourse, requestVersion, token)) return;
        appState.historicalComparisonEnabled = false;
        setHistoricalComparisonState('failed', appState.historicalComparisonDescriptor);
        updateHistoricalLegend();
        console.error('Failed to load historical comparison:', error);
        if (appState.lastRenderArgs) {
            const args = appState.lastRenderArgs;
            try {
                await renderChart(
                    args.chartLabel,
                    args.chartPoints,
                    args.chartDomain,
                    { requestVersion: args.requestVersion },
                );
            } catch (fallbackError) {
                console.error('Failed to restore current enrollment chart:', fallbackError);
            }
        }
    }
}

export function mapHistoricalComparisonPoints(
    historicalComparison,
    historicalMapper,
    historicalCoordinateMapper,
) {
    return historicalComparison.chartPoints
        .map((point, index) => ({
            x: historicalCoordinateMapper.mapX(historicalMapper.xValues[index]),
            y: point.enrollmentLevel ?? point.fill,
            sourceIndex: point.sourceIndex ?? index,
            ...(point.removalEnded ? { removalEnded: true } : {}),
        }))
        .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
}
