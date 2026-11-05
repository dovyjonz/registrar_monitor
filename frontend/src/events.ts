import { appState, getHydratedCourse } from './state';

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

function syncSortButton(button: HTMLElement) {
    const newest = appState._eventSortOrder === 'newest';
    button.dataset.sort = appState._eventSortOrder;
    button.textContent = newest ? '↓ Newest' : '↑ Oldest';
    button.setAttribute(
        'aria-label',
        `Sort order: ${newest ? 'newest' : 'oldest'} first. Activate to show ${newest ? 'oldest' : 'newest'} first.`,
    );
}

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
export function renderEventHistory(courseCode, course = getHydratedCourse(courseCode)) {
    const container = document.getElementById('eventHistoryList');
    const countEl = document.getElementById('eventCount');
    const details = document.getElementById('eventHistory');
    if (!container || !details) return;

    appState._currentEvents = course?.ev || [];
    appState._eventFilterType = 'all';
    appState._eventFilterSection = 'all';
    // The rebuilt controls start at "Newest"; keep state in step with them.
    appState._eventSortOrder = 'newest';

    if (appState._currentEvents.length === 0) {
        details.style.display = 'none';
        return;
    }
    details.style.display = '';
    if (countEl) countEl.textContent = `(${appState._currentEvents.length})`;

    // Build filter controls
    const sections = [...new Set<string>(appState._currentEvents.filter(e => e.sc).map(e => e.sc))].sort();
    const types = [...new Set<string>(appState._currentEvents.map(e => e.et))];

    const filtersEl = document.getElementById('eventFilters');
    if (filtersEl) {
        filtersEl.textContent = '';
        const efRow = document.createElement('div');
        efRow.className = 'ef-row';

        const sortBtn = document.createElement('button');
        sortBtn.className = 'ef-pill ef-sort';
        sortBtn.type = 'button';
        sortBtn.title = 'Sort order';
        syncSortButton(sortBtn);
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
        filtersEl.querySelector<HTMLElement>('.ef-sort')?.addEventListener('click', (e) => {
            appState._eventSortOrder = appState._eventSortOrder === 'newest' ? 'oldest' : 'newest';
            syncSortButton(e.currentTarget as HTMLElement);
            _renderFilteredEvents();
        });
        filtersEl.querySelector<HTMLElement>('#efType')?.addEventListener('change', (e) => {
            appState._eventFilterType = (e.target as HTMLSelectElement).value;
            _renderFilteredEvents();
        });
        filtersEl.querySelector<HTMLElement>('#efSection')?.addEventListener('change', (e) => {
            appState._eventFilterSection = (e.target as HTMLSelectElement).value;
            _renderFilteredEvents();
        });
    }

    _renderFilteredEvents();
}

function _renderFilteredEvents() {
    const container = document.getElementById('eventHistoryList');
    const countEl = document.getElementById('eventCount');
    if (!container) return;

    let filtered = appState._currentEvents;
    if (appState._eventFilterType !== 'all') filtered = filtered.filter(e => e.et === appState._eventFilterType);
    if (appState._eventFilterSection !== 'all') filtered = filtered.filter(e => e.sc === appState._eventFilterSection);

    // Sort
    filtered = [...filtered].sort((a, b) => {
        const ta = a.st ? new Date(a.st).getTime() : 0;
        const tb = b.st ? new Date(b.st).getTime() : 0;
        return appState._eventSortOrder === 'newest' ? tb - ta : ta - tb;
    });

    if (countEl) countEl.textContent = `(${filtered.length}/${appState._currentEvents.length})`;

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
