import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildAverageChartPoints,
    buildProfessorAverageChartPoints,
    buildSectionActivityTimeline,
    courseHasProfessor,
    createHistoricalCoordinateMapper,
    getHistoricalMilestoneAlignment,
    getChartMapper,
    getInstructorAtSnapshot,
    normalizeHistoricalDomain,
    normalizeInstructorName,
} from '../src/chartMapping.mjs';

test('normalizes professor names conservatively', () => {
    assert.equal(normalizeInstructorName('  Jane  A.  Smith  '), 'jane a. smith');
    assert.equal(normalizeInstructorName('Ｊａｎｅ　Ｓｍｉｔｈ'), 'jane smith');
    assert.equal(normalizeInstructorName('TBA TBA'), '');
    assert.equal(normalizeInstructorName('TBA1 TBA1'), '');
    assert.equal(normalizeInstructorName('Jane A. Smith'), 'jane a. smith');
    assert.notEqual(
        normalizeInstructorName('Jane A. Smith'),
        normalizeInstructorName('Jane Smith'),
    );
});

const professorSnapshots = [
    { ts: '2026-01-01T10:00:00Z' },
    { ts: '2026-01-02T10:00:00Z' },
    { ts: '2026-01-03T10:00:00Z' },
    { ts: '2026-01-04T10:00:00Z' },
];

test('section activity starts at first history and stops at removal', () => {
    const timeline = buildSectionActivityTimeline(
        'B',
        { h: [{ i: 1, f: 0.25 }, { i: 2, f: 0.5 }] },
        [{ et: 'section_added', sc: 'B', i: 1 }, { et: 'section_removed', sc: 'B', i: 3 }],
        professorSnapshots,
    );

    assert.deepEqual(timeline.intervals, [{ start: 1, end: 3 }]);
    assert.equal(timeline.isActiveAt(0), false);
    assert.equal(timeline.isActiveAt(1), true);
    assert.equal(timeline.isActiveAt(2), true);
    assert.equal(timeline.isActiveAt(3), false);
});

test('section removal at its event snapshot is treated as an exclusive end', () => {
    const timeline = buildSectionActivityTimeline(
        'B',
        { h: [{ i: 1, f: 0.25 }] },
        [{ et: 'section_added', sc: 'B', i: 1 }, { et: 'section_removed', sc: 'B', i: 1 }],
        professorSnapshots,
    );

    assert.deepEqual(timeline.intervals, [{ start: 1, end: 1 }]);
    assert.equal(timeline.isActiveAt(1), false);
});

test('section re-additions retain the initial active interval', () => {
    const readdSnapshots = [
        ...professorSnapshots,
        { ts: '2026-01-05T10:00:00Z' },
        { ts: '2026-01-06T10:00:00Z' },
    ];
    const timeline = buildSectionActivityTimeline(
        'B',
        { h: [{ i: 0, f: 0.25 }, { i: 4, f: 0.5 }] },
        [
            { et: 'section_removed', sc: 'B', i: 2 },
            { et: 'section_added', sc: 'B', i: 4 },
        ],
        readdSnapshots,
    );

    assert.deepEqual(timeline.intervals, [
        { start: 0, end: 2 },
        { start: 4, end: 6 },
    ]);
    assert.equal(timeline.isActiveAt(0), true);
    assert.equal(timeline.isActiveAt(2), false);
    assert.equal(timeline.isActiveAt(4), true);
});

test('placeholder instructor transitions clear stale historical assignments', () => {
    const events = [
        { et: 'instructor_changed', sc: 'B', ov: 'Alex', nv: 'Jane Smith', i: 1 },
        { et: 'instructor_changed', sc: 'B', ov: 'Jane Smith', nv: 'TBA', i: 2 },
    ];

    assert.equal(
        getInstructorAtSnapshot('B', { in: 'TBA' }, events, 1),
        'jane smith',
    );
    assert.equal(getInstructorAtSnapshot('B', { in: 'TBA' }, events, 2), '');
    assert.equal(getInstructorAtSnapshot('B', { in: 'TBA' }, events, 3), '');
});

test('professor averages use equal section weighting and reconstructed instructors', () => {
    const course = {
        s: {
            A: {
                in: 'Alex',
                h: [
                    { i: 0, f: 0.4, e: 40, c: 100 },
                    { i: 1, f: 0.5, e: 50, c: 100 },
                    { i: 2, f: 0.6, e: 60, c: 100 },
                ],
            },
            B: {
                in: 'Jane Smith',
                h: [{ i: 0, f: 0.8, e: 8, c: 10 }, { i: 2, f: 0.9, e: 9, c: 10 }],
            },
            C: {
                in: 'Jane Smith',
                h: [{ i: 1, f: 0.2, e: 4, c: 20 }, { i: 3, f: 0.4, e: 8, c: 20 }],
            },
        },
        ev: [
            {
                et: 'instructor_changed',
                sc: 'A',
                ov: 'Jane Smith',
                nv: 'Alex',
                i: 2,
            },
            { et: 'section_added', sc: 'C', i: 1 },
        ],
    };

    const points = buildProfessorAverageChartPoints(
        course,
        ' Jane   Smith ',
        professorSnapshots,
    );

    assert.deepEqual(points.map(point => point.snapshotIdx), [0, 1, 2, 3]);
    assert.deepEqual(points.map(point => point.contributingSections), [2, 3, 2, 2]);
    assert.deepEqual(points.map(point => point.fill), [60, 50, 55, 65]);
    assert.equal(points[2].fillRatio, 0.55);
    assert.equal(points[2].enrollment, null);
    assert.equal(points[2].capacity, null);
});

test('late-opened and sparse sections join only after their first usable observation', () => {
    const course = {
        s: {
            Early: {
                in: 'Jane Smith',
                h: [{ i: 0, f: 0.2 }, { i: 2, f: 0.4 }],
            },
            Late: {
                in: 'Jane Smith',
                h: [{ i: 3, f: 0.8 }],
            },
        },
        ev: [{ et: 'section_added', sc: 'Late', i: 3 }],
    };

    const points = buildProfessorAverageChartPoints(
        course,
        'Jane Smith',
        professorSnapshots,
    );

    assert.deepEqual(points.map(point => point.snapshotIdx), [0, 2, 3]);
    assert.deepEqual(points.map(point => point.contributingSections), [1, 1, 2]);
    assert.deepEqual(points.map(point => point.fill), [20, 40, 60]);
});

test('unknown values never match a professor and historical domains handle degenerate input', () => {
    const course = {
        s: {
            A: { in: 'TBA', h: [{ i: 0, f: 0.5 }] },
        },
    };
    assert.equal(courseHasProfessor(course, 'Jane Smith', professorSnapshots), false);
    assert.deepEqual(normalizeHistoricalDomain([4, 5], [4], [10, 20]), [10, 10]);
    assert.deepEqual(normalizeHistoricalDomain([5], [4, 8], [10, 20]), [10]);
    assert.deepEqual(normalizeHistoricalDomain([4, 5], [], [10, 20]), []);
    assert.deepEqual(normalizeHistoricalDomain([4, 5], [4, 5], [10]), [10, 10]);
});

test('course shadows reuse the regular average-history calculation', () => {
    const points = buildAverageChartPoints(
        {
            ah: [{ i: 0, f: 0.25 }, { i: 2, f: 0.75 }],
            s: {
                A: { h: [{ i: 1, f: 0.4, c: 10 }, { i: 2, f: 0.8, c: 20 }] },
                B: { h: [{ i: 1, f: 0.2, c: 10 }, { i: 2, f: 0.7, c: 10 }] },
            },
        },
        professorSnapshots,
    );

    assert.deepEqual(points.map(point => point.fill), [25, 75]);
    assert.equal(points.at(-1).capacityChanged, true);
});

test('historical domains align phased, snapshots, and timeline mappings', () => {
    const currentPoints = [
        { snapshotIdx: 0, timestamp: new Date('2026-01-01T00:00:00Z').getTime() },
        { snapshotIdx: 1, timestamp: new Date('2026-01-03T00:00:00Z').getTime() },
    ];
    const historicalPoints = [
        { snapshotIdx: 0, timestamp: new Date('2025-06-01T00:00:00Z').getTime() },
        { snapshotIdx: 1, timestamp: new Date('2025-06-08T00:00:00Z').getTime() },
    ];
    const currentDomain = currentPoints.map(point => ({ ...point, label: '' }));
    const historicalDomain = historicalPoints.map(point => ({ ...point, label: '' }));
    const currentMilestones = [
        { time: '2026-01-01T00:00:00Z', label: 'Open' },
        { time: '2026-01-03T00:00:00Z', label: 'Close' },
    ];
    const historicalMilestones = [
        { time: '2025-06-01T00:00:00Z', label: 'Open' },
        { time: '2025-06-08T00:00:00Z', label: 'Close' },
    ];

    for (const mode of ['phased', 'snapshots', 'timeline']) {
        const current = getChartMapper(
            mode,
            currentPoints,
            currentDomain,
            currentMilestones,
        );
        const historical = getChartMapper(
            mode,
            historicalPoints,
            historicalDomain,
            historicalMilestones,
        );
        const normalized = normalizeHistoricalDomain(
            historical.xValues,
            historical.domainXValues,
            current.domainXValues,
        );
        assert.equal(normalized[0], Math.min(...current.domainXValues));
        assert.equal(normalized.at(-1), Math.max(...current.domainXValues));
    }
});

test('historical milestone alignment tolerates old semesters without deadlines', () => {
    const currentMilestones = [
        { time: '2026-08-05T09:00:00Z', label: 'Y4+' },
        { time: '2026-08-05T11:00:00Z', label: 'Y3' },
        { time: '2026-08-05T13:00:00Z', label: 'Y2' },
        { time: '2026-08-15T09:00:00Z', label: 'ALL' },
        { time: '2026-08-26T12:00:00Z', label: 'Drop' },
        { time: '2026-08-28T17:30:00Z', label: 'Close' },
    ];
    const historicalMilestones = currentMilestones.slice(0, 4).map((milestone, index) => ({
        ...milestone,
        time: `2025-08-${String(5 + index).padStart(2, '0')}T09:00:00Z`,
    }));
    const alignment = getHistoricalMilestoneAlignment({
        historicalMilestones,
        currentMilestones,
        historicalMapTime: time => time,
        currentMapTime: time => time,
    });

    assert.deepEqual(
        alignment.map(pair => [pair.historical.label, pair.current.label]),
        [['Y4+', 'Y4+'], ['Y3', 'Y3'], ['Y2', 'Y2'], ['ALL', 'ALL']],
    );

    const mapper = createHistoricalCoordinateMapper({
        historicalDomainXValues: [0, 900],
        currentDomainXValues: [0, 1100],
        historicalMilestones: [
            { time: 100, label: 'Y4+' },
            { time: 200, label: 'Y3' },
            { time: 300, label: 'Y2' },
            { time: 900, label: 'ALL' },
        ],
        currentMilestones: [
            { time: 100, label: 'Y4+' },
            { time: 200, label: 'Y3' },
            { time: 300, label: 'Y2' },
            { time: 900, label: 'ALL' },
            { time: 1000, label: 'Drop' },
            { time: 1100, label: 'Close' },
        ],
        historicalMapTime: time => time,
        currentMapTime: time => time,
    });

    assert.equal(mapper.mapX(100), 100);
    assert.equal(mapper.mapX(300), 300);
    assert.equal(mapper.mapX(900), 900);
    assert.equal(mapper.anchors.length, 4);
});
