/**
 * Enrollment Monitor - Application JavaScript
 */

import './style.css';
import {
    OBSERVATION_STEP_MODE,
    buildAverageChartPoints,
    buildCourseChartDomain,
    buildProfessorAverageChartPoints,
    buildSectionChartPoints,
    courseHasProfessor,
    createHistoricalCoordinateMapper,
    getChartMapper,
    getEnrollmentScaleMax,
    getXScaleBounds,
    extendSteppedSeriesToDomainEnd,
    limitPointsBeforeFirstMilestone,
    normalizeInstructorName,
} from './chartMapping.mjs';
import {
    courseToSlug,
    getManifestUrl,
    prioritizeHistoricalSemesters,
    semesterToSlug,
} from './urlSlugs.mjs';
import {
    IntegrityError,
    loadDepartmentPayload,
    loadSemesterManifest,
    UnsupportedSchemaError,
} from './manifestData.mjs';

function markPerformance(name) {
    if (typeof performance?.mark === 'function') performance.mark(name);
}

function markAfterAnimationFrames(name, frameCount = 2) {
    if (typeof requestAnimationFrame !== 'function') {
        markPerformance(name);
        return;
    }

    let remaining = frameCount;
    const nextFrame = () => {
        remaining -= 1;
        if (remaining <= 0) {
            markPerformance(name);
            return;
        }
        requestAnimationFrame(nextFrame);
    };
    requestAnimationFrame(nextFrame);
}

// Lazy-loaded Chart.js modules (loaded on first chart use)
let chartJsLoaded = false;
let Chart = null;

async function loadChartJs() {
    if (chartJsLoaded) return;
    const [chartModule, chartHelpers, annotationModule, zoomModule] = await Promise.all([
        import('chart.js/auto'),
        import('chart.js/helpers'),
        import('chartjs-plugin-annotation'),
        import('chartjs-plugin-zoom'),
    ]);
    Chart = chartModule.default;
    chartModule.Tooltip.positioners.glide = function(items, eventPosition) {
        const average = chartModule.Tooltip.positioners.average.call(this, items, eventPosition);
        if (average === false) return false;
        return {
            x: Math.min(Math.max(eventPosition.x, this.chart.chartArea.left), this.chart.chartArea.right),
            y: average.y,
        };
    };
    const getSteppedElementAtX = (meta, pixelX, useFinalPosition = false) => {
        let selected = null;
        for (let index = 0; index < meta.data.length; index++) {
            const element = meta.data[index];
            const properties = element.getProps(['x'], useFinalPosition);
            if (!selected) selected = { element, index };
            if (properties.x <= pixelX) selected = { element, index };
            if (properties.x > pixelX) break;
        }
        return selected;
    };
    chartModule.Interaction.modes.glideStepped = (chartInstance, event, options, useFinalPosition) => {
        const position = chartHelpers.getRelativePosition(event, chartInstance);
        return chartInstance.data.datasets.flatMap((_, datasetIndex) => {
            const meta = chartInstance.getDatasetMeta(datasetIndex);
            if (!meta.visible) return [];
            const selected = getSteppedElementAtX(meta, position.x, useFinalPosition);
            return selected ? [{
                element: selected.element,
                datasetIndex,
                index: selected.index,
            }] : [];
        });
    };
    const hoverIndicator = {
        id: 'hoverIndicator',
        afterEvent(chartInstance, args, options) {
            const { chartArea } = chartInstance;
            const { event } = args;
            const inside = event.type !== 'mouseout'
                && event.x >= chartArea.left && event.x <= chartArea.right
                && event.y >= chartArea.top && event.y <= chartArea.bottom;
            const nextX = inside ? event.x : null;
            if (chartInstance.$hoverPixelX !== nextX) {
                chartInstance.$hoverPixelX = nextX;
                if (Number.isFinite(nextX) && typeof options.unmapX === 'function') {
                    const mappedX = chartInstance.scales.x.getValueForPixel(nextX);
                    chartInstance.canvas.dataset.hoverTimestamp = String(options.unmapX(mappedX));
                } else {
                    delete chartInstance.canvas.dataset.hoverTimestamp;
                }
                args.changed = true;
            }
        },
        afterDatasetsDraw(chartInstance) {
            const pixelX = chartInstance.$hoverPixelX;
            if (!Number.isFinite(pixelX)) return;
            const { ctx } = chartInstance;
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(pixelX, chartInstance.chartArea.top);
            ctx.lineTo(pixelX, chartInstance.chartArea.bottom);
            ctx.lineWidth = 1;
            ctx.strokeStyle = 'rgba(220, 224, 232, 0.32)';
            ctx.stroke();
            chartInstance.data.datasets.forEach((dataset, datasetIndex) => {
                const meta = chartInstance.getDatasetMeta(datasetIndex);
                if (!meta.visible) return;
                const selected = getSteppedElementAtX(meta, pixelX);
                if (!selected) return;
                const { y } = selected.element.getProps(['y'], false);
                if (!Number.isFinite(y)) return;
                ctx.beginPath();
                ctx.arc(pixelX, y, dataset.id === 'enrollment' ? 4 : 3, 0, Math.PI * 2);
                ctx.fillStyle = '#1f3b60';
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.strokeStyle = dataset.borderColor;
                ctx.stroke();
            });
            ctx.restore();
        },
    };
    Chart.register(annotationModule.default, zoomModule.default, hoverIndicator);
    chartJsLoaded = true;
}

// Global state
let chart = null;
let selectedCourse = null;
let selectedSection = null;
let currentEnrollmentData = [];
let chartMode = localStorage.getItem('chartMode') || 'phased'; // 'phased', 'snapshots', or 'timeline'
let staticManifest = null;
let staticManifestUrl = null;
let staticManifestStale = false;
let courseRequestVersion = 0;
const departmentPayloads = new Map();
const summaryCourses = new Map();
const hydratedCourses = new Map();
let mappedData = null;
const dataLoadController = new AbortController();
window.addEventListener('pagehide', () => dataLoadController.abort(), { once: true });

// Cache for last render args so toggle can re-render
let lastRenderArgs = null;

// Historical comparison state is page-session scoped. Promises are cached so
// toggling, changing chart modes, and reopening a course never repeat a
// verified manifest or department request.
let historicalComparisonEnabled = false;
let historicalComparisonStatus = 'hidden';
let historicalComparisonMode = 'course';
let historicalComparisonDescriptor = null;
let historicalComparisonData = null;
let comparisonRequestVersion = 0;
const historicalSemesterManifests = new Map();
const historicalSemesterSummaries = new Map();
const historicalDepartmentPayloads = new Map();
const resolvedCourseComparisons = new Map();
const resolvedProfessorComparisons = new Map();

// The generated semester page receives its v3 summary through the manifest
// pointer in the body data attribute. Combined prototype pages may still
// provide their own in-memory dataset below.
let DATA = null;
let MILESTONES = [];
const COMBINED_DATA = window.COMBINED_DATA;

// Determine mode from data structure
const IS_COMBINED = typeof COMBINED_DATA !== 'undefined';

// For combined mode: active semester state
let activeSemester = IS_COMBINED && COMBINED_DATA
    ? (localStorage.getItem('activeSemester') || COMBINED_DATA.as)
    : null;

// Validate stored semester exists (combined mode)
if (IS_COMBINED && COMBINED_DATA && !COMBINED_DATA.sems.includes(activeSemester)) {
    activeSemester = COMBINED_DATA.as;
}

// Bookmarks/Favorites State
const bookmarks = new Set(JSON.parse(localStorage.getItem('courseBookmarks') || '[]'));

/**
 * Get current semester data based on mode.
 */
function getData() {
    if (IS_COMBINED && COMBINED_DATA) {
        return COMBINED_DATA.sd[activeSemester];
    }
    return DATA;
}

/**
 * Keep the startup summary and lazily loaded course details in separate maps.
 * The static manifest's summary objects intentionally contain no section or
 * history data, so they must never be replaced with department details.
 */
function refreshCourseMaps() {
    const data = getData();
    if (mappedData === data) return;

    summaryCourses.clear();
    hydratedCourses.clear();
    mappedData = data;

    if (!data?.cr) return;

    for (const [code, course] of Object.entries(data.cr)) {
        summaryCourses.set(code, course);
        // Combined prototype pages keep their complete course data in memory;
        // generated semester pages hydrate details from the v3 department blob.
        if (IS_COMBINED) {
            hydratedCourses.set(code, course);
        }
    }
}

function getSummaryCourse(courseCode) {
    refreshCourseMaps();
    return summaryCourses.get(courseCode) || null;
}

function getHydratedCourse(courseCode) {
    refreshCourseMaps();
    return hydratedCourses.get(courseCode) || null;
}

function getCourseDepartment(course, courseCode) {
    return course?.department || course?.d || courseCode.split(' ')[0] || 'Other';
}

function getCourseTitle(course) {
    return course?.title ?? course?.ti ?? '';
}

function getCourseAverageFill(course) {
    const fill = course?.averageFill ?? course?.af;
    return Number.isFinite(fill) ? fill : 0;
}

function getCourseIsFilled(course) {
    return course?.isFilled ?? course?.if ?? false;
}

function getCourseSections(course) {
    return course?.sections || course?.s || {};
}

function getCourseSnapshots(course) {
    const data = getData();
    return course?.sn || data?.sn || [];
}

/**
 * Get current milestones based on mode.
 */
function getMilestones() {
    if (IS_COMBINED) {
        return COMBINED_DATA.md[activeSemester] || [];
    }
    return MILESTONES;
}

function getCurrentSemesterLabel() {
    return staticManifest?.semester
        || getData()?.semester
        || getData()?.sem
        || (IS_COMBINED ? activeSemester : '');
}

function getHistoricalSemesterCandidates() {
    if (IS_COMBINED || !staticManifest) return [];
    const currentSemester = getCurrentSemesterLabel();
    const links = [...document.querySelectorAll('.semester-nav-link')];
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
            `data/${candidate.slug}/manifest.json`,
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
    if (!semesterCache.has(department)) {
        const request = loadDepartmentPayload(
            department,
            candidate.manifest,
            candidate.manifestUrl,
            semesterCache,
            { signal: dataLoadController.signal },
        );
        semesterCache.set(department, request);
        request.catch(() => semesterCache.delete(department));
    }
    return semesterCache.get(department);
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
    let courseFound = false;
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
        courseFound = true;

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
    return { candidate: null, error: lastError, courseFound };
}

function displayInstructorName(value) {
    if (typeof value !== 'string') return '';
    return value.normalize('NFKC').trim().replace(/\s+/gu, ' ');
}

function getHistoricalComparisonLabel(descriptor, action = 'Show') {
    if (!descriptor) return `${action} an earlier semester comparison`;
    if (descriptor.mode === 'professor') {
        return `${action} ${descriptor.semester} · ${descriptor.professorDisplayName}`;
    }
    return `${action} ${descriptor.semester} course aggregate`;
}

function updateHistoricalLegend() {
    const legend = document.getElementById('chartLegend');
    const capacityVisible = currentEnrollmentData.some(point => Number.isFinite(point.capacityLevel));
    legend?.classList.toggle('visible', capacityVisible);
}

function setHistoricalComparisonState(status, descriptor = historicalComparisonDescriptor) {
    historicalComparisonStatus = status;
    historicalComparisonDescriptor = descriptor;
    const controls = document.getElementById('historicalComparisonControls');
    const toggle = document.getElementById('historicalComparisonToggle');
    if (!controls || !toggle) return;

    controls.dataset.state = status;
    if (status === 'hidden') {
        controls.hidden = true;
        toggle.disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Show an earlier semester comparison');
        toggle.textContent = 'Earlier semester';
        return;
    }

    controls.hidden = false;
    if (status === 'loading') {
        toggle.disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Historical comparison is loading');
        toggle.textContent = 'Finding earlier…';
        return;
    }

    if (status === 'idle') {
        controls.hidden = false;
        toggle.disabled = false;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'Find an earlier semester comparison');
        toggle.textContent = 'Compare semesters';
        return;
    }

    if (status === 'unavailable') {
        toggle.disabled = true;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', 'No qualifying earlier comparison is available');
        toggle.textContent = 'No comparison';
        return;
    }

    if (status === 'failed') {
        toggle.disabled = false;
        toggle.setAttribute('aria-pressed', 'false');
        toggle.setAttribute('aria-label', getHistoricalComparisonLabel(descriptor, 'Retry'));
        toggle.textContent = 'Retry comparison';
        return;
    }

    const enabled = status === 'enabled';
    toggle.disabled = false;
    toggle.setAttribute('aria-pressed', String(enabled));
    toggle.setAttribute(
        'aria-label',
        getHistoricalComparisonLabel(descriptor, enabled ? 'Hide' : 'Show'),
    );
    toggle.textContent = descriptor?.semester || 'Earlier semester';
}

function resetHistoricalComparisonState() {
    comparisonRequestVersion += 1;
    historicalComparisonEnabled = false;
    historicalComparisonStatus = 'hidden';
    historicalComparisonMode = 'course';
    historicalComparisonDescriptor = null;
    historicalComparisonData = null;
    setHistoricalComparisonState('hidden', null);
    updateHistoricalLegend();
}

function initializeHistoricalComparisonControl(course) {
    if (IS_COMBINED || !staticManifest) {
        setHistoricalComparisonState('hidden', null);
        return;
    }
    const mode = selectedSection ? 'professor' : 'course';
    historicalComparisonMode = mode;
    if (mode === 'professor') {
        const section = getCourseSections(course)[selectedSection];
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

function hasHistoricalCandidateCache(courseCode) {
    const identity = historicalComparisonDescriptor?.professorIdentity;
    if (selectedSection && identity) {
        return resolvedProfessorComparisons.has(
            `${getCurrentSemesterLabel()}|${courseCode}|${identity}`,
        );
    }
    return resolvedCourseComparisons.has(`${getCurrentSemesterLabel()}|${courseCode}`);
}

function isCurrentComparisonRequest(courseCode, requestVersion, token) {
    return selectedCourse === courseCode
        && requestVersion === courseRequestVersion
        && token === comparisonRequestVersion;
}

async function resolveProfessorAvailability(courseCode, course, requestVersion, token) {
    const section = getCourseSections(course)[selectedSection];
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
            const unavailable = result.courseFound === true;
            if (result.error) resolvedProfessorComparisons.delete(cacheKey);
            setHistoricalComparisonState(
                result.error ? 'failed' : (unavailable ? 'unavailable' : 'hidden'),
                unavailable || result.error ? descriptorBase : null,
            );
            return;
        }
        const descriptor = {
            ...descriptorBase,
            semester: result.semester,
            candidate: result,
        };
        historicalComparisonDescriptor = descriptor;
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
                result.error ? 'failed' : 'hidden',
                result.error ? { mode: 'course' } : null,
            );
            return;
        }
        const descriptor = {
            mode: 'course',
            semester: result.semester,
            candidate: result,
        };
        historicalComparisonDescriptor = descriptor;
        setHistoricalComparisonState('available', descriptor);
    } catch {
        if (isCurrentComparisonRequest(courseCode, requestVersion, token)) {
            setHistoricalComparisonState('failed', { mode: 'course' });
        }
    }
}

async function resolveHistoricalAvailability(courseCode, course, requestVersion) {
    if (requestVersion !== courseRequestVersion || selectedCourse !== courseCode) return;
    const token = ++comparisonRequestVersion;
    historicalComparisonEnabled = false;
    historicalComparisonData = null;
    historicalComparisonMode = selectedSection ? 'professor' : 'course';

    if (IS_COMBINED || !staticManifest) {
        setHistoricalComparisonState('hidden', null);
        updateHistoricalLegend();
        return;
    }
    if (historicalComparisonMode === 'professor') {
        await resolveProfessorAvailability(courseCode, course, requestVersion, token);
    } else {
        await resolveCourseAvailability(courseCode, requestVersion, token);
    }
}

async function buildHistoricalComparisonData(descriptor, courseCode = selectedCourse) {
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

async function enableHistoricalComparison() {
    if (!selectedCourse || !historicalComparisonDescriptor) return;
    const requestVersion = courseRequestVersion;
    const token = comparisonRequestVersion;
    if (historicalComparisonEnabled) {
        historicalComparisonEnabled = false;
        setHistoricalComparisonState('available', historicalComparisonDescriptor);
        updateHistoricalLegend();
        if (lastRenderArgs) {
            const args = lastRenderArgs;
            await renderChart(
                args.chartLabel,
                args.chartPoints,
                args.chartDomain,
                { requestVersion: args.requestVersion },
            );
        }
        return;
    }

    setHistoricalComparisonState('loading', historicalComparisonDescriptor);
    try {
        if (!historicalComparisonData) {
            historicalComparisonData = await buildHistoricalComparisonData(
                historicalComparisonDescriptor,
                selectedCourse,
            );
        }
        if (!isCurrentComparisonRequest(selectedCourse, requestVersion, token)) return;
        historicalComparisonEnabled = true;
        setHistoricalComparisonState('enabled', historicalComparisonDescriptor);
        updateHistoricalLegend();
        if (lastRenderArgs) {
            const args = lastRenderArgs;
            await renderChart(
                args.chartLabel,
                args.chartPoints,
                args.chartDomain,
                {
                    requestVersion: args.requestVersion,
                    historicalComparison: historicalComparisonData,
                },
            );
        }
    } catch (error) {
        if (!isCurrentComparisonRequest(selectedCourse, requestVersion, token)) return;
        historicalComparisonEnabled = false;
        setHistoricalComparisonState('failed', historicalComparisonDescriptor);
        updateHistoricalLegend();
        console.error('Failed to load historical comparison:', error);
        if (lastRenderArgs) {
            const args = lastRenderArgs;
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

function mapHistoricalComparisonPoints(
    historicalComparison,
    historicalMapper,
    historicalCoordinateMapper,
) {
    return historicalComparison.chartPoints
        .map((point, index) => ({
            x: historicalCoordinateMapper.mapX(historicalMapper.xValues[index]),
            y: point.enrollmentLevel ?? point.fill,
            sourceIndex: point.sourceIndex ?? index,
        }))
        .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
}

const DEADLINE_LABELS = new Set(['Drop', 'WL', 'Close']);

function shouldRenderMilestone(milestone, chartMode, dataTimestamps) {
    if (chartMode !== 'phased' && DEADLINE_LABELS.has(milestone.label)) return false;
    const milestoneTime = new Date(milestone.time).getTime();
    if (!Number.isFinite(milestoneTime)) return false;
    if (chartMode !== 'phased' && dataTimestamps.length > 0) {
        const dataMin = Math.min(...dataTimestamps);
        const dataMax = Math.max(...dataTimestamps);
        if (milestoneTime < dataMin || milestoneTime > dataMax) return false;
    }
    return true;
}

function getCurrentMilestoneXValues(milestones, chartMode, dataTimestamps, mapTime) {
    return milestones
        .filter(milestone => shouldRenderMilestone(milestone, chartMode, dataTimestamps))
        .map(milestone => mapTime(new Date(milestone.time).getTime()))
        .filter(Number.isFinite);
}

function addCurrentMilestoneAnnotations({
    annotations,
    milestones,
    chartMode,
    dataTimestamps,
    mapTime,
    xValues,
    fillData,
    historicalDataPoints,
}) {
    const labelXValues = xValues.length > 0
        ? xValues
        : historicalDataPoints.map(point => point.x);
    const labelFillData = fillData.length > 0
        ? fillData
        : historicalDataPoints.map(point => point.y);

    const renderedPriorities = new Set();
    milestones.forEach((milestone, index) => {
        if (!shouldRenderMilestone(milestone, chartMode, dataTimestamps)) return;
        const xPos = mapTime(new Date(milestone.time).getTime());
        if (!Number.isFinite(xPos)) return;

        const priority = milestone.priority ? String(milestone.priority) : '';
        const startsPriority = priority && !renderedPriorities.has(priority);
        if (startsPriority) renderedPriorities.add(priority);
        const milestoneLabel = startsPriority
            ? `P${priority} · ${milestone.label}`
            : milestone.label;

        let closestDataIdx = 0;
        let minDiff2 = Infinity;
        labelXValues.forEach((x, dataIndex) => {
            const difference = Math.abs(x - xPos);
            if (difference < minDiff2) {
                minDiff2 = difference;
                closestDataIdx = dataIndex;
            }
        });
        const fillAtPoint = labelFillData[closestDataIdx] || 0;
        const labelPos = fillAtPoint > 50 ? 'start' : 'end';
        const color = milestone.color || '#78909C';

        annotations[`line${index}`] = {
            type: 'line', xMin: xPos, xMax: xPos,
            borderColor: color, borderWidth: 2, borderDash: [5, 3],
            drawTime: 'beforeDatasetsDraw',
            z: -1,
            label: {
                display: true, content: milestoneLabel, position: labelPos,
                backgroundColor: color, color: getContrastColor(color),
                font: { size: 9, weight: 'bold' }, padding: 3, borderRadius: 3,
                // Keep the line behind the enrollment series, but draw the
                // label afterward so its text and background stay opaque.
                z: 10, drawTime: 'afterDatasetsDraw',
            }
        };
    });
}

function buildHistoricalChartState({
    historicalComparison,
    chartMode,
    currentComparisonDomain,
    currentMapper,
    milestones,
}) {
    if (!historicalComparison?.chartPoints?.length) {
        return { historicalDataPoints: [] };
    }

    const historicalChartPoints = limitPointsBeforeFirstMilestone(
        historicalComparison.chartPoints.map((point, sourceIndex) => ({
            ...point,
            sourceIndex,
        })),
        historicalComparison.milestones,
    );
    const historicalChartDomain = limitPointsBeforeFirstMilestone(
        historicalComparison.chartDomain,
        historicalComparison.milestones,
    );

    const historicalMapper = getChartMapper(
        chartMode,
        historicalChartPoints,
        historicalChartDomain,
        historicalComparison.milestones,
    );
    const historicalDomain = historicalMapper.domainXValues.length > 0
        ? historicalMapper.domainXValues
        : historicalMapper.xValues;
    // A current semester may have only its first observation. Keep the
    // historical shadow drawable until the current domain has two anchors.
    const targetDomain = currentComparisonDomain.length > 1
        ? currentComparisonDomain
        : historicalDomain;
    const alignMilestones = chartMode === 'phased' || currentComparisonDomain.length > 1;
    const historicalCoordinateMapper = createHistoricalCoordinateMapper({
        historicalDomainXValues: historicalDomain,
        currentDomainXValues: targetDomain,
        historicalMilestones: alignMilestones ? historicalComparison.milestones : [],
        currentMilestones: alignMilestones ? milestones : [],
        historicalMapTime: historicalMapper.mapTime,
        currentMapTime: currentMapper.mapTime,
    });

    const historicalDataPoints = mapHistoricalComparisonPoints(
            { ...historicalComparison, chartPoints: historicalChartPoints },
            historicalMapper,
            historicalCoordinateMapper,
        );
    return { historicalDataPoints };
}

function buildMilestoneRenderState({
    chartMode,
    milestones,
    domainTimestamps,
    mapTime,
    xValues,
    fillData,
    historicalDataPoints,
}) {
    const currentMilestoneXValues = getCurrentMilestoneXValues(
        milestones,
        chartMode,
        domainTimestamps,
        mapTime,
    );
    const annotationXValues = chartMode === 'phased' ? [...currentMilestoneXValues] : [];
    const annotations = {};
    if (milestones.length > 0
        && (chartMode === 'phased' || domainTimestamps.length > 0)) {
        addCurrentMilestoneAnnotations({
            annotations,
            milestones,
            chartMode,
            dataTimestamps: domainTimestamps,
            mapTime,
            xValues,
            fillData,
            historicalDataPoints,
        });
    }
    return { annotations, annotationXValues };
}

/**
 * Get contrasting text color (black or white) based on background.
 */
function getContrastColor(hexColor) {
    const hex = hexColor.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? '#1a1a2e' : '#ffffff';
}

/**
 * Format course code for display.
 */
function formatCourseCode(code) {
    const parts = code.split(' ');
    if (parts.length !== 2) return code;
    return `${parts[0]} ${parts[1]}`;
}

/**
 * Get CSS class for fill status.
 */
function getStatusClass(fill, isFilled = false) {
    if (isFilled || fill >= 1.0) return 'full';
    if (fill >= 0.75) return 'near';
    return '';
}

/**
 * Get human-readable section type name.
 */
function getSectionTypeName(type) {
    const names = {
        'L': 'Lecture',
        'S': 'Seminar',
        'R': 'Recitation',
        'D': 'Discussion',
        'B': 'Lab',
        'Lb': 'Lab',
        'Int': 'Internship',
        'P': 'Project',
        'IS': 'Independent Study',
        'T': 'Tutorial',
    };
    return names[type] || type || 'Section';
}

/**
 * Format ISO date string for display.
 */
function formatDate(isoString) {
    if (!isoString) return 'N/A';
    const date = new Date(isoString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Render semester toggle buttons (combined mode only).
 */
function renderSemesterToggle() {
    if (!IS_COMBINED) return;

    const toggle = document.getElementById('semesterToggle');
    if (!toggle) return;

    toggle.textContent = '';
    for (const sem of COMBINED_DATA.sems) {
        const btn = document.createElement('button');
        btn.className = `semester-btn${sem === activeSemester ? ' active' : ''}`;
        btn.textContent = sem;
        btn.addEventListener('click', () => window.switchSemester(sem));
        toggle.appendChild(btn);
    }
}

/**
 * Switch to a different semester (combined mode).
 */
// Export to window so it can be called from HTML onclick
window.switchSemester = function(semester) {
    if (!IS_COMBINED) return;

    activeSemester = semester;
    localStorage.setItem('activeSemester', semester);
    closeModal();
    renderSemesterToggle();
    renderCourseGrid();
}

// Export to window so it can be called from HTML onclick
window.closeModal = closeModal;

/**
 * Render the main course grid.
 */
function getCourseSectionStats(course) {
    if (Number.isInteger(course.sectionCount) && Number.isInteger(course.fullSectionCount)) {
        return {
            sectionCount: course.sectionCount,
            fullSectionCount: course.fullSectionCount,
        };
    }
    const sections = Object.values(getCourseSections(course));
    return {
        sectionCount: sections.length,
        fullSectionCount: sections.filter(section => section.cf >= 1.0).length,
    };
}

function renderJumpToNavigation(sortedDepts) {
    const jumpNav = document.getElementById('jumpToNav');
    if (!jumpNav) return;

    jumpNav.textContent = '';
    for (const dept of sortedDepts) {
        const a = document.createElement('a');
        a.href = `#dept-${dept}`;
        a.textContent = dept;
        jumpNav.appendChild(a);
    }

    const toggle = document.getElementById('departmentToggle');
    const count = document.getElementById('departmentCount');
    const hasDepartments = sortedDepts.length > 0;
    if (count) count.textContent = hasDepartments ? String(sortedDepts.length) : '';
    if (toggle) {
        toggle.hidden = !hasDepartments;
        if (!hasDepartments) toggle.setAttribute('aria-expanded', 'false');
    }
    jumpNav.hidden = !hasDepartments || toggle?.getAttribute('aria-expanded') !== 'true';
}

function renderCourseGrid() {
    const data = getData();
    const grid = document.getElementById('courseGrid');
    if (!grid || !data) return;
    refreshCourseMaps();
    grid.textContent = '';

    // Update header text
    const lastUpdatedEl = document.getElementById('lastUpdated');
    if (lastUpdatedEl) {
        const semester = IS_COMBINED ? activeSemester : (data.semester || data.sem);
        const staleLabel = staticManifestStale ? 'Stale data • ' : '';
        lastUpdatedEl.textContent = `${staleLabel}${semester} • Last updated ${formatDate(
            data.lastReportTime ?? data.lrt,
        )}`;
    }

    // Group the small summary courses by department. The summary already has
    // section counts, so rendering never needs to inspect lazy detail data.
    const deptCourses = {};
    for (const [code, course] of summaryCourses.entries()) {
        const dept = getCourseDepartment(course, code);

        if (!deptCourses[dept]) deptCourses[dept] = [];
        deptCourses[dept].push({ code, ...course });
    }

    // Sort departments alphabetically
    const sortedDepts = Object.keys(deptCourses).sort();

    let totalCourses = 0;
    let totalSections = 0;
    let fullSections = 0;

    for (const dept of sortedDepts) {
        // Department header
        const header = document.createElement('h2');
        header.className = 'dept-header';
        header.id = `dept-${dept}`;
        header.textContent = '';
        const deptSpan = document.createElement('span');
        deptSpan.textContent = dept;
        header.appendChild(deptSpan);
        const topLink = document.createElement('a');
        topLink.href = '#';
        topLink.className = 'back-to-top';
        topLink.textContent = '↑ Top';
        topLink.addEventListener('click', (e) => {
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
        header.appendChild(topLink);
        grid.appendChild(header);

        const courses = deptCourses[dept];
        // Sort courses by code
        courses.sort((a, b) => a.code.localeCompare(b.code));

        for (const course of courses) {
            totalCourses++;
            const { sectionCount, fullSectionCount } = getCourseSectionStats(course);
            totalSections += sectionCount;
            fullSections += fullSectionCount;

            const averageFill = getCourseAverageFill(course);
            const isFilled = getCourseIsFilled(course);
            const status = isFilled || averageFill >= 1 ? 'full' :
                averageFill >= 0.8 ? 'near' : 'open';
            const isStarred = bookmarks.has(course.code);

            const cell = document.createElement('button');
            cell.type = 'button';
            cell.className = `course-cell ${getStatusClass(averageFill, isFilled)}${isStarred ? ' starred' : ''}`;
            cell.setAttribute('data-course', course.code);
            cell.setAttribute('data-status', status);
            cell.setAttribute('data-fill', averageFill);
            cell.setAttribute('aria-label', `${formatCourseCode(course.code)} — ${Math.round(averageFill * 100)}% full`);
            cell.style.setProperty('--cell-index', totalCourses);
            const codeSpan = document.createElement('span');
            codeSpan.className = 'course-code';
            codeSpan.textContent = formatCourseCode(course.code);
            cell.appendChild(codeSpan);
            const fillSpan = document.createElement('span');
            fillSpan.className = 'course-fill';
            fillSpan.textContent = `${Math.round(averageFill * 100)}%`;
            cell.appendChild(fillSpan);
            cell.onclick = () => openCourse(course.code);
            grid.appendChild(cell);
        }
    }

    // Show complete totals immediately; intermediate animated values are misleading.
    document.getElementById('totalCourses').textContent = totalCourses;
    document.getElementById('totalSections').textContent = totalSections;
    document.getElementById('fullSections').textContent = fullSections;
    document.getElementById('snapshotCount').textContent = (
        data.snapshotCount ?? (Array.isArray(data.sn) ? data.sn.length : 0)
    );

    applyFilters();
    markPerformance('registrar:grid-dom-complete');
}

/**
 * Open course detail modal.
 */
async function hydrateCourse(courseCode) {
    refreshCourseMaps();
    if (hydratedCourses.has(courseCode)) {
        return hydratedCourses.get(courseCode);
    }

    const summaryCourse = summaryCourses.get(courseCode);
    if (!summaryCourse) return null;

    // Combined prototype pages keep their complete course data in memory.
    if (IS_COMBINED) {
        hydratedCourses.set(courseCode, summaryCourse);
        return summaryCourse;
    }

    const department = getCourseDepartment(summaryCourse, courseCode);
    const payload = await loadDepartmentPayload(
        department,
        staticManifest,
        staticManifestUrl,
        departmentPayloads,
        { signal: dataLoadController.signal },
    );
    const fullCourse = payload.courses?.[courseCode];
    if (!fullCourse) {
        throw new Error(`Missing department data for course ${courseCode}`);
    }
    hydratedCourses.set(courseCode, fullCourse);
    return fullCourse;
}

const MODAL_DETAIL_IDS = [
    'sectionTypeSelector',
    'sectionList',
    'chartContainer',
    'eventHistory',
];

function setModalDetailVisibility(visible) {
    for (const id of MODAL_DETAIL_IDS) {
        const element = document.getElementById(id);
        if (element) element.hidden = !visible;
    }
}

function resetCourseDetailView() {
    document.getElementById('modalDetailState')?.remove();
    setModalDetailVisibility(false);

    const sectionTypeSelector = document.getElementById('sectionTypeSelector');
    const sectionList = document.getElementById('sectionList');
    const eventHistory = document.getElementById('eventHistory');
    const eventFilters = document.getElementById('eventFilters');
    const eventHistoryList = document.getElementById('eventHistoryList');
    if (sectionTypeSelector) sectionTypeSelector.textContent = '';
    if (sectionList) sectionList.textContent = '';
    if (eventHistory) {
        eventHistory.style.display = 'none';
        eventHistory.removeAttribute('open');
    }
    if (eventFilters) eventFilters.textContent = '';
    if (eventHistoryList) eventHistoryList.textContent = '';

    currentEnrollmentData = [];
    lastRenderArgs = null;
    document.getElementById('chartLegend')?.classList.remove('visible');
    const placeholder = document.getElementById('chartPlaceholder');
    if (placeholder) placeholder.style.display = '';
    document.getElementById('enrollment-chart')?.classList.add('chart-hidden');
    if (chart) {
        chart.destroy();
        chart = null;
    }
    setZoomControlsState(false);
}

function createModalDetailState({ className, role, message }) {
    const body = document.querySelector('.modal-body');
    if (!body) return null;

    const state = document.createElement('div');
    state.id = 'modalDetailState';
    state.className = className;
    state.setAttribute('role', role);
    state.setAttribute('aria-live', role === 'alert' ? 'assertive' : 'polite');
    state.textContent = message;
    body.prepend(state);
    return state;
}

function showModalDetailLoading() {
    resetCourseDetailView();
    const state = createModalDetailState({
        className: 'empty-state modal-detail-state',
        role: 'status',
        message: 'Loading course details…',
    });
    if (state) state.setAttribute('aria-busy', 'true');
}

function classifyDepartmentLoadError(error) {
    if (error instanceof UnsupportedSchemaError || error?.name === 'UnsupportedSchemaError') {
        return 'unsupported';
    }
    if (error instanceof IntegrityError || error?.name === 'IntegrityError') {
        return 'corrupt';
    }

    const message = String(error?.message || error || '');
    if (/missing department data|no static payload|HTTP 404/i.test(message)) {
        return 'missing';
    }
    if (/failed to load .*HTTP|network|fetch|NetworkError|TypeError|AbortError/i.test(message)) {
        return 'network';
    }
    return 'corrupt';
}

function departmentErrorMessage(error) {
    switch (classifyDepartmentLoadError(error)) {
        case 'missing':
            return 'Missing department data. Please retry.';
        case 'corrupt':
            return 'Corrupt or hash-mismatched data. Please retry.';
        case 'unsupported':
            return 'Unsupported schema version for department data. Please retry.';
        case 'network':
        default:
            return 'Network failure while loading department data. Please retry.';
    }
}

function showModalDetailError(courseCode, error, requestVersion) {
    if (requestVersion !== courseRequestVersion || selectedCourse !== courseCode) return;

    resetCourseDetailView();
    const state = createModalDetailState({
        className: 'error-state modal-detail-state',
        role: 'alert',
        message: departmentErrorMessage(error),
    });
    if (!state) return;

    const retryButton = document.createElement('button');
    retryButton.type = 'button';
    retryButton.id = 'retryDepartment';
    retryButton.textContent = 'Retry';
    retryButton.addEventListener('click', () => {
        if (selectedCourse === courseCode) retryCourse(courseCode);
    });
    state.appendChild(retryButton);
    console.error(`Failed to load ${courseCode}:`, error);
}

function focusModal() {
    setTimeout(() => {
        const focusable = getModalFocusableElements();
        if (focusable.length > 0) focusable[0].focus();
    }, 50);
}

function updateModalCourseTitle(courseCode, title) {
    const heading = document.getElementById('modalTitle');
    const code = document.getElementById('modalCourseCode');
    const name = document.getElementById('modalCourseName');
    if (!heading || !code || !name) return;
    code.textContent = courseCode;
    name.textContent = title || '';
    name.hidden = !title;
    heading.setAttribute('aria-label', title ? `${courseCode}: ${title}` : courseCode);
}

function openCourseModal(courseCode, summaryCourse) {
    const overlay = document.getElementById('modalOverlay');
    if (!overlay?.classList.contains('active')) {
        modalOpener = document.activeElement;
    }

    selectedCourse = courseCode;
    selectedSection = null;
    resetHistoricalComparisonState();
    initializeHistoricalComparisonControl(summaryCourse);
    const title = getCourseTitle(summaryCourse);
    updateModalCourseTitle(courseCode, title);
    updateModalBookmark(courseCode);
    overlay.classList.add('active');
    document.documentElement.classList.add('modal-open');
    document.body.classList.add('modal-open');

    // Make background inert while the existing modal is open.
    document.getElementById('main-content').setAttribute('inert', '');
    document.querySelector('header').setAttribute('inert', '');
    document.querySelector('.controls-panel').setAttribute('inert', '');

    document.querySelectorAll('.chart-mode-btn').forEach(b => {
        const isActive = b.dataset.mode === chartMode;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-pressed', isActive);
    });

    const shareBtn = document.getElementById('modalShareLink');
    if (shareBtn) shareBtn.style.display = '';
    history.replaceState(null, '', `#${courseCode.replace(/\s+/g, '-')}`);
    showModalDetailLoading();
    focusModal();
}

function renderCourseDetails(courseCode, course, requestVersion) {
    if (requestVersion !== courseRequestVersion || selectedCourse !== courseCode) return;

    document.getElementById('modalDetailState')?.remove();
    setModalDetailVisibility(true);
    const title = getCourseTitle(course);
    updateModalCourseTitle(courseCode, title);
    updateModalBookmark(courseCode);

    const sectionList = document.getElementById('sectionList');
    const sections = Object.entries(getCourseSections(course)).sort((a, b) => {
        const typePriority = { L: 0, S: 1, R: 1, D: 1, B: 2, Lb: 2 };
        const pa = typePriority[a[1].t] ?? 3;
        const pb = typePriority[b[1].t] ?? 3;
        if (pa !== pb) return pa - pb;
        return a[0].localeCompare(b[0], undefined, { numeric: true });
    });

    const sectionsByType = {};
    for (const [sectionCode, section] of sections) {
        const type = section.t || 'Other';
        if (!sectionsByType[type]) sectionsByType[type] = [];
        sectionsByType[type].push({ code: sectionCode, ...section });
    }

    const sectionTypeSelector = document.getElementById('sectionTypeSelector');
    sectionTypeSelector.textContent = '';
    sectionList.textContent = '';

    for (const [type, typeSections] of Object.entries(sectionsByType)) {
        const typeGroup = document.createElement('div');
        typeGroup.className = 'section-type-group';

        const typeLabel = document.createElement('div');
        typeLabel.className = 'section-type-label';
        typeLabel.textContent = getSectionTypeName(type);
        typeGroup.appendChild(typeLabel);

        const groupList = document.createElement('div');
        groupList.className = 'section-list';
        groupList.style.marginBottom = '0';

        for (const section of typeSections) {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = `section-item ${getStatusClass(section.cf)}`;
            item.id = `section-${section.code}`;
            item.setAttribute('aria-pressed', 'false');
            item.setAttribute('aria-label', `${section.code}${section.in ? `, ${section.in}` : ''}, ${Math.round(section.cf * 100)}% full`);
            const sectionId = document.createElement('div');
            sectionId.className = 'section-id';
            sectionId.textContent = section.code;
            item.appendChild(sectionId);

            if (section.in) {
                const instructor = document.createElement('div');
                instructor.className = 'section-instructor';
                instructor.textContent = section.in;
                item.appendChild(instructor);
            }

            const stats = document.createElement('div');
            stats.className = 'section-stats';
            const fillEl = document.createElement('span');
            fillEl.className = 'section-fill';
            fillEl.textContent = `${Math.round(section.cf * 100)}%`;
            stats.appendChild(fillEl);
            const countEl = document.createElement('span');
            countEl.textContent = `(${section.ce}/${section.cc})`;
            stats.appendChild(countEl);
            item.appendChild(stats);
            item.onclick = () => selectSection(section.code);
            groupList.appendChild(item);
        }

        typeGroup.appendChild(groupList);
        sectionTypeSelector.appendChild(typeGroup);
    }

    renderEventHistory(courseCode, course);
    setTimeout(() => {
        if (requestVersion !== courseRequestVersion || selectedCourse !== courseCode) return;
        showAverageFillChart(courseCode, course, requestVersion)
            .then(() => {
                if (requestVersion === courseRequestVersion && selectedCourse === courseCode) {
                    markPerformance('registrar:course-rendered');
                    if (hasHistoricalCandidateCache(courseCode)) {
                        void resolveHistoricalAvailability(courseCode, course, requestVersion);
                    }
                }
            })
            .catch(error => {
                console.error(`Failed to render ${courseCode} chart:`, error);
            });
    }, 50);
}

async function loadAndRenderCourse(courseCode, requestVersion) {
    let course;
    try {
        course = await hydrateCourse(courseCode);
    } catch (error) {
        showModalDetailError(courseCode, error, requestVersion);
        return;
    }
    if (requestVersion !== courseRequestVersion || selectedCourse !== courseCode) return;
    if (!course) {
        showModalDetailError(courseCode, new Error('Missing department data'), requestVersion);
        return;
    }
    markPerformance('registrar:course-detail-ready');
    renderCourseDetails(courseCode, course, requestVersion);
}

async function openCourse(courseCode) {
    refreshCourseMaps();
    const summaryCourse = getSummaryCourse(courseCode);
    if (!summaryCourse) return;

    const requestVersion = ++courseRequestVersion;
    openCourseModal(courseCode, summaryCourse);
    await loadAndRenderCourse(courseCode, requestVersion);
}

async function retryCourse(courseCode) {
    if (selectedCourse !== courseCode || !document.getElementById('modalOverlay')?.classList.contains('active')) {
        return;
    }
    const requestVersion = ++courseRequestVersion;
    showModalDetailLoading();
    await loadAndRenderCourse(courseCode, requestVersion);
}

function setZoomControlsState(isZoomed) {
    const resetBtn = document.getElementById('chartZoomReset');
    if (resetBtn) {
        resetBtn.disabled = !chart || !isZoomed;
    }
}

function updateZoomControls() {
    const isZoomed = chart && typeof chart.isZoomedOrPanned === 'function'
        ? chart.isZoomedOrPanned()
        : false;
    setZoomControlsState(Boolean(isZoomed));
}

function resetChartZoom() {
    if (chart && typeof chart.resetZoom === 'function') {
        chart.resetZoom('none');
    }
    updateZoomControls();
}

/**
 * Render milestone progress bar with equal-spaced dots.
 */
function createMilestoneDot(milestone, index, count, now, renderedPriorities) {
    const startsPriority = milestone.priority
        && !renderedPriorities.has(milestone.priority);
    if (startsPriority) renderedPriorities.add(milestone.priority);

    const dot = document.createElement('div');
    const passed = now >= milestone.time;
    dot.className = `mp-dot${passed ? ' passed' : ''}${startsPriority ? ' priority-start' : ''}`;
    dot.style.left = `${(index / (count - 1)) * 100}%`;
    if (passed) dot.style.background = milestone.color;
    dot.title = milestone.priority
        ? `Priority ${milestone.priority}: ${milestone.label}`
        : milestone.label;

    const label = document.createElement('span');
    label.className = 'mp-dot-label';
    label.textContent = startsPriority
        ? `P${milestone.priority} · ${milestone.label}`
        : milestone.label;
    dot.appendChild(label);
    return dot;
}

function renderMilestoneProgress() {
    const container = document.getElementById('milestoneProgress');
    if (!container) return;
    const milestones = getMilestones();
    const details = container.closest('.milestone-details');
    const summary = document.getElementById('milestoneSummaryValue');
    if (!milestones || milestones.length < 2) {
        container.textContent = '';
        if (details) details.hidden = true;
        return;
    }
    if (details) details.hidden = false;

    const now = Date.now();
    const mTimes = milestones.map(m => ({
        time: new Date(m.time).getTime(),
        label: m.label,
        color: m.color,
        priority: m.priority ? String(m.priority) : '',
    })).sort((a, b) => a.time - b.time);
    const count = mTimes.length;
    const nextMilestone = mTimes.find(milestone => now < milestone.time);
    if (summary) {
        const priorityPrefix = nextMilestone?.priority ? `P${nextMilestone.priority} · ` : '';
        summary.textContent = nextMilestone
            ? `${priorityPrefix}Next ${nextMilestone.label}`
            : 'Registration complete';
    }

    // Find how far along we are in terms of milestone segments passed
    let filledSegments = 0;
    for (let i = 0; i < count; i++) {
        if (now >= mTimes[i].time) filledSegments = i + 1;
    }
    // Interpolate within current segment
    let fillPct;
    if (filledSegments >= count) {
        fillPct = 100;
    } else if (filledSegments === 0) {
        fillPct = 0;
    } else {
        const segStart = mTimes[filledSegments - 1].time;
        const segEnd = mTimes[filledSegments].time;
        const segFrac = segEnd === segStart ? 1 : (now - segStart) / (segEnd - segStart);
        fillPct = ((filledSegments - 1 + Math.min(1, Math.max(0, segFrac))) / (count - 1)) * 100;
    }

    container.textContent = '';
    const track = document.createElement('div');
    track.className = 'mp-track';

    const fill = document.createElement('div');
    fill.className = 'mp-fill';
    const gradientStops = mTimes.map((milestone, index) => {
        const position = (index / (count - 1)) * 100;
        return `${milestone.color || '#78909C'} ${position}%`;
    });
    fill.style.background = `linear-gradient(90deg, ${gradientStops.join(', ')})`;
    // Reveal the full-width gradient instead of scaling it. Scaling compresses
    // the color stops during active registration, so dots no longer match the
    // line color at their physical timeline position.
    fill.style.clipPath = `inset(0 ${100 - fillPct}% 0 0)`;
    track.appendChild(fill);

    const renderedPriorities = new Set();
    for (let i = 0; i < count; i++) {
        track.appendChild(createMilestoneDot(
            mTimes[i], i, count, now, renderedPriorities,
        ));
    }

    container.appendChild(track);
}

// Global state for event history
let _currentEvents = [];
let _eventSortOrder = 'newest'; // 'newest' or 'oldest'
let _eventFilterType = 'all'; // 'all' or specific event type
let _eventFilterSection = 'all'; // 'all' or specific section code

const EVENT_ICONS = {
    'course_added': '🟢', 'course_removed': '🔴',
    'section_added': '➕', 'section_removed': '➖',
    'capacity_changed': '📊', 'instructor_changed': '🔄'
};
const EVENT_LABELS = {
    'course_added': 'Added', 'course_removed': 'Removed',
    'section_added': 'Sec +', 'section_removed': 'Sec −',
    'capacity_changed': 'Capacity', 'instructor_changed': 'Instructor'
};

function eventDesc(e) {
    const descs = {
        'course_added': 'Course added',
        'course_removed': 'Course removed',
        'section_added': e => `Section ${e.sc || ''} added`,
        'section_removed': e => `Section ${e.sc || ''} removed`,
        'capacity_changed': e => `${e.sc || ''} capacity: ${e.ov} → ${e.nv}`,
        'instructor_changed': e => `${e.sc || ''} instructor: ${e.ov} → ${e.nv}`,
    };
    const fn = descs[e.et];
    return typeof fn === 'function' ? fn(e) : (fn || e.et);
}

/**
 * Render event history for a course, with filtering and sorting.
 */
function renderEventHistory(courseCode, course = getHydratedCourse(courseCode)) {
    const container = document.getElementById('eventHistoryList');
    const countEl = document.getElementById('eventCount');
    const details = document.getElementById('eventHistory');
    if (!container || !details) return;

    _currentEvents = course?.ev || [];
    _eventFilterType = 'all';
    _eventFilterSection = 'all';

    if (_currentEvents.length === 0) {
        details.style.display = 'none';
        return;
    }
    details.style.display = '';
    if (countEl) countEl.textContent = `(${_currentEvents.length})`;

    // Build filter controls
    const sections = [...new Set(_currentEvents.filter(e => e.sc).map(e => e.sc))].sort();
    const types = [...new Set(_currentEvents.map(e => e.et))];

    const filtersEl = document.getElementById('eventFilters');
    if (filtersEl) {
        filtersEl.textContent = '';
        const efRow = document.createElement('div');
        efRow.className = 'ef-row';

        const sortBtn = document.createElement('button');
        sortBtn.className = 'ef-pill ef-sort';
        sortBtn.dataset.sort = 'newest';
        sortBtn.title = 'Sort order';
        sortBtn.textContent = '↓ Newest';
        efRow.appendChild(sortBtn);

        const typeSelect = document.createElement('select');
        typeSelect.className = 'ef-select';
        typeSelect.id = 'efType';
        typeSelect.setAttribute('aria-label', 'Filter by type');
        const allTypesOpt = document.createElement('option');
        allTypesOpt.value = 'all';
        allTypesOpt.textContent = 'All types';
        typeSelect.appendChild(allTypesOpt);
        for (const t of types) {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = `${EVENT_ICONS[t] || ''} ${EVENT_LABELS[t] || t}`;
            typeSelect.appendChild(opt);
        }
        efRow.appendChild(typeSelect);

        if (sections.length > 1) {
            const sectionSelect = document.createElement('select');
            sectionSelect.className = 'ef-select';
            sectionSelect.id = 'efSection';
            sectionSelect.setAttribute('aria-label', 'Filter by section');
            const allSectionsOpt = document.createElement('option');
            allSectionsOpt.value = 'all';
            allSectionsOpt.textContent = 'All sections';
            sectionSelect.appendChild(allSectionsOpt);
            for (const s of sections) {
                const opt = document.createElement('option');
                opt.value = s;
                opt.textContent = s;
                sectionSelect.appendChild(opt);
            }
            efRow.appendChild(sectionSelect);
        }

        filtersEl.appendChild(efRow);

        // Wire up events
        filtersEl.querySelector('.ef-sort')?.addEventListener('click', (e) => {
            _eventSortOrder = _eventSortOrder === 'newest' ? 'oldest' : 'newest';
            e.target.textContent = _eventSortOrder === 'newest' ? '↓ Newest' : '↑ Oldest';
            _renderFilteredEvents();
        });
        filtersEl.querySelector('#efType')?.addEventListener('change', (e) => {
            _eventFilterType = e.target.value;
            _renderFilteredEvents();
        });
        filtersEl.querySelector('#efSection')?.addEventListener('change', (e) => {
            _eventFilterSection = e.target.value;
            _renderFilteredEvents();
        });
    }

    _renderFilteredEvents();
}

function _renderFilteredEvents() {
    const container = document.getElementById('eventHistoryList');
    const countEl = document.getElementById('eventCount');
    if (!container) return;

    let filtered = _currentEvents;
    if (_eventFilterType !== 'all') filtered = filtered.filter(e => e.et === _eventFilterType);
    if (_eventFilterSection !== 'all') filtered = filtered.filter(e => e.sc === _eventFilterSection);

    // Sort
    filtered = [...filtered].sort((a, b) => {
        const ta = a.st ? new Date(a.st).getTime() : 0;
        const tb = b.st ? new Date(b.st).getTime() : 0;
        return _eventSortOrder === 'newest' ? tb - ta : ta - tb;
    });

    if (countEl) countEl.textContent = `(${filtered.length}/${_currentEvents.length})`;

    container.textContent = '';
    if (filtered.length === 0) {
        const emptyMsg = document.createElement('div');
        emptyMsg.className = 'event-item';
        emptyMsg.style.color = 'hsl(var(--muted-foreground))';
        emptyMsg.style.justifyContent = 'center';
        emptyMsg.textContent = 'No matching events';
        container.appendChild(emptyMsg);
    } else {
        for (const e of filtered) {
            const row = document.createElement('div');
            row.className = 'event-item';

            const icon = document.createElement('span');
            icon.className = 'event-icon';
            icon.textContent = EVENT_ICONS[e.et] || '📝';
            row.appendChild(icon);

            const desc = document.createElement('span');
            desc.className = 'event-desc';
            desc.textContent = eventDesc(e);
            row.appendChild(desc);

            const ts = document.createElement('span');
            ts.className = 'event-ts';
            ts.textContent = e.st ? new Date(e.st).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
            row.appendChild(ts);

            container.appendChild(row);
        }
    }
}

/**
 * Show average fill chart for a course.
 */
async function showAverageFillChart(
    courseCode,
    course = getHydratedCourse(courseCode),
    requestVersion = null,
) {
    if (requestVersion !== null && requestVersion !== courseRequestVersion) return;
    if (!course) return;
    const snapshots = getCourseSnapshots(course);
    const chartPoints = buildAverageChartPoints(course, snapshots);
    const chartDomain = buildCourseChartDomain(course, snapshots);
    currentEnrollmentData = chartPoints;

    updateHistoricalLegend();
    await renderChart(courseCode, chartPoints, chartDomain, { requestVersion });
}

function updateChartAccessibilitySummary({
    canvas,
    chartLabel,
    visibleChartPoints,
    fillData,
    historicalComparison,
    hasHistoricalDataset,
}) {
    canvas.setAttribute('aria-label', `${chartLabel} enrollment history`);
    const summary = document.getElementById('chartSummary');
    if (!summary) return;

    const latestPoint = visibleChartPoints.at(-1);
    const latestFill = Number.isFinite(latestPoint?.fill)
        ? Math.round(latestPoint.fill)
        : Math.round(fillData.at(-1) || 0);
    const countSummary = Number.isFinite(latestPoint?.enrollment)
        && Number.isFinite(latestPoint?.capacity)
        ? `, ${latestPoint.enrollment} of ${latestPoint.capacity} seats filled`
        : '';
    const professorSummary = historicalComparison?.mode === 'professor'
        ? ` for ${historicalComparison.professorDisplayName}`
        : '';
    const comparisonSummary = hasHistoricalDataset
        ? ` Compared with ${historicalComparison.semester}${professorSummary}.`
        : '';
    summary.textContent = `${chartLabel}. ${visibleChartPoints.length} enrollment observations. Latest: ${latestFill}% full${countSummary}.${comparisonSummary}`;
}

/**
 * Select a section and show its enrollment chart.
 */
async function selectSection(sectionCode) {
    const course = getHydratedCourse(selectedCourse);
    if (!course) return;
    const requestVersion = courseRequestVersion;

    // Toggle selection if clicking same section
    if (selectedSection === sectionCode) {
        const selectedItem = document.getElementById(`section-${sectionCode}`);
        selectedItem?.classList.remove('selected');
        selectedItem?.setAttribute('aria-pressed', 'false');
        selectedSection = null;
        resetHistoricalComparisonState();
        initializeHistoricalComparisonControl(course);
        currentEnrollmentData = [];
        await showAverageFillChart(selectedCourse, course, requestVersion);
        void resolveHistoricalAvailability(selectedCourse, course, requestVersion);
        return;
    }

    // Update selection styling
    if (selectedSection) {
        const previousItem = document.getElementById(`section-${selectedSection}`);
        previousItem?.classList.remove('selected');
        previousItem?.setAttribute('aria-pressed', 'false');
    }
    selectedSection = sectionCode;
    resetHistoricalComparisonState();
    const selectedItem = document.getElementById(`section-${sectionCode}`);
    selectedItem?.classList.add('selected');
    selectedItem?.setAttribute('aria-pressed', 'true');

    const section = getCourseSections(course)[sectionCode];
    if (!section) return;
    initializeHistoricalComparisonControl(course);

    const snapshots = getCourseSnapshots(course);
    const chartPoints = buildSectionChartPoints(section, snapshots);
    const chartDomain = buildCourseChartDomain(course, snapshots);
    currentEnrollmentData = chartPoints;

    updateHistoricalLegend();

    await renderChart(
        `${sectionCode} Enrollment %`,
        chartPoints,
        chartDomain,
        { requestVersion },
    );
    void resolveHistoricalAvailability(selectedCourse, course, requestVersion);
}

/**
 * Render enrollment chart with milestones and phased/timeline/snapshots mode.
 */
async function renderChart(
    chartLabel,
    chartPoints,
    chartDomain,
    { requestVersion = null, historicalComparison = null } = {},
) {
    await loadChartJs();
    if (requestVersion !== null && requestVersion !== courseRequestVersion) return;
    // Cache args for mode toggle re-render
    lastRenderArgs = {
        chartLabel,
        chartPoints,
        chartDomain,
        requestVersion,
        historicalComparison,
    };

    const milestones = getMilestones();
    const visibleChartPoints = limitPointsBeforeFirstMilestone(chartPoints, milestones);
    const visibleChartDomain = limitPointsBeforeFirstMilestone(chartDomain, milestones);
    const fillData = visibleChartPoints.map(point => point.enrollmentLevel ?? point.fill);
    const capacityData = visibleChartPoints.map(point => point.capacityLevel);
    const domainTimestamps = visibleChartDomain.map(point => point.timestamp);
    const currentMapper = getChartMapper(
        chartMode,
        visibleChartPoints,
        visibleChartDomain,
        milestones,
    );
    const { xValues, domainXValues, mapTime, unmapX } = currentMapper;
    const currentComparisonDomain = domainXValues.length > 0 ? domainXValues : xValues;
    const historicalChartState = buildHistoricalChartState({
        historicalComparison,
        chartMode,
        currentComparisonDomain,
        currentMapper,
        milestones,
    });
    const { historicalDataPoints } = historicalChartState;
    const hasHistoricalDataset = historicalDataPoints.length > 0;
    const enrollmentScaleMax = getEnrollmentScaleMax([
        ...fillData,
        ...capacityData,
        ...historicalDataPoints.map(point => point.y),
    ]);
    const { annotations, annotationXValues } = buildMilestoneRenderState({
        chartMode,
        milestones,
        domainTimestamps,
        mapTime,
        xValues,
        fillData,
        historicalDataPoints,
    });
    const historicalXValues = historicalDataPoints.map(point => point.x);
    const xScaleDomain = currentComparisonDomain.length > 1
        ? currentComparisonDomain
        : historicalXValues.length > 1
            ? historicalXValues
            : currentComparisonDomain;
    const xBounds = getXScaleBounds([
        ...xScaleDomain,
        ...annotationXValues,
    ]);
    const displayHistoricalDataPoints = historicalComparison?.mode === 'course'
        ? extendSteppedSeriesToDomainEnd(historicalDataPoints, xBounds.max)
        : historicalDataPoints;

    // Show chart canvas
    document.getElementById('chartPlaceholder').style.display = 'none';
    const canvas = document.getElementById('enrollment-chart');
    canvas.classList.remove('chart-hidden');
    updateChartAccessibilitySummary({
        canvas,
        chartLabel,
        visibleChartPoints,
        fillData,
        historicalComparison,
        hasHistoricalDataset,
    });
    canvas.offsetHeight;

    if (chart) { chart.destroy(); chart = null; }
    setZoomControlsState(false);

    // Build dataset with {x, y} pairs
    const dataPoints = fillData.map((y, i) => ({ x: xValues[i], y }));
    const observedCapacityPoints = capacityData.flatMap((y, i) => (
        Number.isFinite(y) ? [{ x: xValues[i], y, sourceIndex: i }] : []
    ));
    const capacityDataPoints = observedCapacityPoints.length > 0 ? [
        {
            x: xBounds.min,
            y: observedCapacityPoints[0].y,
            sourceIndex: observedCapacityPoints[0].sourceIndex,
            synthetic: true,
        },
        ...observedCapacityPoints,
        {
            x: xBounds.max,
            y: observedCapacityPoints.at(-1).y,
            sourceIndex: observedCapacityPoints.at(-1).sourceIndex,
            synthetic: true,
        },
    ] : [];

    const currentDataset = {
        id: 'enrollment',
        label: chartLabel,
        data: dataPoints,
        borderColor: '#ffd700',
        backgroundColor: 'rgba(255, 215, 0, 0.1)',
        fill: true,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderWidth: 3,
        pointRadius: 0,
        pointHoverRadius: 0,
        order: 0,
    };
    const capacityDataset = capacityDataPoints.length > 0 ? {
        id: 'capacity',
        label: 'Capacity',
        data: capacityDataPoints,
        borderColor: 'rgba(78, 205, 196, 0.84)',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderWidth: 6,
        pointRadius: 0,
        pointHoverRadius: 0,
        // The wide supporting stroke remains visible as a teal halo when the
        // yellow enrollment line is exactly equal to capacity.
        order: 2,
    } : null;
    const historicalDataset = hasHistoricalDataset ? {
        id: 'historical',
        label: historicalComparison.mode === 'professor'
            ? `${historicalComparison.semester} · ${historicalComparison.professorDisplayName}`
            : `${historicalComparison.semester} course aggregate`,
        data: displayHistoricalDataPoints,
        borderColor: 'rgba(247, 249, 252, 0.9)',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderDash: [7, 4],
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 0,
        // Draw above capacity and milestones, but behind the current line.
        order: 1,
    } : null;
    canvas.dataset.historicalDatasets = historicalDataset ? '1' : '0';
    const compactTooltip = window.matchMedia('(max-width: 768px)').matches;
    canvas.dataset.tooltipDensity = compactTooltip ? 'compact' : 'regular';
    const tooltipMetrics = {
        id: 'tooltipMetrics',
        afterDraw(chartInstance) {
            const width = chartInstance.tooltip?.opacity > 0
                ? Math.round(chartInstance.tooltip.width)
                : 0;
            canvas.dataset.tooltipWidth = String(width);
        },
    };

    chart = new Chart(canvas, {
        type: 'line',
        plugins: [tooltipMetrics],
        data: {
            datasets: [historicalDataset, capacityDataset, currentDataset].filter(Boolean),
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            layout: { padding: { left: 0, right: 0, top: 4, bottom: 4 } },
            plugins: {
                annotation: { annotations },
                hoverIndicator: { unmapX },
                legend: { display: false },
                zoom: {
                    limits: {
                        x: { min: xBounds.min, max: xBounds.max, minRange: xBounds.minRange },
                        y: { min: 0, max: enrollmentScaleMax }
                    },
                    pan: {
                        enabled: true,
                        mode: 'x',
                        threshold: 8,
                        onPanComplete: updateZoomControls
                    },
                    zoom: {
                        mode: 'x',
                        wheel: {
                            enabled: true,
                            speed: 0.08
                        },
                        pinch: {
                            enabled: true
                        },
                        drag: {
                            enabled: true,
                            modifierKey: 'shift',
                            threshold: 8,
                            backgroundColor: 'rgba(255, 215, 0, 0.16)',
                            borderColor: '#ffd700',
                            borderWidth: 1
                        },
                        onZoomComplete: updateZoomControls
                    }
                },
                tooltip: {
                    position: 'glide',
                    backgroundColor: '#1a1a2e', titleColor: '#ffd700', bodyColor: '#eaeaea',
                    borderColor: '#3a3a5e', borderWidth: 1,
                    ...(compactTooltip ? {
                        padding: 7,
                        cornerRadius: 6,
                        titleFont: { size: 11, weight: 'bold' },
                        titleMarginBottom: 4,
                        bodyFont: { size: 11 },
                        bodySpacing: 2,
                        boxWidth: 8,
                        boxHeight: 8,
                        boxPadding: 3,
                    } : {}),
                    callbacks: {
                        title: (items) => {
                            if (!items.length) return '';
                            const chartInstance = items[0].chart;
                            const pixelX = chartInstance.$hoverPixelX;
                            const mappedX = Number.isFinite(pixelX)
                                ? chartInstance.scales.x.getValueForPixel(pixelX)
                                : items[0].parsed.x;
                            const timestamp = unmapX(mappedX);
                            if (!Number.isFinite(timestamp)) return '';
                            return new Date(timestamp).toLocaleString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                hour: 'numeric',
                                minute: '2-digit',
                            });
                        },
                        label: (ctx) => {
                            if (ctx.dataset.id === 'historical') {
                                const sourceIndex = displayHistoricalDataPoints[ctx.dataIndex]?.sourceIndex;
                                const historicalPoint = historicalComparison.chartPoints[sourceIndex];
                                if (!historicalPoint) return `${ctx.parsed.y}%`;
                                const openingLevel = Math.round(ctx.parsed.y);
                                const fillLevel = historicalPoint.fill;
                                if (Math.abs(openingLevel - fillLevel) > 1) {
                                    if (compactTooltip) {
                                        return [
                                            `${historicalComparison.semester}: ${openingLevel}% of opening`,
                                            `Same enrollment: ${fillLevel}% of capacity`,
                                        ];
                                    }
                                    return [
                                        `${historicalComparison.semester} line: ${openingLevel}% of opening capacity`,
                                        `Same enrollment: ${fillLevel}% of capacity on this date`,
                                    ];
                                }
                                return `${historicalComparison.semester}: ${fillLevel}% full`;
                            }
                            const idx = ctx.dataset.id === 'capacity'
                                ? capacityDataPoints[ctx.dataIndex]?.sourceIndex
                                : ctx.dataIndex;
                            const enrollInfo = visibleChartPoints[idx];
                            if (ctx.dataset.id === 'capacity') {
                                const amount = Number.isFinite(enrollInfo?.capacity)
                                    ? ` ${enrollInfo.capacity}`
                                    : '';
                                const openingCopy = compactTooltip ? 'opening' : 'of opening';
                                return `Capacity${amount} · ${Math.round(ctx.parsed.y)}% ${openingCopy}`;
                            }
                            let lbl = 'Enrollment';
                            if (enrollInfo && enrollInfo.enrollment !== null) {
                                lbl += ` ${enrollInfo.enrollment}/${enrollInfo.capacity}`;
                            }
                            const openingLevel = Math.round(ctx.parsed.y);
                            const fillLevel = enrollInfo?.fill ?? openingLevel;
                            const details = Math.abs(openingLevel - fillLevel) > 1
                                ? `${openingLevel}% of opening · ${fillLevel}% full`
                                : `${fillLevel}% full`;
                            return `${lbl} · ${details}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    display: false,
                    min: xBounds.min,
                    max: xBounds.max
                },
                y: {
                    min: 0, max: enrollmentScaleMax,
                    ticks: {
                        display: true,
                        stepSize: 25,
                        color: 'rgba(220, 224, 232, 0.68)',
                        font: { size: 9, weight: '600' },
                        padding: 4,
                        callback: value => `${value}%`,
                    },
                    grid: {
                        // Keep horizontal guides beneath the yellow course series.
                        z: -1,
                        color: 'rgba(220, 224, 232, 0.18)',
                        lineWidth: 1,
                        drawTicks: false,
                    },
                    border: { display: false }
                }
            },
            interaction: { intersect: false, mode: 'glideStepped', axis: 'x' }
        }
    });
    updateHistoricalLegend();
    updateZoomControls();
}

/**
 * Close the course detail modal.
 */
function closeModal() {
    courseRequestVersion += 1;
    document.getElementById('modalOverlay').classList.remove('active');
    document.documentElement.classList.remove('modal-open');
    document.body.classList.remove('modal-open');

    // Remove inert from background
    document.getElementById('main-content')?.removeAttribute('inert');
    document.querySelector('header')?.removeAttribute('inert');
    document.querySelector('.controls-panel')?.removeAttribute('inert');

    // Hide share button
    const shareBtn = document.getElementById('modalShareLink');
    if (shareBtn) shareBtn.style.display = 'none';

    // Restore focus to the opener
    if (modalOpener && typeof modalOpener.focus === 'function') {
        modalOpener.focus();
        modalOpener = null;
    }

    selectedCourse = null;
    selectedSection = null;
    currentEnrollmentData = [];
    lastRenderArgs = null;
    resetHistoricalComparisonState();
    resetCourseDetailView();
    // Clear URL hash
    history.replaceState(null, '', window.location.pathname);
}

/**
 * Clear chart active elements (fix for persistent hover on touch).
 */
function clearChartActiveElements() {
    if (chart) {
        chart.setActiveElements([]);
        if (chart.tooltip) {
            chart.tooltip.setActiveElements([], { x: 0, y: 0 });
            chart.tooltip.opacity = 0;
        }
        chart.update('none');
    }
}

// Event listeners
document.getElementById('modalOverlay').addEventListener('click', (e) => {
    if (e.target.id === 'modalOverlay') closeModal();
});

// Track the element that opened the modal for focus restoration
let modalOpener = null;

/**
 * Get all focusable elements within the modal.
 */
function getModalFocusableElements() {
    const modal = document.querySelector('.modal');
    if (!modal) return [];
    return [...modal.querySelectorAll(
        'button:not([disabled]):not([tabindex="-1"]), ' +
        '[href], input:not([disabled]), select:not([disabled]), ' +
        'textarea:not([disabled]), [tabindex]:not([tabindex="-1"]):not([disabled])'
    )];
}

/**
 * Trap focus within the modal on Tab/Shift+Tab.
 */
function trapFocus(e) {
    if (e.key !== 'Tab') return;
    const focusable = getModalFocusableElements();
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey) {
        if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
        }
    } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
    }
}

// Add focus trap keydown listener on the modal overlay
document.getElementById('modalOverlay').addEventListener('keydown', trapFocus);

// Close button event listener
document.getElementById('modalCloseBtn')?.addEventListener('click', closeModal);

// Share link button
document.getElementById('modalShareLink')?.addEventListener('click', async () => {
    if (!selectedCourse) return;
    const semSlug = semesterToSlug(IS_COMBINED ? activeSemester : getData().sem || '');
    const courseSlug = courseToSlug(selectedCourse);
    const shareUrl = `${window.location.origin}/courses/${semSlug}/${courseSlug}.html`;

    try {
        await navigator.clipboard.writeText(shareUrl);
        showToast('Share link copied');
    } catch {
        // Fallback: select a temporary input
        const input = document.createElement('input');
        input.value = shareUrl;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        input.remove();
        showToast('Share link copied');
    }
});

// Use window capture phase so this fires before any child element (e.g. the
// search input) can call stopPropagation() and swallow the Escape event.
window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('modalOverlay').classList.contains('active')) {
        closeModal();
    }
}, true);

document.getElementById('chartContainer').addEventListener('touchend', () => {
    setTimeout(clearChartActiveElements, 100);
});

document.getElementById('chartContainer').addEventListener('wheel', (e) => {
    if (chart) {
        e.preventDefault();
        e.stopPropagation();
    }
}, { passive: false });

document.querySelector('.modal-body').addEventListener('click', (e) => {
    if (!e.target.closest('#chartContainer')) {
        clearChartActiveElements();
    }
});


// ============================================
// Search Functionality (Phase 4)
// ============================================

const searchInput = document.getElementById('courseSearch');
const clearSearchButton = document.getElementById('clearSearch');
const departmentToggle = document.getElementById('departmentToggle');

departmentToggle?.addEventListener('click', () => {
    const jumpNav = document.getElementById('jumpToNav');
    if (!jumpNav) return;
    const expanded = departmentToggle.getAttribute('aria-expanded') !== 'true';
    departmentToggle.setAttribute('aria-expanded', String(expanded));
    jumpNav.hidden = !expanded;
});

// Keyboard shortcut: "/" to focus search
document.addEventListener('keydown', (e) => {
    const modalActive = document.getElementById('modalOverlay').classList.contains('active');
    if (e.key === '/' && document.activeElement !== searchInput && !modalActive) {
        e.preventDefault();
        searchInput?.focus();
    }
});

// Arrow key navigation in course grid
document.getElementById('courseGrid')?.addEventListener('keydown', (e) => {
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;

    const cells = [...document.querySelectorAll('.course-cell:not(.hidden)')];
    const idx = cells.indexOf(document.activeElement);
    if (idx === -1) return;

    e.preventDefault();
    const gridEl = document.getElementById('courseGrid');
    const cols = Math.floor(gridEl.offsetWidth / 128);

    let next = idx;
    switch (e.key) {
        case 'ArrowRight': next = Math.min(idx + 1, cells.length - 1); break;
        case 'ArrowLeft': next = Math.max(idx - 1, 0); break;
        case 'ArrowDown': next = Math.min(idx + cols, cells.length - 1); break;
        case 'ArrowUp': next = Math.max(idx - cols, 0); break;
    }
    cells[next]?.focus();
});

// ============================================
// Filter by Status (UX Enhancement)
// ============================================

let currentFilter = 'all';

document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => {
            b.classList.remove('active');
            b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        currentFilter = btn.dataset.filter;
        applyFilters();
    });
});

/**
 * Show empty state message in the course grid when no courses match.
 */
function showEmptyState(query) {
    hideEmptyState();
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    const div = document.createElement('div');
    div.className = 'empty-state';
    if (currentFilter === 'starred' && !query) {
        div.textContent = 'No bookmarked courses yet. Bookmark a course from its details to see it here.';
    } else if (currentFilter === 'starred') {
        div.textContent = `No bookmarked courses match '${query}'.`;
    } else if (query) {
        div.textContent = `No courses match '${query}'. Try a different search term.`;
    } else {
        const labels = { full: 'full', near: 'near-full', open: 'open' };
        div.textContent = `No ${labels[currentFilter] || currentFilter} courses are available.`;
    }
    grid.appendChild(div);
}

/**
 * Remove the empty state message from the course grid.
 */
function hideEmptyState() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    const existing = grid.querySelector('.empty-state');
    if (existing) existing.remove();
}

/**
 * Show error state in the course grid with a retry button.
 */
function showErrorState() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    // Remove any existing error or empty states
    grid.querySelectorAll('.empty-state, .error-state, .timeout-state').forEach(el => el.remove());
    const div = document.createElement('div');
    div.className = 'error-state';
    div.textContent = 'Failed to load enrollment data. ';
    const retryBtn = document.createElement('button');
    retryBtn.id = 'retryFetch';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', () => {
        div.remove();
        initApp();
    });
    div.appendChild(retryBtn);
    grid.appendChild(div);
}

/**
 * Show a timeout warning in the course grid.
 */
function showTimeoutWarning() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    // Don't add duplicate warnings
    if (grid.querySelector('.timeout-state')) return;
    const div = document.createElement('div');
    div.className = 'timeout-state';
    div.textContent = 'Still loading... This is taking longer than expected.';
    grid.appendChild(div);
}

/**
 * Remove the timeout warning from the course grid.
 */
function hideTimeoutWarning() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    const existing = grid.querySelector('.timeout-state');
    if (existing) existing.remove();
}

function applyFilters() {
    const searchQuery = searchInput?.value.toLowerCase().trim() || '';
    const cells = document.querySelectorAll('.course-cell');

    let visibleCount = 0;
    cells.forEach(cell => {
        const code = cell.getAttribute('data-course').toLowerCase();
        const status = cell.dataset.status;
        const isStarred = cell.classList.contains('starred');

        const matchesSearch = !searchQuery || code.includes(searchQuery);
        const matchesFilter = currentFilter === 'all' ||
            (currentFilter === 'starred' && isStarred) ||
            status === currentFilter;

        const isVisible = matchesSearch && matchesFilter;
        cell.classList.toggle('hidden', !isVisible);
        if (isVisible) visibleCount++;
    });

    const announcement = document.getElementById('searchAnnouncement');
    if (announcement) {
        announcement.textContent = `Showing ${visibleCount} courses`;
    }

    // Update department headers
    document.querySelectorAll('.dept-header').forEach(header => {
        const nextCells = [];
        let sibling = header.nextElementSibling;
        while (sibling && !sibling.classList.contains('dept-header')) {
            if (sibling.classList.contains('course-cell')) {
                nextCells.push(sibling);
            }
            sibling = sibling.nextElementSibling;
        }
        header.style.display = nextCells.some(c => !c.classList.contains('hidden')) ? '' : 'none';
    });

    const sortBy = document.getElementById('sortSelect')?.value;
    const visibleDepartments = [...new Set([...cells]
        .filter(cell => !cell.classList.contains('hidden'))
        .map(cell => cell.dataset.course.split(' ')[0]))]
        .sort();
    renderJumpToNavigation(sortBy === 'department' ? visibleDepartments : []);

    // Show empty state if no courses are visible (and we have cells to filter)
    if (cells.length > 0 && visibleCount === 0) {
        showEmptyState(searchInput?.value || '');
    } else {
        hideEmptyState();
    }
}

function updateSearchControls() {
    if (clearSearchButton) clearSearchButton.hidden = !searchInput?.value;
}

searchInput?.addEventListener('input', () => {
    updateSearchControls();
    applyFilters();
});

searchInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !searchInput.value) return;
    event.stopPropagation();
    searchInput.value = '';
    updateSearchControls();
    applyFilters();
});

clearSearchButton?.addEventListener('click', () => {
    if (!searchInput) return;
    searchInput.value = '';
    updateSearchControls();
    applyFilters();
    searchInput.focus();
});

// ============================================
// Sort Functionality (UX Enhancement)
// ============================================

document.getElementById('sortSelect')?.addEventListener('change', (e) => {
    const sortBy = e.target.value;
    const grid = document.getElementById('courseGrid');
    const cells = [...grid.querySelectorAll('.course-cell')];
    const headers = [...grid.querySelectorAll('.dept-header')];

    // Remove all items
    cells.forEach(c => c.remove());
    headers.forEach(h => h.remove());

    // Sort cells
    cells.sort((a, b) => {
        const codeA = a.dataset.course;
        const codeB = b.dataset.course;
        const fillA = parseFloat(a.dataset.fill);
        const fillB = parseFloat(b.dataset.fill);
        const deptA = codeA.split(' ')[0];
        const deptB = codeB.split(' ')[0];

        switch (sortBy) {
            case 'code': return codeA.localeCompare(codeB);
            case 'fill-desc': return fillB - fillA;
            case 'fill-asc': return fillA - fillB;
            default: return deptA.localeCompare(deptB) || codeA.localeCompare(codeB);
        }
    });

    // Re-add with department headers if sorting by department
    if (sortBy === 'department') {
        let currentDept = '';
        cells.forEach(cell => {
            const dept = cell.dataset.course.split(' ')[0];
            if (dept !== currentDept) {
                currentDept = dept;
                const header = document.createElement('h2');
                header.className = 'dept-header';
                header.id = `dept-${dept}`;
                const sortDeptSpan = document.createElement('span');
                sortDeptSpan.textContent = dept;
                header.appendChild(sortDeptSpan);
                header.appendChild(document.createTextNode(' '));
                const sortTopLink = document.createElement('a');
                sortTopLink.href = '#';
                sortTopLink.className = 'back-to-top';
                sortTopLink.textContent = '↑ Top';
                sortTopLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                });
                header.appendChild(sortTopLink);
                grid.appendChild(header);
            }
            grid.appendChild(cell);
        });
    } else {
        // No headers for other sorts
        cells.forEach(c => grid.appendChild(c));
        document.getElementById('jumpToNav').textContent = '';
    }
    applyFilters();
});

// ============================================
// Bookmarks/Favorites (UX Enhancement)
// ============================================

function saveBookmarks() {
    localStorage.setItem('courseBookmarks', JSON.stringify([...bookmarks]));
}

// ============================================
// Toast Notifications (Visual Polish)
// ============================================

function showToast(message, duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ============================================
// Modal Bookmark Handler
// ============================================

const modalBookmarkBtn = document.getElementById('modalBookmark');

function updateModalBookmark(code) {
    if (!modalBookmarkBtn) return;
    const isStarred = bookmarks.has(code);
    modalBookmarkBtn.classList.toggle('starred', isStarred);
    modalBookmarkBtn.setAttribute('aria-pressed', String(isStarred));
    modalBookmarkBtn.setAttribute(
        'aria-label',
        isStarred ? 'Remove bookmark' : 'Bookmark course',
    );
    modalBookmarkBtn.onclick = () => {
        if (bookmarks.has(code)) {
            bookmarks.delete(code);
        } else {
            bookmarks.add(code);
        }
        saveBookmarks();
        updateModalBookmark(code);
        // Update course cell
        const cell = document.querySelector(`.course-cell[data-course="${code}"]`);
        if (cell) {
            cell.classList.toggle('starred', bookmarks.has(code));
        }
        showToast(bookmarks.has(code) ? 'Course bookmarked' : 'Bookmark removed');
    };
}

// ============================================
// Chart Mode Toggle
// ============================================

document.querySelectorAll('.chart-mode-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        chartMode = btn.dataset.mode;
        localStorage.setItem('chartMode', chartMode);
        setZoomControlsState(false);
        document.querySelectorAll('.chart-mode-btn').forEach(b => {
            const isActive = b.dataset.mode === chartMode;
            b.classList.toggle('active', isActive);
            b.setAttribute('aria-pressed', isActive);
        });
        if (lastRenderArgs) {
            const a = lastRenderArgs;
            await renderChart(
                a.chartLabel,
                a.chartPoints,
                a.chartDomain,
                {
                    requestVersion: a.requestVersion,
                    historicalComparison: a.historicalComparison,
                },
            );
        }
    });
});

document.getElementById('historicalComparisonToggle')?.addEventListener('click', async () => {
    if (!selectedCourse || historicalComparisonStatus === 'loading') return;
    const course = getHydratedCourse(selectedCourse);
    if (!course) return;
    if (historicalComparisonStatus === 'idle') {
        await resolveHistoricalAvailability(selectedCourse, course, courseRequestVersion);
        if (historicalComparisonStatus === 'available') {
            await enableHistoricalComparison();
        }
        return;
    }
    if (historicalComparisonStatus === 'failed' && !historicalComparisonDescriptor?.candidate) {
        void resolveHistoricalAvailability(selectedCourse, course, courseRequestVersion);
        return;
    }
    void enableHistoricalComparison();
});

document.getElementById('chartZoomReset')?.addEventListener('click', resetChartZoom);

// ============================================
// Deep Linking via URL Hash
// ============================================

function handleHashNavigation() {
    const hash = window.location.hash.slice(1); // Remove '#'
    if (!hash) return;
    const courseCode = hash.replace(/-/g, ' '); // 'CSCI-101' -> 'CSCI 101'
    refreshCourseMaps();
    if (summaryCourses.has(courseCode)) {
        openCourse(courseCode);
    }
}

window.addEventListener('hashchange', handleHashNavigation);

// ============================================
// Initialization
// ============================================

function installPayload(payload) {
    DATA = payload.data;
    MILESTONES = payload.milestones || [];
    mappedData = null;
    summaryCourses.clear();
    hydratedCourses.clear();
    departmentPayloads.clear();
    refreshCourseMaps();
}

async function initApp() {
    // Generated semester pages always start from the v3 manifest pointer.
    const manifestUrl = getManifestUrl(document);
    let summaryReadyMarked = false;
    if (manifestUrl && !DATA) {
        // Show loading timeout warning after 12 seconds
        const timeoutId = setTimeout(showTimeoutWarning, 12000);

        try {
            const absoluteUrl = new URL(manifestUrl, window.location.href);
            const loaded = await loadSemesterManifest(absoluteUrl.href, {
                signal: dataLoadController.signal,
            });
            installPayload(loaded.payload);
            staticManifest = loaded.manifest;
            staticManifestUrl = loaded.manifestUrl;
            staticManifestStale = loaded.stale;
            markPerformance('registrar:summary-ready');
            summaryReadyMarked = true;

            clearTimeout(timeoutId);
            hideTimeoutWarning();
            const loader = document.getElementById('loadingIndicator');
            if (loader) loader.remove();
        } catch (error) {
            console.error("Failed to load enrollment data:", error);
            clearTimeout(timeoutId);
            hideTimeoutWarning();
            const loader = document.getElementById('loadingIndicator');
            if (loader) loader.remove();
            showErrorState();
            return;
        }
    }

    if (!summaryReadyMarked) markPerformance('registrar:summary-ready');

    if (IS_COMBINED) {
        renderSemesterToggle();
    }

    // Render page-level progress bar
    renderMilestoneProgress();

    // Initial Render
    renderCourseGrid();
    markAfterAnimationFrames('registrar:grid-rendered');

    // Handle deep link on initial load
    handleHashNavigation();

    // Show "last updated" toast on load
    setTimeout(() => {
        const lastUpdatedEl = document.getElementById('lastUpdated');
        if (lastUpdatedEl) {
            const text = lastUpdatedEl.textContent;
            const match = text.match(/(\d{1,2}\/\d{1,2}\/\d{2,4}.*)/);
            if (match) {
                showToast(`📊 Data updated: ${match[1]}`);
            }
        }
    }, 1000);
}

// Kick off
initApp();
