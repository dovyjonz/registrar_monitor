/**
 * Enrollment Monitor - Application JavaScript
 * 
 * This script handles:
 * - Course grid rendering
 * - Modal interactions for course/section details
 * - Enrollment history charts with Chart.js
 * - Semester switching (combined mode)
 * 
 * Data uses minified keys for smaller file size:
 * - i: snapshotIdx, e: enrollment, c: capacity, f: fill
 * - ce: currentEnrollment, cc: currentCapacity, cf: currentFill
 * - af: averageFill, h: history, s: sections, d: department
 * - in: instructor, ts: timestamp, sid: sectionId, of: overallFill
 * - lrt: lastReportTime, sn: snapshots, cr: courses, sem: semester
 * - sems: semesters, as: activeSemester, sd: semesterData, md: milestonesData
 * - if: isFilled, t: type, ti: title
 */

// Global state
let chart = null;
let selectedCourse = null;
let selectedSection = null;
let viewingGraph = false; // eslint-disable-line no-unused-vars -- tracks modal state
let currentEnrollmentData = [];

// Determine mode from data structure
const IS_COMBINED = typeof COMBINED_DATA !== 'undefined';

// For combined mode: active semester state
let activeSemester = IS_COMBINED
    ? (localStorage.getItem('activeSemester') || COMBINED_DATA.as)
    : null;

// Validate stored semester exists (combined mode)
if (IS_COMBINED && !COMBINED_DATA.sems.includes(activeSemester)) {
    activeSemester = COMBINED_DATA.as;
}

/**
 * Get current semester data based on mode.
 */
function getData() {
    if (IS_COMBINED) {
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
                onclick="switchSemester('${sem}')">${sem}</button>
    `).join('');
}

/**
 * Switch to a different semester (combined mode).
 */
function switchSemester(semester) { // eslint-disable-line no-unused-vars -- called from HTML onclick
    if (!IS_COMBINED) return;

    activeSemester = semester;
    localStorage.setItem('activeSemester', semester);
    closeModal();
    renderSemesterToggle();
    renderCourseGrid();
}

/**
 * Render the main course grid.
 */
function renderCourseGrid() {
    const data = getData();
    const grid = document.getElementById('courseGrid');
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
        const dept = course.d;
        if (!deptCourses[dept]) deptCourses[dept] = [];
        deptCourses[dept].push({ code, ...course });
    }

    // Sort departments alphabetically
    const sortedDepts = Object.keys(deptCourses).sort();

    let totalCourses = 0;
    let totalSections = 0;
    let fullSections = 0;

    for (const dept of sortedDepts) {
        const courses = deptCourses[dept];

        // Department header
        const header = document.createElement('div');
        header.className = 'dept-header';
        header.id = `dept-${dept}`;
        header.innerHTML = `
            <span>${dept}</span>
            <a href="#" class="back-to-top" onclick="event.preventDefault(); window.scrollTo({top: 0, behavior: 'smooth'});">↑ Top</a>
        `;
        grid.appendChild(header);

        // Sort courses by code
        courses.sort((a, b) => a.code.localeCompare(b.code));

        for (const course of courses) {
            totalCourses++;
            const sectionCount = Object.keys(course.s).length;
            totalSections += sectionCount;

            for (const section of Object.values(course.s)) {
                if (section.cf >= 1.0) fullSections++;
            }

            const cell = document.createElement('div');
            cell.className = `course-cell ${getStatusClass(course.af, course.if)}`;
            cell.setAttribute('data-course', course.code);
            cell.innerHTML = `
                <span class="course-code">${formatCourseCode(course.code)}</span>
                <span class="course-fill">${Math.round(course.af * 100)}%</span>
            `;
            cell.onclick = () => openCourse(course.code);
            grid.appendChild(cell);
        }
    }

    // Update stats
    document.getElementById('totalCourses').textContent = totalCourses;
    document.getElementById('totalSections').textContent = totalSections;
    document.getElementById('fullSections').textContent = fullSections;
    document.getElementById('snapshotCount').textContent = data.sn.length;

    // Render jump-to navigation
    const jumpNav = document.getElementById('jumpToNav');
    jumpNav.innerHTML = sortedDepts.map(dept =>
        `<a href="#dept-${dept}">${dept}</a>`
    ).join('');
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
    const title = course.ti ? ` - ${course.ti}` : '';
    document.getElementById('modalTitle').textContent = `${courseCode}${title}`;

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

    setTimeout(() => {
        showAverageFillChart(courseCode);
    }, 50);

    document.documentElement.classList.add('modal-open');
}

/**
 * Show average fill chart for a course.
 */
function showAverageFillChart(courseCode) {
    const data = getData();
    const course = data.cr[courseCode];
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
    renderChart('Average Fill', labels, fillData, timestamps, false);
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
 * Render enrollment chart with milestones.
 */
function renderChart(chartLabel, labels, fillData, timestamps, showCapacityMarkers) {
    const milestones = getMilestones();

    // Build milestone annotations
    const annotations = {};
    if (timestamps.length > 0) {
        const minTime = Math.min(...timestamps);
        const maxTime = Math.max(...timestamps);

        milestones.forEach((m, idx) => {
            const mTime = new Date(m.time).getTime();
            if (mTime >= minTime && mTime <= maxTime) {
                // Find closest label index
                let closestIdx = 0;
                let minDiff = Infinity;
                timestamps.forEach((t, i) => {
                    const diff = Math.abs(t - mTime);
                    if (diff < minDiff) {
                        minDiff = diff;
                        closestIdx = i;
                    }
                });

                // Position label based on fill value
                const fillAtPoint = fillData[closestIdx] || 0;
                const labelPos = fillAtPoint > 50 ? 'start' : 'end';

                annotations[`line${idx}`] = {
                    type: 'line',
                    xMin: closestIdx,
                    xMax: closestIdx,
                    borderColor: m.color,
                    borderWidth: 2,
                    borderDash: [5, 3],
                    drawTime: 'beforeDatasetsDraw',
                    label: {
                        display: true,
                        content: m.label,
                        position: labelPos,
                        backgroundColor: m.color,
                        color: getContrastColor(m.color),
                        font: { size: 9, weight: 'bold' },
                        padding: 3,
                        borderRadius: 3,
                        z: 10,
                        drawTime: 'afterDatasetsDraw',
                    }
                };
            }
        });
    }

    // Show chart canvas
    document.getElementById('chartPlaceholder').style.display = 'none';
    const canvas = document.getElementById('enrollment-chart');
    canvas.classList.remove('chart-hidden');
    canvas.offsetHeight; // Force reflow

    // Destroy existing chart
    if (chart) {
        chart.destroy();
        chart = null;
    }

    // Build point styling for capacity change markers
    const pointStyles = currentEnrollmentData.map(d =>
        showCapacityMarkers && d.capacityChanged ? 'rectRot' : 'circle'
    );
    const pointColors = currentEnrollmentData.map(d =>
        showCapacityMarkers && d.capacityChanged ? '#4ecdc4' : '#ffd700'
    );
    const pointRadii = currentEnrollmentData.map(d =>
        showCapacityMarkers && d.capacityChanged ? 7 : (labels.length > 50 ? 0 : 3)
    );
    const pointBorderColors = currentEnrollmentData.map(d =>
        showCapacityMarkers && d.capacityChanged ? '#ffffff' : '#ffd700'
    );
    const pointBorderWidths = currentEnrollmentData.map(d =>
        showCapacityMarkers && d.capacityChanged ? 2 : 1
    );

    // Create chart
    chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: chartLabel,
                data: fillData,
                borderColor: '#ffd700',
                backgroundColor: 'rgba(255, 215, 0, 0.1)',
                fill: true,
                tension: 0.3,
                pointStyle: pointStyles,
                pointRadius: pointRadii,
                pointHoverRadius: 6,
                pointBackgroundColor: pointColors,
                pointBorderColor: pointBorderColors,
                pointBorderWidth: pointBorderWidths,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                annotation: {
                    annotations: annotations
                },
                legend: {
                    labels: {
                        color: '#eaeaea',
                        font: { family: 'monospace' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1a1a2e',
                    titleColor: '#ffd700',
                    bodyColor: '#eaeaea',
                    borderColor: '#3a3a5e',
                    borderWidth: 1,
                    callbacks: {
                        label: (ctx) => {
                            const idx = ctx.dataIndex;
                            const enrollInfo = currentEnrollmentData[idx];
                            if (enrollInfo && enrollInfo.enrollment !== null) {
                                let label = `${ctx.parsed.y}% (${enrollInfo.enrollment}/${enrollInfo.capacity})`;
                                if (enrollInfo.capacityChanged) {
                                    label += ` • Cap: ${enrollInfo.prevCapacity} → ${enrollInfo.capacity}`;
                                }
                                return label;
                            }
                            return `${ctx.dataset.label}: ${ctx.parsed.y}%`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { display: false },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                },
                y: {
                    min: 0,
                    suggestedMax: 100,
                    ticks: { display: false },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            },
            interaction: {
                intersect: false,
                mode: 'index'
            }
        }
    });
}

/**
 * Close the course detail modal.
 */
function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
    document.documentElement.classList.remove('modal-open');
    selectedCourse = null;
    selectedSection = null;
    viewingGraph = false;
    currentEnrollmentData = [];
    document.getElementById('chartLegend').classList.remove('visible');
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
        chart.tooltip.setActiveElements([]);
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

// Initialize
if (IS_COMBINED) {
    renderSemesterToggle();
}
renderCourseGrid();
