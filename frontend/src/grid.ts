import { getCoursePublicState } from './registrationSemantics.mjs';
import { getElectiveCategories } from './electiveFilters.mjs';
import { loadDepartmentPayload } from './manifestData.mjs';
import { appState, bookmarks, dataLoadController, departmentPayloads, getCourseDepartment, getCourseSections, getData, hydratedCourses, refreshCourseMaps, summaryCourses } from './state';
import { applyFilters, setDepartmentPanelOpen } from './search';
import { formatCourseCode, formatDate, getStatusClass } from './format';
import { openCourse } from './modal';
import { markPerformance } from './perf';

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
    const sections = Object.values<any>(getCourseSections(course));
    return {
        sectionCount: sections.length,
        fullSectionCount: sections.filter(section => section.cf >= 1.0).length,
    };
}

export function renderJumpToNavigation(sortedDepts) {
    const jumpNav = document.getElementById('jumpToNav');
    if (!jumpNav) return;

    jumpNav.textContent = '';
    for (const dept of sortedDepts) {
        const a = document.createElement('a');
        a.href = `#dept-${dept}`;
        a.textContent = dept;
        a.addEventListener('click', event => {
            event.preventDefault();
            setDepartmentPanelOpen(false);
            requestAnimationFrame(() => {
                const target = document.getElementById(`dept-${dept}`);
                if (!target) return;
                const alignTarget = () => {
                    window.scrollTo({
                        top: window.scrollY + target.getBoundingClientRect().top - 16,
                        behavior: 'instant',
                    });
                };
                alignTarget();
                requestAnimationFrame(alignTarget);
            });
        });
        jumpNav.appendChild(a);
    }

    const toggle = document.getElementById('departmentToggle');
    const count = document.getElementById('departmentCount');
    const hasDepartments = sortedDepts.length > 0;
    if (count) count.textContent = hasDepartments ? String(sortedDepts.length) : '';
    if (toggle) {
        toggle.hidden = false;
        (toggle as HTMLButtonElement).disabled = !hasDepartments;
        if (!hasDepartments) toggle.setAttribute('aria-expanded', 'false');
    }
    const panel = document.getElementById('departmentPanel');
    if (panel) panel.hidden = !hasDepartments || toggle?.getAttribute('aria-expanded') !== 'true';
}

export function renderCourseGrid() {
    const data = getData();
    const grid = document.getElementById('courseGrid');
    if (!grid || !data) return;
    refreshCourseMaps();
    grid.textContent = '';

    // Update header text
    const lastUpdatedEl = document.getElementById('lastUpdated');
    if (lastUpdatedEl) {
        const semester = data.semester || data.sem;
        const staleLabel = appState.staticManifestStale ? 'Stale data • ' : '';
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

            const publicState = getCoursePublicState(course);
            const isStarred = bookmarks.has(course.code);

            const cell = document.createElement('button');
            cell.type = 'button';
            cell.className = `course-cell ${getStatusClass(publicState.averageFill, publicState.isFilled)}${isStarred ? ' starred' : ''}`;
            cell.setAttribute('data-course', course.code);
            cell.setAttribute('data-status', publicState.status);
            cell.setAttribute('data-fill', publicState.averageFill);
            cell.setAttribute('data-electives', getElectiveCategories(course.code).join(' '));
            cell.setAttribute(
                'aria-label',
                `${formatCourseCode(course.code)}: ${publicState.accessibilityCopy}`,
            );
            cell.style.setProperty('--cell-index', String(totalCourses));
            const codeSpan = document.createElement('span');
            codeSpan.className = 'course-code';
            codeSpan.textContent = formatCourseCode(course.code);
            cell.appendChild(codeSpan);
            const fillSpan = document.createElement('span');
            fillSpan.className = 'course-fill';
            fillSpan.textContent = publicState.readout;
            cell.appendChild(fillSpan);
            const reason = document.createElement('span');
            reason.className = 'search-reason';
            reason.id = `search-reason-${totalCourses}`;
            reason.hidden = true;
            cell.appendChild(reason);
            cell.onclick = () => openCourse(course.code);
            grid.appendChild(cell);
        }
    }

    // Show complete totals immediately; intermediate animated values are misleading.
    document.getElementById('totalCourses').textContent = String(totalCourses);
    document.getElementById('totalSections').textContent = String(totalSections);
    document.getElementById('fullSections').textContent = String(fullSections);
    document.getElementById('snapshotCount').textContent = (
        data.snapshotCount ?? (Array.isArray(data.sn) ? data.sn.length : 0)
    );

    applyFilters();
    markPerformance('registrar:grid-dom-complete');
}

/**
 * Open course detail modal.
 */
export async function hydrateCourse(courseCode) {
    refreshCourseMaps();
    if (hydratedCourses.has(courseCode)) {
        return hydratedCourses.get(courseCode);
    }

    const summaryCourse = summaryCourses.get(courseCode);
    if (!summaryCourse) return null;

    const department = getCourseDepartment(summaryCourse, courseCode);
    const payload = await loadDepartmentPayload(
        department,
        appState.staticManifest,
        appState.staticManifestUrl,
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
