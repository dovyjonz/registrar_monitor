import { IntegrityError, UnsupportedSchemaError } from './manifestData.mjs';
import { getSectionTypeName } from './chartMapping.mjs';
import { appState, getCourseSections, getCourseTitle, getSummaryCourse, refreshCourseMaps } from './state';
import { clearChartActiveElements, selectSection, setZoomControlsState, showAverageFillChart } from './chart';
import { hasHistoricalCandidateCache, initializeHistoricalComparisonControl, resetHistoricalComparisonState, resolveHistoricalAvailability } from './comparison';
import { showToast, updateModalBookmark } from './bookmarks';
import { getSemesterSlug, showCourseRoute } from './routing';
import { getStatusClass } from './format';
import { renderEventHistory } from './events';
import { markPerformance } from './perf';
import { hydrateCourse } from './grid';
import { modalOverlay } from './dom';

// Export to window so it can be called from HTML onclick
(window as any).closeModal = closeModal;

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

    appState.currentEnrollmentData = [];
    appState.lastRenderArgs = null;
    document.getElementById('chartLegend')?.classList.remove('visible');
    const unavailableGuide = document.getElementById('registrationUnavailableGuide');
    if (unavailableGuide) unavailableGuide.hidden = true;
    const readout = document.getElementById('chartReadout');
    if (readout) {
        readout.hidden = true;
        readout.textContent = '';
    }
    const placeholder = document.getElementById('chartPlaceholder');
    if (placeholder) placeholder.style.display = '';
    document.getElementById('enrollment-chart')?.classList.add('chart-hidden');
    if (appState.chart) {
        appState.chart.destroy();
        appState.chart = null;
    }
    setZoomControlsState(false);
}

function createModalDetailState({ className, role, message }) {
    const body = document.querySelector<HTMLElement>('.modal-body');
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
    if (requestVersion !== appState.courseRequestVersion || appState.selectedCourse !== courseCode) return;

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
        if (appState.selectedCourse === courseCode) retryCourse(courseCode);
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
}

function renderCoursePublicationState(course) {
    const state = document.getElementById('courseState');
    if (!state) return;
    const preview = course?.previewState;
    if (preview?.status !== 'removed') {
        state.hidden = true;
        state.textContent = '';
        return;
    }
    state.textContent = '';
    const badge = document.createElement('strong');
    badge.className = 'removed-badge';
    badge.textContent = 'REMOVED';
    state.appendChild(badge);
    const copy = document.createElement('span');
    const changed = preview?.lastChanged
        ? new Date(preview.lastChanged).toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        })
        : null;
    copy.textContent = changed
        ? `Removed from the registrar listing. Final state: ${changed}`
        : 'Removed from the registrar listing';
    state.appendChild(copy);
    state.hidden = false;
}

export function openCourseModal(courseCode, summaryCourse) {
    const overlay = document.getElementById('modalOverlay');
    if (!overlay?.classList.contains('active')) {
        appState.modalOpener = document.activeElement as HTMLElement;
    }

    appState.selectedCourse = courseCode;
    appState.selectedSection = null;
    appState.selectedCourseShareUrl = null;
    resetHistoricalComparisonState();
    const title = getCourseTitle(summaryCourse);
    updateModalCourseTitle(courseCode, title);
    updateModalBookmark(courseCode);
    overlay.classList.add('active');
    document.documentElement.classList.add('modal-open');
    document.body.classList.add('modal-open');

    // Make background inert while the existing modal is open.
    document.getElementById('main-content').setAttribute('inert', '');
    document.querySelector<HTMLElement>('header').setAttribute('inert', '');
    document.querySelector<HTMLElement>('.controls-panel').setAttribute('inert', '');

    document.querySelectorAll<HTMLElement>('.chart-mode-btn').forEach(b => {
        const isActive = b.dataset.mode === appState.chartMode;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-pressed', String(isActive));
    });

    const shareBtn = document.getElementById('modalShareLink');
    if (shareBtn) shareBtn.style.display = '';
    void showCourseRoute(courseCode);
    showModalDetailLoading();
    focusModal();
}

export function renderCourseDetails(courseCode, course, requestVersion) {
    if (requestVersion !== appState.courseRequestVersion || appState.selectedCourse !== courseCode) return;

    document.getElementById('modalDetailState')?.remove();
    setModalDetailVisibility(true);
    const title = getCourseTitle(course);
    updateModalCourseTitle(courseCode, title);
    updateModalBookmark(courseCode);
    renderCoursePublicationState(course);

    const sectionList = document.getElementById('sectionList');
    const sections = Object.entries<any>(getCourseSections(course)).sort((a, b) => {
        const typePriority: Record<string, number> = { L: 0, S: 1, R: 1, D: 1, B: 2, Lb: 2 };
        const pa = typePriority[a[1].t] ?? 3;
        const pb = typePriority[b[1].t] ?? 3;
        if (pa !== pb) return pa - pb;
        return a[0].localeCompare(b[0], undefined, { numeric: true });
    });

    const sectionsByType: Record<string, any[]> = {};
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
        if (requestVersion !== appState.courseRequestVersion || appState.selectedCourse !== courseCode) return;
        showAverageFillChart(courseCode, course, requestVersion)
            .then(() => {
                if (requestVersion === appState.courseRequestVersion && appState.selectedCourse === courseCode) {
                    initializeHistoricalComparisonControl(course);
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
    if (requestVersion !== appState.courseRequestVersion || appState.selectedCourse !== courseCode) return;
    if (!course) {
        showModalDetailError(courseCode, new Error('Missing department data'), requestVersion);
        return;
    }
    markPerformance('registrar:course-detail-ready');
    renderCourseDetails(courseCode, course, requestVersion);
}

export async function openCourse(courseCode) {
    refreshCourseMaps();
    const summaryCourse = getSummaryCourse(courseCode);
    if (!summaryCourse) return;

    const requestVersion = ++appState.courseRequestVersion;
    openCourseModal(courseCode, summaryCourse);
    await loadAndRenderCourse(courseCode, requestVersion);
}

async function retryCourse(courseCode) {
    if (appState.selectedCourse !== courseCode || !document.getElementById('modalOverlay')?.classList.contains('active')) {
        return;
    }
    const requestVersion = ++appState.courseRequestVersion;
    showModalDetailLoading();
    await loadAndRenderCourse(courseCode, requestVersion);
}

/**
 * Close the course detail modal.
 */
function finishModalClose(overlay) {
    if (overlay.classList.contains('active')) return;

    document.documentElement.classList.remove('modal-open');
    document.body.classList.remove('modal-open');
    document.getElementById('main-content')?.removeAttribute('inert');
    document.querySelector<HTMLElement>('header')?.removeAttribute('inert');
    document.querySelector<HTMLElement>('.controls-panel')?.removeAttribute('inert');

    const shareBtn = document.getElementById('modalShareLink');
    if (shareBtn) shareBtn.style.display = 'none';

    if (appState.modalOpener && typeof appState.modalOpener.focus === 'function') {
        appState.modalOpener.focus();
        appState.modalOpener = null;
    }

    appState.selectedCourse = null;
    appState.selectedSection = null;
    appState.selectedCourseShareUrl = null;
    appState.currentEnrollmentData = [];
    appState.lastRenderArgs = null;
    resetHistoricalComparisonState();
    resetCourseDetailView();
}

function closeModal() {
    const overlay = document.getElementById('modalOverlay');
    if (!overlay?.classList.contains('active')) return;

    appState.courseRequestVersion += 1;
    const finish = event => {
        if (event.target !== overlay || event.propertyName !== 'opacity') return;
        overlay.removeEventListener('transitionend', finish);
        finishModalClose(overlay);
    };
    overlay.addEventListener('transitionend', finish);
    overlay.classList.remove('active');

    if (getComputedStyle(overlay).transitionDuration === '0s') {
        overlay.removeEventListener('transitionend', finish);
        finishModalClose(overlay);
    }

    history.replaceState(null, '', `/semesters/${getSemesterSlug()}/`);
}

modalOverlay.addEventListener('pointerdown', (event) => {
    appState.backdropGesture = {
        pointerId: event.pointerId,
        startedOnBackdrop: event.target === modalOverlay,
        startX: event.clientX,
        startY: event.clientY,
        moved: false,
    };
}, true);

modalOverlay.addEventListener('pointermove', (event) => {
    if (!appState.backdropGesture || event.pointerId !== appState.backdropGesture.pointerId) return;
    const distance = Math.hypot(
        event.clientX - appState.backdropGesture.startX,
        event.clientY - appState.backdropGesture.startY,
    );
    if (distance > 8) appState.backdropGesture.moved = true;
}, true);

modalOverlay.addEventListener('pointercancel', () => {
    appState.backdropGesture = null;
}, true);

modalOverlay.addEventListener('click', (event) => {
    const dismiss = event.target === modalOverlay
        && appState.backdropGesture?.startedOnBackdrop
        && !appState.backdropGesture.moved;
    appState.backdropGesture = null;
    if (dismiss) closeModal();
});

/**
 * Get all focusable elements within the modal.
 */
function getModalFocusableElements() {
    const modal = document.querySelector<HTMLElement>('.modal');
    if (!modal) return [];
    return [...modal.querySelectorAll<HTMLElement>(
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
    if (!appState.selectedCourse) return;
    const courseCode = appState.selectedCourse;
    if (!appState.selectedCourseShareUrl) await showCourseRoute(courseCode);
    if (appState.selectedCourse !== courseCode || !appState.selectedCourseShareUrl) {
        showToast('Share link unavailable');
        return;
    }
    const shareUrl = appState.selectedCourseShareUrl;

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

document.getElementById('chartContainer').addEventListener('wheel', (e) => {
    if (appState.chart) {
        e.preventDefault();
        e.stopPropagation();
    }
}, { passive: false });

document.querySelector<HTMLElement>('.modal').addEventListener('click', (e) => {
    if (!(e.target as HTMLElement).closest('.chart-wrapper')) {
        clearChartActiveElements();
    }
});

document.querySelector<HTMLElement>('.modal').addEventListener('touchstart', (e) => {
    if (!(e.target as HTMLElement).closest('.chart-wrapper')) clearChartActiveElements();
}, { passive: true });
