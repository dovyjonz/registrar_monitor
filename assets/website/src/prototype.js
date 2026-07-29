/**
 * Local dashboard redesign prototype.
 */

import './prototype.css';
import {
    buildAverageChartPoints,
    buildCourseChartDomain,
    getChartMapper,
    getXScaleBounds,
} from './chartMapping.mjs';

let chartJsLoaded = false;
let Chart = null;
let chart = null;

async function loadChartJs() {
    if (chartJsLoaded) return;
    const chartModule = await import('chart.js/auto');
    Chart = chartModule.default;
    chartJsLoaded = true;
}

const app = document.getElementById('prototypeApp');
const favorites = new Set(JSON.parse(localStorage.getItem('prototypeFavorites') || '[]'));
const detailCache = new Map();

const state = {
    indexUrl: document.body?.dataset?.prototypeIndex || 'prototype-data/index.json',
    payload: null,
    rows: [],
    selectedCode: null,
    activeTab: 'overview',
    rangeDays: 7,
    search: '',
    status: 'all',
    favoritesOnly: false,
    sort: 'department',
};

function saveFavorites() {
    localStorage.setItem('prototypeFavorites', JSON.stringify([...favorites]));
}

function formatNumber(value) {
    return new Intl.NumberFormat('en-US').format(value || 0);
}

function formatPercent(fill) {
    return `${Math.round((fill || 0) * 100)}%`;
}

function formatDateTime(value) {
    if (!value) return 'N/A';
    return new Date(value).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function h(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function statusLabel(status) {
    if (status === 'full') return 'Full';
    if (status === 'near') return 'Near Full';
    return 'Open';
}

function getSelectedRow() {
    return state.rows.find(row => row.code === state.selectedCode) || state.rows[0] || null;
}

function getFilteredRows() {
    const query = state.search.trim().toLowerCase();
    return state.rows
        .filter(row => {
            const matchesSearch = !query ||
                row.code.toLowerCase().includes(query) ||
                (row.title || '').toLowerCase().includes(query) ||
                (row.department || '').toLowerCase().includes(query);
            const matchesStatus = state.status === 'all' || row.status === state.status;
            const matchesFavorite = !state.favoritesOnly || favorites.has(row.code);
            return matchesSearch && matchesStatus && matchesFavorite;
        })
        .sort((a, b) => {
            if (state.sort === 'fill-desc') return b.fill - a.fill || a.code.localeCompare(b.code);
            if (state.sort === 'fill-asc') return a.fill - b.fill || a.code.localeCompare(b.code);
            if (state.sort === 'updated') return (b.lastActivityAt || '').localeCompare(a.lastActivityAt || '');
            return a.department.localeCompare(b.department) || a.code.localeCompare(b.code);
        });
}

async function loadIndex(indexUrl) {
    const response = await fetch(indexUrl);
    if (!response.ok) throw new Error(`Failed to load prototype data: ${response.status}`);
    const payload = await response.json();
    state.indexUrl = indexUrl;
    state.payload = payload;
    state.rows = payload.courseRows || [];
    state.selectedCode = state.rows[0]?.code || null;
    state.activeTab = 'overview';
    detailCache.clear();
    renderApp();
    if (state.selectedCode) await selectCourse(state.selectedCode);
}

function renderMetricCards(summary) {
    const cards = [
        ['Courses', summary.courses, 'primary'],
        ['Sections', summary.sections, 'purple'],
        ['Full', summary.fullSections, 'danger'],
        ['Near Full', summary.nearFullSections, 'warning'],
        ['Snapshots', summary.snapshots, 'teal'],
    ];
    return cards.map(([label, value, tone]) => `
        <article class="prototype-stat ${tone}">
            <span class="prototype-stat-icon" aria-hidden="true"></span>
            <strong>${formatNumber(value)}</strong>
            <span>${label}</span>
        </article>
    `).join('');
}

function renderSemesterOptions() {
    const options = state.payload?.semesters || [];
    if (options.length === 0) {
        return `<option>${h(state.payload?.semester || '')}</option>`;
    }
    return options.map(option => `
        <option value="${h(option.indexUrl)}" ${option.semester === state.payload.semester ? 'selected' : ''}>
            ${h(option.semester)}${option.semester === state.payload.semester ? ' (Current)' : ''}
        </option>
    `).join('');
}

function renderRows() {
    const rows = getFilteredRows();
    const selectedCode = state.selectedCode;
    const table = document.getElementById('prototypeRows');
    const count = document.getElementById('prototypeRowCount');
    if (!table) return;

    if (count) count.textContent = `Showing ${rows.length} of ${state.rows.length} courses`;
    if (rows.length === 0) {
        table.innerHTML = '<div class="prototype-empty">No courses match the current filters.</div>';
        return;
    }

    table.innerHTML = rows.map(row => `
        <div class="prototype-row ${row.code === selectedCode ? 'selected' : ''}" data-course="${h(row.code)}" role="button" tabindex="0">
            <span class="course-main">
                <strong>${h(row.code)}</strong>
                <small>${h(row.title || 'Untitled course')}</small>
            </span>
            <span>${row.enrollmentTotal} / ${row.capacityTotal}</span>
            <span class="fill-cell">
                <span>${formatPercent(row.fill)}</span>
                <i style="--fill:${Math.min(100, Math.round(row.fill * 100))}%"></i>
            </span>
            <span><mark class="status-pill ${row.status}">${statusLabel(row.status)}</mark></span>
            <span class="updated-cell">${formatDateTime(row.lastActivityAt)}</span>
            <span>
                <button class="row-favorite ${favorites.has(row.code) ? 'active' : ''}" data-favorite="${h(row.code)}" type="button" aria-label="Toggle favorite for ${h(row.code)}">
                    ${favorites.has(row.code) ? '*' : '+'}
                </button>
            </span>
        </div>
    `).join('');
}

function renderShell() {
    const summary = state.payload.summary;
    app.innerHTML = `
        <aside class="prototype-sidebar" aria-label="Prototype navigation">
            <div class="prototype-brand-mark" aria-hidden="true">NU</div>
            <nav>
                <a class="active" href="#prototype-main">Dashboard</a>
                <a href="#prototype-main">Courses</a>
                <a href="#prototype-main">Snapshots</a>
                <a href="#prototype-main">Reports</a>
                <a href="#prototype-main">Alerts <span>${summary.fullSections}</span></a>
                <a href="#prototype-main">Favorites</a>
                <a href="#prototype-main">Settings</a>
            </nav>
            <section class="prototype-system-card" aria-label="System status">
                <strong>System Status</strong>
                <span class="live-dot">All Systems Operational</span>
                <dl>
                    <div><dt>Data Feeds</dt><dd>Live</dd></div>
                    <div><dt>Snapshots</dt><dd>${formatNumber(summary.snapshots)}</dd></div>
                    <div><dt>Fill</dt><dd>${formatPercent(summary.overallFill)}</dd></div>
                </dl>
            </section>
        </aside>
        <div class="prototype-shell">
            <header class="prototype-topbar">
                <div class="prototype-title-block">
                    <div class="prototype-logo" aria-hidden="true">NU</div>
                    <div>
                        <h1>Course Registration Monitor</h1>
                        <p>Live enrollment tracking across campus</p>
                    </div>
                </div>
                <div class="prototype-term-controls">
                    <select id="prototypeSemester" aria-label="Semester">${renderSemesterOptions()}</select>
                    <button type="button">Past Terms</button>
                </div>
                <div class="prototype-live">
                    <span class="live-dot">Live</span>
                    <small>Updated ${formatDateTime(summary.lastReportTime)}</small>
                </div>
                <div class="prototype-user" aria-label="Prototype user controls">
                    <button type="button" aria-label="Notifications">12</button>
                    <span class="prototype-avatar" aria-hidden="true">AM</span>
                </div>
            </header>

            <main id="prototype-main" class="prototype-main">
                <section class="prototype-content" aria-label="Courses">
                    <div class="prototype-stats">${renderMetricCards(summary)}</div>
                    <div class="prototype-toolbar">
                        <label class="prototype-search">
                            <span aria-hidden="true"></span>
                            <input id="prototypeSearch" type="search" placeholder="Search courses, titles, or departments..." value="${h(state.search)}" aria-label="Search courses">
                        </label>
                        <select id="prototypeStatus" aria-label="Filter by status">
                            <option value="all" ${state.status === 'all' ? 'selected' : ''}>All Statuses</option>
                            <option value="full" ${state.status === 'full' ? 'selected' : ''}>Full</option>
                            <option value="near" ${state.status === 'near' ? 'selected' : ''}>Near Full</option>
                            <option value="open" ${state.status === 'open' ? 'selected' : ''}>Open</option>
                        </select>
                        <button id="prototypeFavoritesOnly" class="${state.favoritesOnly ? 'active' : ''}" type="button">Favorites</button>
                        <select id="prototypeSort" aria-label="Sort courses">
                            <option value="department" ${state.sort === 'department' ? 'selected' : ''}>By Department</option>
                            <option value="fill-desc" ${state.sort === 'fill-desc' ? 'selected' : ''}>Fill High to Low</option>
                            <option value="fill-asc" ${state.sort === 'fill-asc' ? 'selected' : ''}>Fill Low to High</option>
                            <option value="updated" ${state.sort === 'updated' ? 'selected' : ''}>Recently Updated</option>
                        </select>
                    </div>
                    <div class="prototype-table" aria-label="Course list">
                        <div class="prototype-table-head" aria-hidden="true">
                            <span>Course</span>
                            <span>Enrolled / Capacity</span>
                            <span>Fill %</span>
                            <span>Status</span>
                            <span>Last Updated</span>
                            <span></span>
                        </div>
                        <div id="prototypeRows" class="prototype-rows"></div>
                        <div class="prototype-table-footer">
                            <span id="prototypeRowCount"></span>
                        </div>
                    </div>
                </section>
                <aside id="prototypeInspector" class="prototype-inspector" aria-label="Course details"></aside>
            </main>
        </div>
        <nav class="prototype-bottom-nav" aria-label="Mobile navigation">
            <a class="active" href="#prototype-main">Dashboard</a>
            <a href="#prototype-main">Courses</a>
            <a href="#prototype-main">Snapshots</a>
            <a href="#prototype-main">Alerts</a>
            <a href="#prototype-main">Favorites</a>
        </nav>
    `;

    attachShellEvents();
    renderRows();
    renderInspectorLoading(getSelectedRow());
}

function renderApp() {
    if (!state.payload) return;
    renderShell();
}

function attachShellEvents() {
    document.getElementById('prototypeSemester')?.addEventListener('change', async event => {
        await loadIndex(event.target.value);
    });
    document.getElementById('prototypeSearch')?.addEventListener('input', event => {
        state.search = event.target.value;
        renderRows();
    });
    document.getElementById('prototypeStatus')?.addEventListener('change', event => {
        state.status = event.target.value;
        renderRows();
    });
    document.getElementById('prototypeSort')?.addEventListener('change', event => {
        state.sort = event.target.value;
        renderRows();
    });
    document.getElementById('prototypeFavoritesOnly')?.addEventListener('click', event => {
        state.favoritesOnly = !state.favoritesOnly;
        event.currentTarget.classList.toggle('active', state.favoritesOnly);
        renderRows();
    });
    document.getElementById('prototypeRows')?.addEventListener('click', async event => {
        const favoriteButton = event.target.closest('[data-favorite]');
        if (favoriteButton) {
            const code = favoriteButton.dataset.favorite;
            if (favorites.has(code)) favorites.delete(code);
            else favorites.add(code);
            saveFavorites();
            renderRows();
            if (state.selectedCode === code) await renderSelectedInspector();
            return;
        }

        const row = event.target.closest('[data-course]');
        if (row) await selectCourse(row.dataset.course);
    });
    document.getElementById('prototypeRows')?.addEventListener('keydown', async event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('[data-favorite]')) return;
        const row = event.target.closest('[data-course]');
        if (!row) return;
        event.preventDefault();
        await selectCourse(row.dataset.course);
    });
}

async function selectCourse(code) {
    state.selectedCode = code;
    renderRows();
    renderInspectorLoading(getSelectedRow());
    await renderSelectedInspector();
}

function renderInspectorLoading(row) {
    const inspector = document.getElementById('prototypeInspector');
    if (!inspector || !row) return;
    inspector.innerHTML = `
        <section class="inspector-hero">
            <button class="inspector-close" type="button" aria-label="Close details">x</button>
            <h2>${h(row.code)}</h2>
            <p>${h(row.title || 'Untitled course')}</p>
            <mark class="status-pill ${row.status}">${statusLabel(row.status)}</mark>
        </section>
        <div class="inspector-loading">Loading course detail...</div>
    `;
}

async function getCourseDetail(row) {
    if (detailCache.has(row.code)) return detailCache.get(row.code);
    const response = await fetch(row.detailUrl);
    if (!response.ok) throw new Error(`Failed to load ${row.code}`);
    const detail = await response.json();
    detailCache.set(row.code, detail);
    return detail;
}

async function renderSelectedInspector() {
    const row = getSelectedRow();
    if (!row) return;
    const requestedCode = row.code;
    try {
        const detail = await getCourseDetail(row);
        if (state.selectedCode !== requestedCode) return;
        renderInspector(detail);
        await renderPrototypeChart(detail);
    } catch (error) {
        if (state.selectedCode !== requestedCode) return;
        const inspector = document.getElementById('prototypeInspector');
        if (inspector) {
            inspector.innerHTML = `<div class="prototype-empty">${h(error.message)}</div>`;
        }
    }
}

function renderInspector(detail) {
    const row = detail.course;
    const inspector = document.getElementById('prototypeInspector');
    if (!inspector) return;
    const tabs = [
        ['overview', 'Overview'],
        ['snapshots', 'Snapshots'],
        ['sections', `Sections (${row.sectionCount})`],
        ['details', 'Details'],
    ];

    inspector.innerHTML = `
        <section class="inspector-hero">
            <button class="inspector-close" type="button" aria-label="Close details">x</button>
            <div>
                <h2>${h(row.code)}</h2>
                <button class="inspector-favorite ${favorites.has(row.code) ? 'active' : ''}" type="button" aria-label="Toggle favorite">${favorites.has(row.code) ? '*' : '+'}</button>
            </div>
            <p>${h(row.title || 'Untitled course')}</p>
            <dl class="inspector-meta">
                <div><dt>Enrolled</dt><dd>${row.enrollmentTotal}</dd></div>
                <div><dt>Capacity</dt><dd>${row.capacityTotal}</dd></div>
                <div><dt>Fill %</dt><dd>${formatPercent(row.fill)}</dd></div>
                <div><dt>Status</dt><dd><mark class="status-pill ${row.status}">${statusLabel(row.status)}</mark></dd></div>
            </dl>
        </section>
        <nav class="inspector-tabs" aria-label="Course detail tabs">
            ${tabs.map(([id, label]) => `<button class="${state.activeTab === id ? 'active' : ''}" data-tab="${id}" type="button">${label}</button>`).join('')}
        </nav>
        <section id="inspectorPanel" class="inspector-panel"></section>
    `;

    inspector.querySelector('.inspector-close')?.addEventListener('click', () => {
        inspector.classList.remove('open');
    });
    inspector.querySelector('.inspector-favorite')?.addEventListener('click', async () => {
        if (favorites.has(row.code)) favorites.delete(row.code);
        else favorites.add(row.code);
        saveFavorites();
        renderRows();
        renderInspector(detail);
        await renderPrototypeChart(detail);
    });
    inspector.querySelector('.inspector-tabs')?.addEventListener('click', async event => {
        const tab = event.target.closest('[data-tab]');
        if (!tab) return;
        state.activeTab = tab.dataset.tab;
        renderInspector(detail);
        if (state.activeTab === 'overview') await renderPrototypeChart(detail);
    });

    inspector.classList.add('open');
    renderInspectorPanel(detail);
}

function renderInspectorPanel(detail) {
    const panel = document.getElementById('inspectorPanel');
    const row = detail.course;
    if (!panel) return;

    if (state.activeTab === 'sections') {
        panel.innerHTML = `
            <div class="section-list-prototype">
                ${row.sections.map(section => `
                    <article>
                        <strong>${h(section.code)}</strong>
                        <span>${h(section.type || 'Section')}</span>
                        <span>${h(section.instructor || 'Instructor TBA')}</span>
                        <b>${section.enrollment} / ${section.capacity}</b>
                    </article>
                `).join('')}
            </div>
        `;
        return;
    }

    if (state.activeTab === 'snapshots') {
        panel.innerHTML = `
            <div class="snapshot-panel">
                <strong>${formatNumber(detail.snapshots.length)}</strong>
                <span>course chart points retained for this lazy detail payload</span>
            </div>
        `;
        return;
    }

    if (state.activeTab === 'details') {
        panel.innerHTML = `
            <dl class="detail-grid">
                <div><dt>Department</dt><dd>${h(row.department)}</dd></div>
                <div><dt>Course Code</dt><dd>${h(row.code)}</dd></div>
                <div><dt>Last Activity</dt><dd>${formatDateTime(row.lastActivityAt)}</dd></div>
                <div><dt>Modeled Waitlist</dt><dd>Not available</dd></div>
            </dl>
        `;
        return;
    }

    panel.innerHTML = `
        <div class="chart-card">
            <div class="chart-card-header">
                <strong>Enrollment Over Time</strong>
                <div class="range-toggle" role="group" aria-label="Chart range">
                    ${[7, 30, 90].map(days => `<button class="${state.rangeDays === days ? 'active' : ''}" data-range="${days}" type="button">${days}D</button>`).join('')}
                </div>
            </div>
            <div class="prototype-chart-wrap">
                <canvas id="prototypeChart" aria-label="Enrollment over time chart" role="img"></canvas>
            </div>
        </div>
        <div class="events-card">
            <div><strong>Recent Events</strong><a href="#prototype-main">View All</a></div>
            ${renderEventList(row.events)}
        </div>
    `;
    panel.querySelector('.range-toggle')?.addEventListener('click', async event => {
        const button = event.target.closest('[data-range]');
        if (!button) return;
        state.rangeDays = Number(button.dataset.range);
        renderInspectorPanel(detail);
        await renderPrototypeChart(detail);
    });
}

function renderEventList(events) {
    const visible = [...(events || [])]
        .sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''))
        .slice(0, 5);
    if (visible.length === 0) {
        return '<p class="prototype-muted">No structural events recorded for this course.</p>';
    }
    return `<ol class="event-list-prototype">
        ${visible.map(event => `
            <li>
                <i class="${event.type}"></i>
                <span><b>${formatDateTime(event.timestamp)}</b>${h(event.label)}</span>
                <em>${h(event.description)}</em>
            </li>
        `).join('')}
    </ol>`;
}

function toChartCourse(course) {
    const rawSections = course.rawSections || {};
    return {
        ah: (course.averageHistory || []).map(point => ({
            i: point.snapshotIdx,
            f: point.fill,
        })),
        s: Object.fromEntries(Object.entries(rawSections).map(([code, section]) => [
            code,
            {
                h: (section.history || []).map(point => ({
                    i: point.snapshotIdx,
                    f: point.fill,
                    e: point.enrollment,
                    c: point.capacity,
                })),
            },
        ])),
    };
}

function filterChartPoints(points) {
    if (!points.length) return points;
    const max = Math.max(...points.map(point => point.timestamp));
    const min = max - state.rangeDays * 24 * 60 * 60 * 1000;
    const filtered = points.filter(point => point.timestamp >= min);
    return filtered.length >= 2 ? filtered : points;
}

async function renderPrototypeChart(detail) {
    const canvas = document.getElementById('prototypeChart');
    if (!canvas) return;
    await loadChartJs();
    if (chart) {
        chart.destroy();
        chart = null;
    }

    const snapshots = (detail.snapshots || []).map(snapshot => ({ ts: snapshot.timestamp }));
    const chartCourse = toChartCourse(detail.course);
    const allPoints = buildAverageChartPoints(chartCourse, snapshots);
    const points = filterChartPoints(allPoints);
    const domain = buildCourseChartDomain(chartCourse, snapshots);
    const mapper = getChartMapper('timeline', points, domain, []);
    const bounds = getXScaleBounds(mapper.xValues);

    chart = new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                label: detail.course.code,
                data: points.map((point, index) => ({ x: mapper.xValues[index], y: point.fill })),
                borderColor: '#ff2d32',
                backgroundColor: 'rgba(255, 45, 50, 0.10)',
                fill: true,
                tension: 0.3,
                pointRadius: points.length > 45 ? 0 : 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#ff2d32',
                pointBorderColor: '#ffffff',
                pointBorderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#ffffff',
                    titleColor: '#0b1838',
                    bodyColor: '#0b1838',
                    borderColor: '#dce5f2',
                    borderWidth: 1,
                    callbacks: {
                        title: items => points[items[0]?.dataIndex]?.label || '',
                        label: context => `Fill: ${context.parsed.y}%`,
                    },
                },
            },
            scales: {
                x: {
                    type: 'linear',
                    display: false,
                    min: bounds.min,
                    max: bounds.max,
                    grid: { display: false },
                    border: { display: false },
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: { color: '#5d6b85', stepSize: 25 },
                    grid: { color: '#ecf1f7' },
                    border: { display: false },
                },
            },
            interaction: { intersect: false, mode: 'index' },
        },
    });
}

async function init() {
    try {
        await loadIndex(state.indexUrl);
    } catch (error) {
        app.innerHTML = `<div class="prototype-error">${error.message}</div>`;
    }
}

init();
