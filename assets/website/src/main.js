/**
 * Enrollment Monitor - Application JavaScript
 */

import './style.css';
import Chart from 'chart.js/auto';
import annotationPlugin from 'chartjs-plugin-annotation';

Chart.register(annotationPlugin);

// Global state
let chart = null;
let selectedCourse = null;
let selectedSection = null;
let viewingGraph = false;
let currentEnrollmentData = [];
let chartMode = localStorage.getItem('chartMode') || 'phased'; // 'phased' or 'timeline'

// Cache for last render args so toggle can re-render
let lastRenderArgs = null;

// Access global variables injected by Python
let DATA = window.DATA || null;
let MILESTONES = window.MILESTONES || [];
let COMBINED_DATA = window.COMBINED_DATA;

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

    toggle.innerHTML = COMBINED_DATA.sems.map(sem => `
        <button class="semester-btn ${sem === activeSemester ? 'active' : ''}"
                onclick="window.switchSemester('${sem}')">${sem}</button>
    `).join('');
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
function renderCourseGrid() {
    const data = getData();
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    grid.innerHTML = '';

    // Update header text
    const lastUpdatedEl = document.getElementById('lastUpdated');
    if (lastUpdatedEl) {
        const semester = IS_COMBINED ? activeSemester : data.sem;
        lastUpdatedEl.textContent = `${semester} • Last updated ${formatDate(data.lrt)}`;
    }

    // Group courses by department (using minified key 'd')
    const deptCourses = {};
    for (const [code, course] of Object.entries(data.cr)) {
        // Handle department parsing safely
        const parts = code.split(' ');
        const dept = parts.length > 0 ? parts[0] : 'Other';

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
        header.innerHTML = `
            <span>${dept}</span>
            <a href="#" class="back-to-top" onclick="event.preventDefault(); window.scrollTo({top: 0, behavior: 'smooth'});">↑ Top</a>
        `;
        grid.appendChild(header);

        const courses = deptCourses[dept];
        // Sort courses by code
        courses.sort((a, b) => a.code.localeCompare(b.code));

        for (const course of courses) {
            totalCourses++;
            const sectionCount = Object.keys(course.s).length;
            totalSections += sectionCount;

            for (const section of Object.values(course.s)) {
                if (section.cf >= 1.0) fullSections++;
            }

            const status = course.if || course.af >= 1 ? 'full' :
                course.af >= 0.8 ? 'near' : 'open';
            const isStarred = bookmarks.has(course.code);

            const cell = document.createElement('div');
            cell.className = `course-cell ${getStatusClass(course.af, course.if)}${isStarred ? ' starred' : ''}`;
            cell.setAttribute('data-course', course.code);
            cell.setAttribute('data-status', status);
            cell.setAttribute('data-fill', course.af);
            cell.setAttribute('tabindex', '0');
            cell.setAttribute('role', 'listitem');
            cell.style.setProperty('--cell-index', totalCourses);
            cell.innerHTML = `
                <span class="course-code">${formatCourseCode(course.code)}</span>
                <span class="course-fill">${Math.round(course.af * 100)}%</span>
            `;
            cell.onclick = () => openCourse(course.code);
            cell.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCourse(course.code); } };
            grid.appendChild(cell);
        }
    }

    // Update stats with animation
    animateCounter(document.getElementById('totalCourses'), totalCourses);
    animateCounter(document.getElementById('totalSections'), totalSections);
    animateCounter(document.getElementById('fullSections'), fullSections);
    animateCounter(document.getElementById('snapshotCount'), data.sn.length);

    // Render jump-to navigation
    const jumpNav = document.getElementById('jumpToNav');
    if (jumpNav) {
        jumpNav.innerHTML = sortedDepts.map(dept =>
            `<a href="#dept-${dept}">${dept}</a>`
        ).join('');
    }

    // Re-apply filters if any are active
    if (typeof currentFilter !== 'undefined' && currentFilter !== 'all') {
        applyFilters();
    }
}

/**
 * Open course detail modal.
 */
function openCourse(courseCode) {
    const data = getData();
    selectedCourse = courseCode;
    selectedSection = null;
    viewingGraph = false;

    const course = data.cr[courseCode];
    if (!course) return;

    const title = course.ti ? ` - ${course.ti}` : '';
    document.getElementById('modalTitle').textContent = `${courseCode}${title}`;

    // Update bookmark button state
    updateModalBookmark(courseCode);

    const sectionList = document.getElementById('sectionList');
    sectionList.innerHTML = '';

    // Sort sections by type then by ID (using minified keys)
    const sections = Object.entries(course.s).sort((a, b) => {
        const typePriority = { L: 0, S: 1, R: 1, D: 1, B: 2, Lb: 2 };
        const pa = typePriority[a[1].t] ?? 3;
        const pb = typePriority[b[1].t] ?? 3;
        if (pa !== pb) return pa - pb;
        return a[0].localeCompare(b[0], undefined, { numeric: true });
    });

    // Group sections by type
    const sectionsByType = {};
    for (const [sectionCode, section] of sections) {
        const type = section.t || 'Other';
        if (!sectionsByType[type]) sectionsByType[type] = [];
        sectionsByType[type].push({ code: sectionCode, ...section });
    }

    // Render section type selector
    const sectionTypeSelector = document.getElementById('sectionTypeSelector');
    sectionTypeSelector.innerHTML = '';

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
            item.innerHTML = `
                <div class="section-id">${section.code}</div>
                ${section.in ? `<div class="section-instructor">${section.in}</div>` : ''}
                <div class="section-stats">
                    <span class="section-fill">${Math.round(section.cf * 100)}%</span>
                    <span>(${section.ce}/${section.cc})</span>
                </div>
            `;
            item.onclick = () => selectSection(section.code);
            groupList.appendChild(item);
        }

        typeGroup.appendChild(groupList);
        sectionTypeSelector.appendChild(typeGroup);
    }

    // Show modal and render default chart
    document.getElementById('modalOverlay').classList.add('active');

    // Update chart mode toggle state
    document.querySelectorAll('.chart-mode-btn').forEach(b => {
        const isActive = b.dataset.mode === chartMode;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-pressed', isActive);
    });

    renderEventHistory(courseCode);

    // Update URL hash for deep linking
    history.replaceState(null, '', '#' + courseCode.replace(/\s+/g, '-'));

    setTimeout(() => {
        showAverageFillChart(courseCode);
    }, 50);

    document.body.classList.add('modal-open');
}

/**
 * Compute warped x-values so each milestone segment gets equal width.
 */
function computeWarpedX(timestamps, milestones) {
    if (!milestones || milestones.length < 2) return null;

    const mTimes = milestones.map(m => new Date(m.time).getTime()).sort((a, b) => a - b);
    // Add virtual boundaries: start = first data point, end = last data point
    const allBounds = [Math.min(timestamps[0], mTimes[0]), ...mTimes, Math.max(timestamps[timestamps.length - 1], mTimes[mTimes.length - 1])];
    // Remove duplicates and sort
    const bounds = [...new Set(allBounds)].sort((a, b) => a - b);
    const segCount = bounds.length - 1;
    if (segCount <= 0) return null;

    const segWidth = 100; // each segment gets 100 units
    return timestamps.map(t => {
        // Find which segment this timestamp falls into
        for (let s = 0; s < segCount; s++) {
            if (t <= bounds[s + 1]) {
                const segStart = bounds[s];
                const segEnd = bounds[s + 1];
                const frac = segEnd === segStart ? 0.5 : (t - segStart) / (segEnd - segStart);
                return s * segWidth + frac * segWidth;
            }
        }
        return (segCount - 1) * segWidth + segWidth; // beyond last
    });
}

/**
 * Render milestone progress bar with equal-spaced dots.
 */
function renderMilestoneProgress() {
    const container = document.getElementById('milestoneProgress');
    if (!container) return;
    const milestones = getMilestones();
    if (!milestones || milestones.length < 2) { container.innerHTML = ''; return; }

    const now = Date.now();
    const mTimes = milestones.map(m => ({ time: new Date(m.time).getTime(), label: m.label, color: m.color })).sort((a, b) => a.time - b.time);
    const count = mTimes.length;

    // Find how far along we are in terms of milestone segments passed
    let filledSegments = 0;
    for (let i = 0; i < count; i++) {
        if (now >= mTimes[i].time) filledSegments = i + 1;
    }
    // Interpolate within current segment
    let fillPct = 0;
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

    const dots = mTimes.map((m, i) => {
        const pos = (i / (count - 1)) * 100; // Equal spacing
        const passed = now >= m.time;
        return `<div class="mp-dot${passed ? ' passed' : ''}" style="left:${pos}%;background:${passed ? m.color : ''}" title="${m.label}"><span class="mp-dot-label">${m.label}</span></div>`;
    }).join('');

    container.innerHTML = `
        <div class="mp-track">
            <div class="mp-fill" style="width:${fillPct}%"></div>
            ${dots}
        </div>`;
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
function renderEventHistory(courseCode) {
    const data = getData();
    const course = data.cr[courseCode];
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
        let html = '<div class="ef-row">';
        // Sort toggle
        html += `<button class="ef-pill ef-sort" data-sort="newest" title="Sort order">↓ Newest</button>`;
        // Type filter
        html += `<select class="ef-select" id="efType" aria-label="Filter by type"><option value="all">All types</option>`;
        for (const t of types) {
            html += `<option value="${t}">${EVENT_ICONS[t] || ''} ${EVENT_LABELS[t] || t}</option>`;
        }
        html += '</select>';
        // Section filter
        if (sections.length > 1) {
            html += `<select class="ef-select" id="efSection" aria-label="Filter by section"><option value="all">All sections</option>`;
            for (const s of sections) html += `<option value="${s}">${s}</option>`;
            html += '</select>';
        }
        html += '</div>';
        filtersEl.innerHTML = html;

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

    container.innerHTML = filtered.length === 0
        ? '<div class="event-item" style="color:hsl(var(--muted-foreground));justify-content:center;">No matching events</div>'
        : filtered.map(e => {
            const icon = EVENT_ICONS[e.et] || '📝';
            const desc = eventDesc(e);
            const ts = e.st ? new Date(e.st).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
            return `<div class="event-item"><span class="event-icon">${icon}</span><span class="event-desc">${desc}</span><span class="event-ts">${ts}</span></div>`;
        }).join('');
}

/**
 * Show average fill chart for a course.
 */
function showAverageFillChart(courseCode) {
    const data = getData();
    const course = data.cr[courseCode];
    if (!course) return;

    const sectionsArr = Object.values(course.s);

    // Build average fill data across all snapshots
    const snapshotFills = {};
    for (const section of sectionsArr) {
        for (const point of section.h) {
            if (!snapshotFills[point.i]) {
                snapshotFills[point.i] = [];
            }
            snapshotFills[point.i].push(point.f);
        }
    }

    // Sort by snapshot index and compute averages
    const sortedIndices = Object.keys(snapshotFills).map(Number).sort((a, b) => a - b);
    const labels = [];
    const fillData = [];
    const timestamps = [];
    currentEnrollmentData = [];

    for (const idx of sortedIndices) {
        const snapshot = data.sn[idx];
        if (snapshot) {
            const fills = snapshotFills[idx];
            const avgFill = fills.reduce((a, b) => a + b, 0) / fills.length;
            const date = new Date(snapshot.ts);
            timestamps.push(date.getTime());
            labels.push(date.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }));
            fillData.push(Math.round(avgFill * 100));
            currentEnrollmentData.push({
                enrollment: null,
                capacity: null,
                prevCapacity: null,
                capacityChanged: false
            });
        }
    }

    document.getElementById('chartLegend').classList.remove('visible');
    renderChart(courseCode, labels, fillData, timestamps, false);
}

/**
 * Select a section and show its enrollment chart.
 */
function selectSection(sectionCode) {
    const data = getData();

    // Toggle selection if clicking same section
    if (selectedSection === sectionCode) {
        document.getElementById(`section-${sectionCode}`)?.classList.remove('selected');
        selectedSection = null;
        viewingGraph = false;
        currentEnrollmentData = [];
        showAverageFillChart(selectedCourse);
        return;
    }

    // Update selection styling
    if (selectedSection) {
        document.getElementById(`section-${selectedSection}`)?.classList.remove('selected');
    }
    selectedSection = sectionCode;
    viewingGraph = true;
    document.getElementById(`section-${sectionCode}`)?.classList.add('selected');

    const section = data.cr[selectedCourse].s[sectionCode];

    // Prepare chart data with capacity change tracking
    const labels = [];
    const fillData = [];
    const timestamps = [];
    currentEnrollmentData = [];
    let prevCapacity = null;

    for (const point of section.h) {
        const snapshot = data.sn[point.i];
        if (snapshot) {
            const date = new Date(snapshot.ts);
            timestamps.push(date.getTime());
            labels.push(date.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }));
            fillData.push(Math.round(point.f * 100));

            const capacityChanged = prevCapacity !== null && point.c !== prevCapacity;
            currentEnrollmentData.push({
                enrollment: point.e,
                capacity: point.c,
                prevCapacity: prevCapacity,
                capacityChanged: capacityChanged
            });
            prevCapacity = point.c;
        }
    }

    // Show legend if there are capacity changes
    const hasCapacityChanges = currentEnrollmentData.some(d => d.capacityChanged);
    document.getElementById('chartLegend').classList.toggle('visible', hasCapacityChanges);

    renderChart(`${sectionCode} Enrollment %`, labels, fillData, timestamps, true);
}

/**
 * Render enrollment chart with milestones and phased/timeline mode.
 */
function renderChart(chartLabel, labels, fillData, timestamps, showCapacityMarkers) {
    // Cache args for mode toggle re-render
    lastRenderArgs = { chartLabel, labels, fillData, timestamps, showCapacityMarkers };

    const milestones = getMilestones();
    const usePhased = chartMode === 'phased';
    const warpedX = usePhased ? computeWarpedX(timestamps, milestones) : null;

    // Build x-axis data
    // Timeline mode: use actual timestamps for proper proportional spacing
    // Phased mode: use warped values for equal-segment spacing
    const xValues = warpedX || timestamps;

    // Labels to exclude from timeline mode (they clutter the chart)
    const DEADLINE_LABELS = new Set(['Drop', 'WL', 'Close']);

    // Build milestone annotations
    const annotations = {};
    if (timestamps.length > 0 && milestones && milestones.length > 0) {
        milestones.forEach((m, idx) => {
            // Skip deadline milestones in timeline mode
            if (!usePhased && DEADLINE_LABELS.has(m.label)) return;

            const mTime = new Date(m.time).getTime();

            // In timeline mode, skip milestones outside data range
            if (!usePhased && timestamps.length > 0) {
                const dataMin = Math.min(...timestamps);
                const dataMax = Math.max(...timestamps);
                if (mTime < dataMin || mTime > dataMax) return;
            }

            let xPos;
            if (warpedX) {
                const mTimes = milestones.map(ms => new Date(ms.time).getTime()).sort((a, b) => a - b);
                const allBounds = [Math.min(timestamps[0], mTimes[0]), ...mTimes, Math.max(timestamps[timestamps.length - 1], mTimes[mTimes.length - 1])];
                const bounds = [...new Set(allBounds)].sort((a, b) => a - b);
                const segWidth = 100;
                for (let s = 0; s < bounds.length - 1; s++) {
                    if (mTime <= bounds[s + 1]) {
                        const frac = bounds[s + 1] === bounds[s] ? 0.5 : (mTime - bounds[s]) / (bounds[s + 1] - bounds[s]);
                        xPos = s * segWidth + frac * segWidth;
                        break;
                    }
                }
                if (xPos === undefined) xPos = (bounds.length - 2) * segWidth + segWidth;
            } else {
                xPos = mTime;
            }

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
                fill: true, tension: 0.3,
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
                tooltip: {
                    backgroundColor: '#1a1a2e', titleColor: '#ffd700', bodyColor: '#eaeaea',
                    borderColor: '#3a3a5e', borderWidth: 1,
                    callbacks: {
                        title: (items) => { if (!items.length) return ''; return labels[items[0].dataIndex] || ''; },
                        label: (ctx) => {
                            const idx = ctx.dataIndex;
                            const enrollInfo = currentEnrollmentData[idx];
                            if (enrollInfo && enrollInfo.enrollment !== null) {
                                let lbl = `${ctx.parsed.y}% (${enrollInfo.enrollment}/${enrollInfo.capacity})`;
                                if (enrollInfo.capacityChanged) lbl += ` \u2022 Cap: ${enrollInfo.prevCapacity} \u2192 ${enrollInfo.capacity}`;
                                return lbl;
                            }
                            return `${ctx.parsed.y}%`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    display: false,
                    ...(usePhased ? {} : {
                        min: Math.min(...timestamps) - (timestamps.length > 1 ? (timestamps[1] - timestamps[0]) * 0.5 : 60000),
                        max: Math.max(...timestamps) + (timestamps.length > 1 ? (timestamps[timestamps.length-1] - timestamps[timestamps.length-2]) * 0.5 : 60000)
                    })
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
}

/**
 * Close the course detail modal.
 */
function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
    document.body.classList.remove('modal-open');
    selectedCourse = null;
    selectedSection = null;
    viewingGraph = false;
    currentEnrollmentData = [];
    lastRenderArgs = null;
    document.getElementById('chartLegend').classList.remove('visible');
    // Clear URL hash
    history.replaceState(null, '', window.location.pathname);
    if (chart) {
        chart.destroy();
        chart = null;
    }
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

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
});

document.getElementById('chartContainer').addEventListener('touchend', () => {
    setTimeout(clearChartActiveElements, 100);
});

document.querySelector('.modal-body').addEventListener('click', (e) => {
    if (!e.target.closest('#chartContainer')) {
        clearChartActiveElements();
    }
});


// ============================================
// Search Functionality (Phase 4)
// ============================================

const searchInput = document.getElementById('courseSearch');

/**
 * Filter courses based on search query.
 */
function filterCourses(query) {
    const cells = document.querySelectorAll('.course-cell');
    const normalizedQuery = query.toLowerCase().trim();

    cells.forEach(cell => {
        const code = cell.getAttribute('data-course').toLowerCase();
        const matches = !normalizedQuery || code.includes(normalizedQuery);
        cell.classList.toggle('hidden', !matches);
    });

    // Show/hide department headers based on visible courses
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
}

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

function applyFilters() {
    const searchQuery = searchInput?.value.toLowerCase().trim() || '';
    const cells = document.querySelectorAll('.course-cell');

    let visibleCount = 0;
    cells.forEach(cell => {
        const code = cell.getAttribute('data-course').toLowerCase();
        const status = cell.dataset.status;
        const isStarred = cell.classList.contains('starred');

        let matchesSearch = !searchQuery || code.includes(searchQuery);
        let matchesFilter = currentFilter === 'all' ||
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
}

// Override original filterCourses to use applyFilters
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
                header.innerHTML = `${dept} <a href="#" class="back-to-top" onclick="scrollTo({top:0,behavior:'smooth'});return false;">↑ Top</a>`;
                grid.appendChild(header);
            }
            grid.appendChild(cell);
        });
        // Rebuild jump nav
        const jumpNav = document.getElementById('jumpToNav');
        const depts = [...new Set(cells.map(c => c.dataset.course.split(' ')[0]))];
        jumpNav.innerHTML = depts.map(d => `<a href="#dept-${d}">${d}</a>`).join('');
    } else {
        // No headers for other sorts
        cells.forEach(c => grid.appendChild(c));
        document.getElementById('jumpToNav').innerHTML = '';
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
    btn.addEventListener('click', () => {
        chartMode = btn.dataset.mode;
        localStorage.setItem('chartMode', chartMode);
        document.querySelectorAll('.chart-mode-btn').forEach(b => {
            const isActive = b.dataset.mode === chartMode;
            b.classList.toggle('active', isActive);
            b.setAttribute('aria-pressed', isActive);
        });
        if (lastRenderArgs) {
            const a = lastRenderArgs;
            renderChart(a.chartLabel, a.labels, a.fillData, a.timestamps, a.showCapacityMarkers);
        }
    });
});

// ============================================
// Deep Linking via URL Hash
// ============================================

function handleHashNavigation() {
    const hash = window.location.hash.slice(1); // Remove '#'
    if (!hash) return;
    const courseCode = hash.replace(/-/g, ' '); // 'CSCI-101' -> 'CSCI 101'
    const data = getData();
    if (data && data.cr && data.cr[courseCode]) {
        openCourse(courseCode);
    }
}

window.addEventListener('hashchange', handleHashNavigation);

// ============================================
// Initialization
// ============================================

async function initApp() {
    // If JSON_URL is provided, fetch it asynchronously
    if (window.JSON_URL && !DATA) {
        try {
            const res = await fetch(window.JSON_URL);
            if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
            const payload = await res.json();
            DATA = payload.data;
            MILESTONES = payload.milestones;
            
            const loader = document.getElementById('loadingIndicator');
            if (loader) loader.remove();
        } catch (error) {
            console.error("Failed to load enrollment data:", error);
            const loader = document.getElementById('loadingIndicator');
            if (loader) {
                loader.innerHTML = `<div style="color: #ff1744;">Failed to load data. Please refresh.</div>`;
            }
            return;
        }
    }

    if (IS_COMBINED) {
        renderSemesterToggle();
    }

    // Render page-level progress bar
    renderMilestoneProgress();

    // Initial Render
    renderCourseGrid();

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
