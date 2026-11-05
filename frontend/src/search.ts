import { searchCourses } from './courseSearch.mjs';
import { courseMatchesElective } from './electiveFilters.mjs';
import { clearSearchButton, departmentPanel, departmentSearch, departmentToggle, electiveFilter, searchInput } from './dom';
import { appState, summaryCourses } from './state';
import { initApp } from './app';
import { renderJumpToNavigation } from './grid';
import { BOOKMARKS_CHANGED_EVENT } from './bookmarks';

export function setDepartmentPanelOpen(expanded) {
    if (!departmentToggle || !departmentPanel) return;
    departmentToggle.setAttribute('aria-expanded', String(expanded));
    departmentPanel.hidden = !expanded;
    if (!expanded) return;

    departmentSearch.value = '';
    for (const link of document.querySelectorAll<HTMLElement>('#jumpToNav a')) link.hidden = false;
    document.getElementById('departmentEmpty').hidden = true;
    departmentSearch.focus();
}

departmentToggle?.addEventListener('click', () => {
    const expanded = departmentToggle.getAttribute('aria-expanded') !== 'true';
    setDepartmentPanelOpen(expanded);
});

departmentSearch?.addEventListener('input', () => {
    const query = departmentSearch.value.trim().toUpperCase();
    let visible = 0;
    for (const link of document.querySelectorAll<HTMLElement>('#jumpToNav a')) {
        link.hidden = query !== '' && !link.textContent.toUpperCase().includes(query);
        if (!link.hidden) visible += 1;
    }
    document.getElementById('departmentEmpty').hidden = visible > 0;
});

departmentSearch?.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        event.preventDefault();
        setDepartmentPanelOpen(false);
        departmentToggle.focus();
        return;
    }
    if (event.key === 'Enter') {
        const firstMatch = document.querySelector<HTMLElement>('#jumpToNav a:not([hidden])');
        if (firstMatch) {
            event.preventDefault();
            firstMatch.click();
        }
    }
});

// Keyboard shortcut: "/" to focus search
function isTextEntryTarget(target: EventTarget | null) {
    return target instanceof HTMLElement
        && (target.isContentEditable || target.matches('input, textarea, select'));
}

document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isTextEntryTarget(e.target) || document.activeElement === searchInput) return;
    if (document.getElementById('modalOverlay').classList.contains('active')) return;
    e.preventDefault();
    searchInput?.focus();
});

// Arrow key navigation in course grid
document.getElementById('courseGrid')?.addEventListener('keydown', (e) => {
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;

    const cells = [...document.querySelectorAll<HTMLElement>('.course-cell:not(.hidden)')];
    const idx = cells.indexOf(document.activeElement as HTMLElement);
    if (idx === -1) return;

    e.preventDefault();
    // Columns are the most cells sharing one row; a department's last row may be partial.
    const rowCounts = new Map<number, number>();
    for (const cell of cells) rowCounts.set(cell.offsetTop, (rowCounts.get(cell.offsetTop) ?? 0) + 1);
    const cols = Math.max(1, ...rowCounts.values());

    let next = idx;
    switch (e.key) {
        case 'ArrowRight': next = Math.min(idx + 1, cells.length - 1); break;
        case 'ArrowLeft': next = Math.max(idx - 1, 0); break;
        case 'ArrowDown': next = Math.min(idx + cols, cells.length - 1); break;
        case 'ArrowUp': next = Math.max(idx - cols, 0); break;
    }
    cells[next]?.focus();
});

document.querySelectorAll<HTMLElement>('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll<HTMLElement>('.filter-btn').forEach(b => {
            b.classList.remove('active');
            b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        appState.currentFilter = btn.dataset.filter;
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
    if (appState.currentFilter === 'starred' && !query) {
        div.textContent = 'No bookmarked courses yet. Bookmark a course from its details to see it here.';
    } else if (appState.currentFilter === 'starred') {
        div.textContent = `No bookmarked courses match '${query}'.`;
    } else if (query) {
        div.textContent = `No courses match '${query}'. Try a different search term.`;
    } else if (electiveFilter?.value !== 'all') {
        const label = electiveFilter.selectedOptions[0]?.textContent || 'selected';
        div.textContent = `No ${label.toLowerCase()} courses are available.`;
    } else {
        const labels = { full: 'full', near: 'near-full', open: 'open' };
        div.textContent = `No ${labels[appState.currentFilter] || appState.currentFilter} courses are available.`;
    }
    grid.appendChild(div);
}

/**
 * Remove the empty state message from the course grid.
 */
function hideEmptyState() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    const existing = grid.querySelector<HTMLElement>('.empty-state');
    if (existing) existing.remove();
}

/**
 * Show error state in the course grid with a retry button.
 */
export function showErrorState() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    // Remove any existing error or empty states
    grid.querySelectorAll<HTMLElement>('.empty-state, .error-state, .timeout-state').forEach(el => el.remove());
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
export function showTimeoutWarning() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    // Don't add duplicate warnings
    if (grid.querySelector<HTMLElement>('.timeout-state')) return;
    const div = document.createElement('div');
    div.className = 'timeout-state';
    div.textContent = 'Still loading... This is taking longer than expected.';
    grid.appendChild(div);
}

/**
 * Remove the timeout warning from the course grid.
 */
export function hideTimeoutWarning() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;
    const existing = grid.querySelector<HTMLElement>('.timeout-state');
    if (existing) existing.remove();
}

let pendingFilterFrame = 0;

/** Coalesce rapid input into at most one filter pass per animation frame. */
function scheduleFilters() {
    if (pendingFilterFrame) cancelAnimationFrame(pendingFilterFrame);
    pendingFilterFrame = requestAnimationFrame(() => {
        pendingFilterFrame = 0;
        applyFilters();
    });
}

export function applyFilters() {
    if (pendingFilterFrame) {
        cancelAnimationFrame(pendingFilterFrame);
        pendingFilterFrame = 0;
    }
    const searchQuery = searchInput?.value.toLowerCase().trim() || '';
    const electiveCategory = electiveFilter?.value || 'all';
    document.querySelectorAll<HTMLElement>('.catalog-select').forEach(field => {
        field.querySelector<HTMLElement>('.catalog-select-label').textContent = field.querySelector<HTMLSelectElement>('select').selectedOptions[0].textContent;
    });
    const results = searchQuery
        ? searchCourses([...summaryCourses].map(([code, course]) => ({ code, title: course.ti, instructors: course.instructors })), searchQuery)
        : [];
    const matches = new Map<string, any>(results.map(result => [result.code, result]));
    sortCourseGrid(searchQuery ? new Map(results.map((result, index) => [result.code, index])) : null);
    document.getElementById('courseGrid')?.classList.toggle('search-active', Boolean(searchQuery));
    const cells = document.querySelectorAll<HTMLElement>('.course-cell');

    let visibleCount = 0;
    cells.forEach(cell => {
        const code = cell.dataset.course;
        const status = cell.dataset.status;
        const isStarred = cell.classList.contains('starred');

        const match = matches.get(code);
        const matchesSearch = !searchQuery || Boolean(match);
        const reason = cell.querySelector<HTMLElement>('.search-reason');
        reason.textContent = match?.reason || '';
        reason.hidden = !searchQuery;
        reason.title = reason.textContent;
        if (match?.reason) cell.setAttribute('aria-describedby', reason.id);
        else cell.removeAttribute('aria-describedby');
        const matchesFilter = appState.currentFilter === 'all' ||
            (appState.currentFilter === 'starred' && isStarred) ||
            status === appState.currentFilter;
        const matchesElective = courseMatchesElective(cell.dataset.course, electiveCategory);

        const isVisible = matchesSearch && matchesFilter && matchesElective;
        cell.classList.toggle('hidden', !isVisible);
        if (isVisible) visibleCount++;
    });

    const announcement = document.getElementById('searchAnnouncement');
    if (announcement) {
        announcement.textContent = searchQuery
            ? `${visibleCount} ${visibleCount === 1 ? 'result' : 'results'} · Sorted by relevance`
            : `Showing ${visibleCount} courses`;
        announcement.classList.toggle('visually-hidden', !searchQuery);
    }

    // Update department headers
    document.querySelectorAll<HTMLElement>('.dept-header').forEach(header => {
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

    const sortBy = (document.getElementById('sortSelect') as HTMLSelectElement)?.value;
    const visibleDepartments = [...new Set([...cells]
        .filter(cell => !cell.classList.contains('hidden'))
        .map(cell => cell.dataset.course.split(' ')[0]))]
        .sort();
    renderJumpToNavigation(!searchQuery && sortBy === 'department' ? visibleDepartments : []);

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

searchInput?.addEventListener('input', (event) => {
    updateSearchControls();
    // Wait for the IME to commit; compositionend applies the final text.
    if ((event as InputEvent).isComposing) return;
    scheduleFilters();
});

searchInput?.addEventListener('compositionend', () => {
    updateSearchControls();
    scheduleFilters();
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

electiveFilter?.addEventListener('change', applyFilters);

// Unbookmarking while the starred filter is active must hide the course.
document.addEventListener(BOOKMARKS_CHANGED_EVENT, () => {
    if (appState.currentFilter === 'starred') applyFilters();
});

function sortCourseGrid(searchRanks) {
    const sortBy = searchRanks ? 'relevance' : (document.getElementById('sortSelect') as HTMLSelectElement)?.value;
    const grid = document.getElementById('courseGrid');
    const cells = [...grid.querySelectorAll<HTMLElement>('.course-cell')];
    const headers = [...grid.querySelectorAll<HTMLElement>('.dept-header')];
    // Current cell/header order, with headers identified by their id.
    const currentOrder = [...grid.children]
        .filter(el => el.classList.contains('course-cell') || el.classList.contains('dept-header'))
        .map(el => (el.classList.contains('dept-header') ? el.id : el));

    // Sort cells
    cells.sort((a, b) => {
        const codeA = a.dataset.course;
        const codeB = b.dataset.course;
        const fillA = parseFloat(a.dataset.fill);
        const fillB = parseFloat(b.dataset.fill);
        const deptA = codeA.split(' ')[0];
        const deptB = codeB.split(' ')[0];

        switch (sortBy) {
            case 'relevance': return (searchRanks.get(codeA) ?? Infinity) - (searchRanks.get(codeB) ?? Infinity);
            case 'code': return codeA.localeCompare(codeB);
            case 'fill-desc': return fillB - fillA || codeA.localeCompare(codeB);
            case 'fill-asc': return fillA - fillB || codeA.localeCompare(codeB);
            default: return deptA.localeCompare(deptB) || codeA.localeCompare(codeB);
        }
    });

    // Skip the DOM churn (and focus loss) when nothing would move.
    const nextOrder: (string | Element)[] = [];
    let nextDept = '';
    for (const cell of cells) {
        const dept = cell.dataset.course.split(' ')[0];
        if (sortBy === 'department' && dept !== nextDept) {
            nextDept = dept;
            nextOrder.push(`dept-${dept}`);
        }
        nextOrder.push(cell);
    }
    if (nextOrder.length === currentOrder.length && nextOrder.every((item, i) => item === currentOrder[i])) {
        if (sortBy !== 'department') document.getElementById('jumpToNav').textContent = '';
        return;
    }

    // Remove all items
    cells.forEach(c => c.remove());
    headers.forEach(h => h.remove());

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
}

document.getElementById('sortSelect')?.addEventListener('change', applyFilters);
