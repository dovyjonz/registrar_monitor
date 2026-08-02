/**
 * Enrollment Monitor - Application JavaScript
 */

import './style.css';
import {
    buildAverageChartPoints,
    buildCourseChartDomain,
    buildSectionChartPoints,
    getChartMapper,
    getXScaleBounds,
} from './chartMapping.mjs';
import {
    courseToSlug,
    getManifestUrl,
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
    const [chartModule, annotationModule, zoomModule] = await Promise.all([
        import('chart.js/auto'),
        import('chartjs-plugin-annotation'),
        import('chartjs-plugin-zoom'),
    ]);
    Chart = chartModule.default;
    Chart.register(annotationModule.default, zoomModule.default);
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
        const header = document.createElement('div');
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

            const cell = document.createElement('div');
            cell.className = `course-cell ${getStatusClass(averageFill, isFilled)}${isStarred ? ' starred' : ''}`;
            cell.setAttribute('data-course', course.code);
            cell.setAttribute('data-status', status);
            cell.setAttribute('data-fill', averageFill);
            cell.setAttribute('tabindex', '0');
            cell.setAttribute('role', 'listitem');
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
            cell.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCourse(course.code); } };
            grid.appendChild(cell);
        }
    }

    // Update stats with animation
    animateCounter(document.getElementById('totalCourses'), totalCourses);
    animateCounter(document.getElementById('totalSections'), totalSections);
    animateCounter(document.getElementById('fullSections'), fullSections);
    animateCounter(
        document.getElementById('snapshotCount'),
        data.snapshotCount ?? (Array.isArray(data.sn) ? data.sn.length : 0),
    );

    // Render jump-to navigation
    renderJumpToNavigation(sortedDepts);

    // Re-apply filters if any are active
    if (typeof currentFilter !== 'undefined' && currentFilter !== 'all') {
        applyFilters();
    }
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

function openCourseModal(courseCode, summaryCourse) {
    const overlay = document.getElementById('modalOverlay');
    if (!overlay?.classList.contains('active')) {
        modalOpener = document.activeElement;
    }

    selectedCourse = courseCode;
    selectedSection = null;
    const title = getCourseTitle(summaryCourse);
    document.getElementById('modalTitle').textContent = `${courseCode}${title ? ` - ${title}` : ''}`;
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
    document.getElementById('modalTitle').textContent = `${courseCode}${title ? ` - ${title}` : ''}`;
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
            const item = document.createElement('div');
            item.className = `section-item ${getStatusClass(section.cf)}`;
            item.id = `section-${section.code}`;
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
    const status = document.getElementById('chartZoomStatus');
    if (resetBtn) {
        resetBtn.disabled = !chart || !isZoomed;
    }
    if (status) {
        status.textContent = chart && isZoomed ? 'Zoomed' : '';
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
function renderMilestoneProgress() {
    const container = document.getElementById('milestoneProgress');
    if (!container) return;
    const milestones = getMilestones();
    if (!milestones || milestones.length < 2) { container.textContent = ''; return; }

    const now = Date.now();
    const mTimes = milestones.map(m => ({ time: new Date(m.time).getTime(), label: m.label, color: m.color })).sort((a, b) => a.time - b.time);
    const count = mTimes.length;

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
    fill.style.clipPath = `inset(0 calc(100% - ${fillPct}%) 0 0)`;
    track.appendChild(fill);

    for (let i = 0; i < count; i++) {
        const m = mTimes[i];
        const pos = (i / (count - 1)) * 100;
        const passed = now >= m.time;
        const dot = document.createElement('div');
        dot.className = `mp-dot${passed ? ' passed' : ''}`;
        dot.style.left = `${pos}%`;
        if (passed) dot.style.background = m.color;
        dot.title = m.label;
        const label = document.createElement('span');
        label.className = 'mp-dot-label';
        label.textContent = m.label;
        dot.appendChild(label);
        track.appendChild(dot);
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

    const hasCapacityChanges = chartPoints.some(point => point.capacityChanged);
    document.getElementById('chartLegend').classList.toggle('visible', hasCapacityChanges);
    await renderChart(courseCode, chartPoints, chartDomain, true, requestVersion);
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
        document.getElementById(`section-${sectionCode}`)?.classList.remove('selected');
        selectedSection = null;
        currentEnrollmentData = [];
        await showAverageFillChart(selectedCourse, course, requestVersion);
        return;
    }

    // Update selection styling
    if (selectedSection) {
        document.getElementById(`section-${selectedSection}`)?.classList.remove('selected');
    }
    selectedSection = sectionCode;
    document.getElementById(`section-${sectionCode}`)?.classList.add('selected');

    const section = getCourseSections(course)[sectionCode];
    if (!section) return;

    const snapshots = getCourseSnapshots(course);
    const chartPoints = buildSectionChartPoints(section, snapshots);
    const chartDomain = buildCourseChartDomain(course, snapshots);
    currentEnrollmentData = chartPoints;

    // Show legend if there are capacity changes
    const hasCapacityChanges = currentEnrollmentData.some(d => d.capacityChanged);
    document.getElementById('chartLegend').classList.toggle('visible', hasCapacityChanges);

    await renderChart(
        `${sectionCode} Enrollment %`,
        chartPoints,
        chartDomain,
        true,
        requestVersion,
    );
}

/**
 * Render enrollment chart with milestones and phased/timeline/snapshots mode.
 */
async function renderChart(
    chartLabel,
    chartPoints,
    chartDomain,
    showCapacityMarkers,
    requestVersion = null,
) {
    await loadChartJs();
    if (requestVersion !== null && requestVersion !== courseRequestVersion) return;
    // Cache args for mode toggle re-render
    lastRenderArgs = {
        chartLabel,
        chartPoints,
        chartDomain,
        showCapacityMarkers,
        requestVersion,
    };

    const milestones = getMilestones();
    const labels = chartPoints.map(point => point.label);
    const fillData = chartPoints.map(point => point.fill);
    const timestamps = chartPoints.map(point => point.timestamp);
    const domainTimestamps = chartDomain.map(point => point.timestamp);
    const { xValues, domainXValues, mapTime } = getChartMapper(chartMode, chartPoints, chartDomain, milestones);
    const xBounds = getXScaleBounds(domainXValues.length > 0 ? domainXValues : xValues);

    // Labels to exclude from non-phased mode (they clutter the chart)
    const DEADLINE_LABELS = new Set(['Drop', 'WL', 'Close']);

    // Build milestone annotations
    const annotations = {};
    if (timestamps.length > 0 && milestones && milestones.length > 0) {
        milestones.forEach((m, idx) => {
            // Skip deadline milestones in timeline and snapshots mode
            if (chartMode !== 'phased' && DEADLINE_LABELS.has(m.label)) return;

            const mTime = new Date(m.time).getTime();

            // In non-phased mode, skip milestones outside data range
            if (chartMode !== 'phased' && domainTimestamps.length > 0) {
                const dataMin = Math.min(...domainTimestamps);
                const dataMax = Math.max(...domainTimestamps);
                if (mTime < dataMin || mTime > dataMax) return;
            }

            const xPos = mapTime(mTime);

            // Position label based on fill value at closest point
            let closestDataIdx = 0, minDiff2 = Infinity;
            xValues.forEach((x, i) => { const d = Math.abs(x - xPos); if (d < minDiff2) { minDiff2 = d; closestDataIdx = i; } });
            const fillAtPoint = fillData[closestDataIdx] || 0;
            const labelPos = fillAtPoint > 50 ? 'start' : 'end';

            annotations[`line${idx}`] = {
                type: 'line', xMin: xPos, xMax: xPos,
                borderColor: m.color, borderWidth: 2, borderDash: [5, 3],
                drawTime: 'beforeDatasetsDraw',
                label: {
                    display: true, content: m.label, position: labelPos,
                    backgroundColor: m.color, color: getContrastColor(m.color),
                    font: { size: 9, weight: 'bold' }, padding: 3, borderRadius: 3,
                    z: 10, drawTime: 'afterDatasetsDraw',
                }
            };
        });
    }

    // Show chart canvas
    document.getElementById('chartPlaceholder').style.display = 'none';
    const canvas = document.getElementById('enrollment-chart');
    canvas.classList.remove('chart-hidden');
    canvas.offsetHeight;

    if (chart) { chart.destroy(); chart = null; }
    setZoomControlsState(false);

    // Point styling
    const pointStyles = currentEnrollmentData.map(d => showCapacityMarkers && d.capacityChanged ? 'rectRot' : 'circle');
    const pointColors = currentEnrollmentData.map(d => showCapacityMarkers && d.capacityChanged ? '#4ecdc4' : '#ffd700');
    const pointRadii = currentEnrollmentData.map(d => showCapacityMarkers && d.capacityChanged ? 7 : (labels.length > 50 ? 0 : 3));
    const pointBorderColors = currentEnrollmentData.map(d => showCapacityMarkers && d.capacityChanged ? '#ffffff' : '#ffd700');
    const pointBorderWidths = currentEnrollmentData.map(d => showCapacityMarkers && d.capacityChanged ? 2 : 1);

    // Build dataset with {x, y} pairs
    const dataPoints = fillData.map((y, i) => ({ x: xValues[i], y }));

    chart = new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                label: chartLabel, data: dataPoints,
                borderColor: '#ffd700', backgroundColor: 'rgba(255, 215, 0, 0.1)',
                fill: true, tension: 0, stepped: 'after',
                pointStyle: pointStyles, pointRadius: pointRadii, pointHoverRadius: 6,
                pointBackgroundColor: pointColors, pointBorderColor: pointBorderColors,
                pointBorderWidth: pointBorderWidths,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            layout: { padding: { left: 8, right: 8, top: 4, bottom: 4 } },
            plugins: {
                annotation: { annotations },
                legend: { display: false },
                zoom: {
                    limits: {
                        x: { min: xBounds.min, max: xBounds.max, minRange: xBounds.minRange },
                        y: { min: 0, max: 100 }
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
                    backgroundColor: '#1a1a2e', titleColor: '#ffd700', bodyColor: '#eaeaea',
                    borderColor: '#3a3a5e', borderWidth: 1,
                    callbacks: {
                        title: (items) => { if (!items.length) return ''; return labels[items[0].dataIndex] || ''; },
                        label: (ctx) => {
                            const idx = ctx.dataIndex;
                            const enrollInfo = currentEnrollmentData[idx];
                            let lbl = `${ctx.parsed.y}%`;
                            if (enrollInfo && enrollInfo.enrollment !== null) {
                                lbl += ` (${enrollInfo.enrollment}/${enrollInfo.capacity})`;
                            }
                            if (enrollInfo?.capacityChanged) {
                                const changes = enrollInfo.capacityChanges?.length
                                    ? enrollInfo.capacityChanges
                                        .map(change => (
                                            `${change.sectionCode} capacity: `
                                            + `${change.previousCapacity} \u2192 ${change.capacity}`
                                        ))
                                        .join('; ')
                                    : `Capacity: ${enrollInfo.prevCapacity} \u2192 ${enrollInfo.capacity}`;
                                lbl += ` \u2022 ${changes}`;
                            }
                            return lbl;
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
                    min: 0, max: 100,
                    ticks: { display: false },
                    grid: { color: 'rgba(255,255,255,0.06)', drawTicks: false },
                    border: { display: false }
                }
            },
            interaction: { intersect: false, mode: 'index' }
        }
    });
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
        showToast('🔗 Share link copied!');
    } catch {
        // Fallback: select a temporary input
        const input = document.createElement('input');
        input.value = shareUrl;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        input.remove();
        showToast('🔗 Share link copied!');
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
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
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
    div.textContent = `No courses match '${query}'. Try a different search term.`;
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

    // Show empty state if no courses are visible (and we have cells to filter)
    if (cells.length > 0 && visibleCount === 0) {
        showEmptyState(searchInput?.value || '');
    } else {
        hideEmptyState();
    }
}

searchInput?.addEventListener('input', () => applyFilters());

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
                const header = document.createElement('div');
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
        // Rebuild jump nav
        const jumpNav = document.getElementById('jumpToNav');
        const depts = [...new Set(cells.map(c => c.dataset.course.split(' ')[0]))];
        jumpNav.textContent = '';
        for (const d of depts) {
            const a = document.createElement('a');
            a.href = `#dept-${d}`;
            a.textContent = d;
            jumpNav.appendChild(a);
        }
    } else {
        // No headers for other sorts
        cells.forEach(c => grid.appendChild(c));
        document.getElementById('jumpToNav').textContent = '';
    }
});

// ============================================
// Bookmarks/Favorites (UX Enhancement)
// ============================================

function saveBookmarks() {
    localStorage.setItem('courseBookmarks', JSON.stringify([...bookmarks]));
}

// ============================================
// Animated Stat Counters (Visual Polish)
// ============================================

function animateCounter(el, target, duration = 800) {
    if (!el) return;
    const start = 0;
    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        const current = Math.round(start + (target - start) * eased);
        el.textContent = current;

        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }
    requestAnimationFrame(update);
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
    modalBookmarkBtn.textContent = isStarred ? '⭐' : '☆';
    modalBookmarkBtn.classList.toggle('starred', isStarred);
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
        showToast(bookmarks.has(code) ? '⭐ Bookmarked!' : '☆ Removed bookmark');
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
                a.showCapacityMarkers,
                a.requestVersion,
            );
        }
    });
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
