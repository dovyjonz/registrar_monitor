import test from 'node:test';
import assert from 'node:assert/strict';
import { _steppedLineTo } from 'chart.js/helpers';

import {
    ENROLLMENT_SCALE_MAX,
    OBSERVATION_STEP_MODE,
    buildAverageChartPoints,
    buildCourseChartDomain,
    buildObservedCapacityPoints,
    buildRegistrationUnavailableIntervals,
    buildSectionChartPoints,
    extendSteppedSeriesToDomainEnd,
    findSteppedPointIndexAtX,
    filterMilestonesToObservedRange,
    getChartMapper,
    getEnrollmentScaleMax,
    getSectionTypeName,
    limitPointsAroundMilestones,
    markObservationEnd,
} from '../src/chartMapping.mjs';

test('stepped interactions stop outside the recorded observation range', () => {
    const xValues = [10, 20, 40];

    assert.equal(findSteppedPointIndexAtX(xValues, 9), null);
    assert.equal(findSteppedPointIndexAtX(xValues, 10), 0);
    assert.equal(findSteppedPointIndexAtX(xValues, 35), 1);
    assert.equal(findSteppedPointIndexAtX(xValues, 40), 2);
    assert.equal(findSteppedPointIndexAtX(xValues, 41), null);
});

test('archived milestone guides stay inside real observed bounds', () => {
    const domain = [{ timestamp: 100 }, { timestamp: 200 }];
    const milestones = [
        { time: new Date(50).toISOString(), label: 'Before' },
        { time: new Date(150).toISOString(), label: 'Inside' },
        { time: new Date(250).toISOString(), label: 'After' },
    ];

    assert.deepEqual(
        filterMilestonesToObservedRange(milestones, domain).map(item => item.label),
        ['Inside'],
    );
});

test('enrollment step transitions occur at the observation timestamp', () => {
    const calls = [];
    _steppedLineTo(
        { lineTo: (...args) => calls.push(args) },
        { x: 10, y: 20 },
        { x: 30, y: 40 },
        false,
        OBSERVATION_STEP_MODE,
    );

    assert.deepEqual(calls, [[30, 20], [30, 40]]);
});

test('full enrollment markers have headroom above the chart boundary', () => {
    assert.equal(getEnrollmentScaleMax([100]), ENROLLMENT_SCALE_MAX);
    assert.equal(getEnrollmentScaleMax([132]), 140);
    assert.equal(getEnrollmentScaleMax([]), ENROLLMENT_SCALE_MAX);
});

test('section type names are shared by browser and preview renderers', () => {
    assert.equal(getSectionTypeName('  B  '), 'Lab');
    assert.equal(getSectionTypeName('PLB'), 'PLB');
    assert.equal(getSectionTypeName(''), 'Other');
});

const snapshots = [
    { ts: '2026-01-01T10:00:00Z' },
    { ts: '2026-01-02T10:00:00Z' },
    { ts: '2026-01-20T10:00:00Z' },
    { ts: '2026-01-21T10:00:00Z' },
    { ts: '2026-01-22T10:00:00Z' },
];

const course = {
    ah: [
        { i: 0, f: 0.25 },
        { i: 3, f: 0.50 },
        { i: 4, f: 0.75 },
    ],
    s: {
        A: {
            h: [
                { i: 1, f: 0.20, e: 4, c: 20 },
                { i: 2, f: 0.45, e: 9, c: 20 },
                { i: 3, f: 0.60, e: 18, c: 30 },
            ],
        },
    },
};

const milestones = [
    { label: 'Open', time: '2025-12-31T10:00:00Z', color: '#2dd4bf' },
    { label: 'Mid', time: '2026-01-15T10:00:00Z', color: '#facc15' },
    { label: 'Close', time: '2026-01-25T10:00:00Z', color: '#fb7185' },
];

function xForSnapshot(points, xValues, snapshotIdx) {
    const index = points.findIndex(point => point.snapshotIdx === snapshotIdx);
    assert.notEqual(index, -1);
    return xValues[index];
}

test('course chart domain is the canonical snapshot range for all course histories', () => {
    const domain = buildCourseChartDomain(course, snapshots);

    assert.deepEqual(domain.map(point => point.snapshotIdx), [0, 1, 2, 3, 4]);
    assert.equal(domain[3].timestamp, Date.parse('2026-01-21T10:00:00Z'));
});

test('unchanged enrollment remains observed through the latest real snapshot', () => {
    const sparseCourse = {
        ah: [{ i: 1, f: 0.5 }],
        s: { A: { h: [{ i: 1, f: 0.5, e: 10, c: 20 }] } },
    };

    assert.deepEqual(
        buildCourseChartDomain(sparseCourse, snapshots).map(point => point.snapshotIdx),
        [1, 2, 3, 4],
    );
});

test('shared snapshots map to the same x value across sparse average and section series', () => {
    const domain = buildCourseChartDomain(course, snapshots);
    const averagePoints = buildAverageChartPoints(course, snapshots);
    const sectionPoints = buildSectionChartPoints(course.s.A, snapshots);

    for (const mode of ['phased', 'snapshots', 'timeline']) {
        const averageMapper = getChartMapper(mode, averagePoints, domain, milestones);
        const sectionMapper = getChartMapper(mode, sectionPoints, domain, milestones);

        assert.equal(
            xForSnapshot(averagePoints, averageMapper.xValues, 3),
            xForSnapshot(sectionPoints, sectionMapper.xValues, 3),
            `${mode} should map snapshotIdx 3 consistently`,
        );
        assert.equal(
            averageMapper.mapTime(Date.parse('2026-01-21T10:00:00Z')),
            sectionMapper.mapTime(Date.parse('2026-01-21T10:00:00Z')),
            `${mode} should map timestamp consistently`,
        );
    }
});

test('chart mappings recover the real time beneath a gliding phased cursor', () => {
    const domain = buildCourseChartDomain(course, snapshots);
    const points = buildAverageChartPoints(course, snapshots);
    const timestamp = Date.parse('2026-01-10T10:00:00Z');

    for (const mode of ['phased', 'snapshots', 'timeline']) {
        const mapper = getChartMapper(mode, points, domain, milestones);
        assert.ok(
            Math.abs(mapper.unmapX(mapper.mapTime(timestamp)) - timestamp) < 1,
            `${mode} should round-trip an in-between timestamp`,
        );
    }
});

test('timeline mapping preserves literal elapsed durations across long gaps', () => {
    const hour = 60 * 60 * 1000;
    const domain = [0, 2 * hour, 50 * hour, 52 * hour].map((timestamp, snapshotIdx) => ({
        snapshotIdx,
        timestamp,
    }));
    const mapper = getChartMapper('timeline', domain, domain, []);

    assert.deepEqual(mapper.domainXValues, [0, 2 * hour, 50 * hour, 52 * hour]);
    assert.equal(mapper.mapTime(50 * hour) - mapper.mapTime(2 * hour), 48 * hour);
    assert.equal(mapper.mapTime(52 * hour) - mapper.mapTime(50 * hour), 2 * hour);
    assert.equal(mapper.unmapX(51 * hour), 51 * hour);
});

test('section chart points keep tooltip and capacity marker metadata on the source snapshot', () => {
    const points = buildSectionChartPoints(course.s.A, snapshots);
    const capacityChange = points.find(point => point.capacityChanged);

    assert.deepEqual(capacityChange, {
        snapshotIdx: 3,
        timestamp: Date.parse('2026-01-21T10:00:00Z'),
        label: new Date('2026-01-21T10:00:00Z').toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        }),
        fill: 60,
        enrollment: 18,
        capacity: 30,
        prevCapacity: 20,
        capacityChanged: true,
        enrollmentLevel: 90,
        capacityLevel: 150,
    });
});

test('the shared phased mapping keeps edge observations in compact gutters', () => {
    const milestones = [100, 150, 200, 300].map((time, index) => ({
        time: new Date(time).toISOString(),
        label: `Milestone ${index + 1}`,
    }));
    const domain = [
        { timestamp: 0 },
        { timestamp: 100 },
        { timestamp: 200 },
        { timestamp: 300 },
        { timestamp: 400 },
    ];
    const mapper = getChartMapper(
        'phased',
        domain,
        domain,
        milestones,
    );

    assert.equal(mapper.mapTime(100) - mapper.mapTime(0), 2);
    assert.equal(mapper.mapTime(150) - mapper.mapTime(100), 100);
    assert.equal(mapper.mapTime(200) - mapper.mapTime(150), 100);
    assert.equal(mapper.mapTime(300) - mapper.mapTime(200), 100);
    assert.equal(mapper.mapTime(400) - mapper.mapTime(300), 2);
});

test('the shared phased mapping leaves an observation before its future milestone', () => {
    const latest = Date.parse('2026-08-13T10:55:00+05:00');
    const next = Date.parse('2026-08-13T11:00:00+05:00');
    const milestones = [
        '2026-08-13T09:00:00+05:00',
        '2026-08-13T11:00:00+05:00',
        '2026-08-13T13:00:00+05:00',
    ].map((time, index) => ({ time, label: `M${index + 1}` }));
    const points = [{ timestamp: latest }];
    const mapper = getChartMapper('phased', points, points, milestones);

    assert.ok(mapper.mapTime(latest) < mapper.mapTime(next));
});

test('capacity changes move the capacity line without moving unchanged enrollment', () => {
    const points = buildSectionChartPoints({
        h: [
            { i: 0, f: 0.5, e: 10, c: 20 },
            { i: 1, f: 1 / 3, e: 10, c: 30 },
        ],
    }, snapshots);

    assert.deepEqual(points.map(point => point.enrollmentLevel), [50, 50]);
    assert.deepEqual(points.map(point => point.capacityLevel), [100, 150]);
    assert.deepEqual(points.map(point => point.fill), [50, 33]);
});

test('capacity line covers observed timestamps only', () => {
    assert.deepEqual(
        buildObservedCapacityPoints([undefined, 100, 125], [10, 20, 30]),
        [
            { x: 20, y: 100, sourceIndex: 1 },
            { x: 30, y: 125, sourceIndex: 2 },
        ],
    );
});

test('removed sections do not dilute later course enrollment or capacity levels', () => {
    const courseWithRemovedSection = {
        ah: [{ i: 3, f: 1 }],
        ev: [{ et: 'section_removed', sc: 'B', i: 2 }],
        s: {
            A: {
                t: 'L',
                h: [
                    { i: 0, f: 0.5, e: 50, c: 100 },
                    { i: 3, f: 2, e: 100, c: 50 },
                ],
            },
            B: {
                t: 'Lb',
                h: [
                    { i: 0, f: 0, e: 0, c: 100 },
                    { i: 1, f: 0.1, e: 10, c: 100 },
                ],
            },
        },
    };

    const [point] = buildAverageChartPoints(courseWithRemovedSection, snapshots);

    assert.equal(point.enrollmentLevel, 100);
    assert.equal(point.capacityLevel, 50);
    assert.equal(point.enrollment, 100);
    assert.equal(point.capacity, 50);
});

test('course tooltip totals use the minimum section-type sum', () => {
    const courseWithLinkedSections = {
        ah: [{ i: 0, f: 0.49 }],
        s: {
            Lecture: { t: 'L', h: [{ i: 0, f: 44 / 90, e: 44, c: 90 }] },
            Lab1: { t: 'Lb', h: [{ i: 0, f: 19 / 45, e: 19, c: 45 }] },
            Lab2: { t: 'Lb', h: [{ i: 0, f: 25 / 45, e: 25, c: 45 }] },
        },
    };

    const [point] = buildAverageChartPoints(courseWithLinkedSections, snapshots);

    assert.equal(point.enrollment, 44);
    assert.equal(point.capacity, 90);
});

test('course levels weight active sections by their aggregate opening capacity', () => {
    const snapshots = [
        { ts: '2026-08-11T10:00:00Z' },
        { ts: '2026-08-17T10:00:00Z' },
        { ts: '2026-08-18T10:00:00Z' },
    ];
    const asc200 = {
        ah: [
            { i: 0, f: 0 },
            { i: 1, f: 33 / 32 },
            { i: 2, f: (33 / 32 + 1) / 2 },
        ],
        ev: [{ et: 'section_added', sc: '2S', i: 2 }],
        s: {
            '1S': {
                t: 'S',
                h: [
                    { i: 0, f: 0, e: 0, c: 12 },
                    { i: 1, f: 33 / 32, e: 33, c: 32 },
                    { i: 2, f: 33 / 32, e: 33, c: 32 },
                ],
            },
            '2S': {
                t: 'S',
                h: [{ i: 2, f: 1, e: 36, c: 36 }],
            },
        },
    };

    const points = buildAverageChartPoints(asc200, snapshots);

    assert.equal(points[1].capacityLevel, (32 / 12) * 100);
    assert.equal(points[2].enrollment, 69);
    assert.equal(points[2].capacity, 68);
    assert.equal(points[2].enrollmentLevel, (69 / 48) * 100);
    assert.equal(points[2].capacityLevel, (68 / 48) * 100);
});

test('synthetic capacity-change points average only active sections', () => {
    const courseWithSyntheticPoint = {
        ah: [{ i: 0, f: 0.25 }],
        ev: [{ et: 'section_removed', sc: 'B', i: 2 }],
        s: {
            A: {
                t: 'L',
                h: [
                    { i: 0, f: 0.5, e: 50, c: 100 },
                    { i: 3, f: 2, e: 100, c: 50 },
                ],
            },
            B: {
                t: 'Lb',
                h: [
                    { i: 0, f: 0, e: 0, c: 100 },
                    { i: 1, f: 0.1, e: 10, c: 100 },
                ],
            },
        },
    };

    const synthetic = buildAverageChartPoints(courseWithSyntheticPoint, snapshots)
        .find(point => point.snapshotIdx === 3);

    assert.equal(synthetic.fill, 200);
});

test('phased charts retain two observations around each registration boundary', () => {
    const milestones = [
        { time: '2026-01-21T10:00:00Z', label: 'Open' },
        { time: '2026-01-23T10:00:00Z', label: 'Close' },
    ];
    const points = snapshots.map((snapshot, snapshotIdx) => ({
        snapshotIdx,
        timestamp: Date.parse(snapshot.ts),
    }));
    points.push(
        { snapshotIdx: 5, timestamp: Date.parse('2026-01-24T10:00:00Z') },
        { snapshotIdx: 6, timestamp: Date.parse('2026-01-25T10:00:00Z') },
        { snapshotIdx: 7, timestamp: Date.parse('2026-01-26T10:00:00Z') },
    );

    assert.deepEqual(
        limitPointsAroundMilestones(points, milestones, 2)
            .map(point => point.snapshotIdx),
        [1, 2, 3, 4, 5, 6],
    );
});

test('sparse current lines carry their last value through the recorded observation domain', () => {
    assert.deepEqual(
        extendSteppedSeriesToDomainEnd(
            [{ x: 10, y: 25, sourceIndex: 2 }, { x: 20, y: 50, sourceIndex: 4 }],
            40,
        ),
        [
            { x: 10, y: 25, sourceIndex: 2 },
            { x: 20, y: 50, sourceIndex: 4 },
            { x: 40, y: 50, synthetic: true, sourceIndex: 4 },
        ],
    );
});

test('average chart points surface section capacity changes at course level', () => {
    const courseWithStableAverage = {
        ah: [
            { i: 0, f: 0.50 },
            { i: 2, f: 0.50 },
        ],
        s: {
            A: {
                h: [
                    { i: 0, f: 0.50, e: 10, c: 20 },
                    { i: 1, f: 0.50, e: 15, c: 30 },
                    { i: 2, f: 0.50, e: 15, c: 30 },
                ],
            },
        },
    };

    const points = buildAverageChartPoints(courseWithStableAverage, snapshots);

    assert.deepEqual(points.map(point => point.snapshotIdx), [0, 1, 2]);
    assert.deepEqual(points[1].capacityChanges, [{
        sectionCode: 'A',
        previousCapacity: 20,
        capacity: 30,
    }]);
    assert.equal(points[1].capacityChanged, true);
});

test('course chart points mark intervals where a required section type is full', () => {
    const snapshots = [
        { ts: '2026-08-01T10:00:00Z' },
        { ts: '2026-08-02T10:00:00Z' },
    ];
    const course = {
        ah: [{ i: 0, f: 0.5 }, { i: 1, f: 0.75 }],
        s: {
            L1: { t: 'L', h: [{ i: 0, e: 10, c: 20, f: 0.5 }, { i: 1, e: 11, c: 20, f: 0.55 }] },
            B1: { t: 'B', h: [{ i: 0, e: 5, c: 10, f: 0.5 }, { i: 1, e: 10, c: 10, f: 1 }] },
        },
    };

    const points = buildAverageChartPoints(course, snapshots);
    assert.equal(points[0].registrationUnavailable, false);
    assert.equal(points[1].registrationUnavailable, true);
    assert.deepEqual(points[1].limitingTypes, ['Lab']);
});

test('overfilled siblings cannot cancel registration places in another section', () => {
    const snapshots = [{ ts: '2026-08-01T10:00:00Z' }];
    const course = { ah: [{ i: 0, f: 1 }], s: {
        L1: { t: 'L', h: [{ i: 0, e: 15, c: 10, f: 1.5 }] },
        L2: { t: 'L', h: [{ i: 0, e: 5, c: 10, f: 0.5 }] },
        B1: { t: 'B', h: [{ i: 0, e: 0, c: 10, f: 0 }] },
    } };
    const points = buildAverageChartPoints(course, snapshots);
    assert.equal(points[0].registrationUnavailable, false);
    assert.deepEqual(buildRegistrationUnavailableIntervals(points.map(p => ({ ...p, x: 0 })), 1), []);
    delete course.s.L2;
    assert.equal(buildAverageChartPoints(course, snapshots)[0].registrationUnavailable, true);
    course.s.B1.h = [{ i: 0, e: 0, c: 0, f: 0 }];
    course.s.L1.h = [{ i: 0, e: 5, c: 10, f: 0.5 }];
    assert.equal(buildAverageChartPoints(course, snapshots)[0].registrationUnavailable, true);
    delete course.s.B1;
    assert.equal(buildAverageChartPoints(course, snapshots)[0].registrationUnavailable, false);
});

test('registration-unavailable intervals decorate observed state without changing points', () => {
    const points = [
        { x: 0, registrationUnavailable: false, sourceIndex: 0 },
        { x: 10, registrationUnavailable: true, limitingTypes: ['Lab'], sourceIndex: 1 },
        { x: 20, registrationUnavailable: true, limitingTypes: ['Lab'], sourceIndex: 2 },
        { x: 30, registrationUnavailable: false, sourceIndex: 3 },
        { x: 40, registrationUnavailable: true, limitingTypes: ['Lecture'], sourceIndex: 4 },
    ];

    assert.deepEqual(buildRegistrationUnavailableIntervals(points, 50), [
        { xMin: 10, xMax: 30, limitingTypes: ['Lab'] },
        { xMin: 40, xMax: 50, limitingTypes: ['Lecture'] },
    ]);
    assert.equal(points.some(point => 'registrationInterval' in point), false);
});

test('observation-end cue decorates the last real point without changing the series', () => {
    const points = [{ x: 10, y: 25, sourceIndex: 2 }, { x: 20, y: 50, sourceIndex: 4 }];
    const decorated = markObservationEnd(points);

    assert.equal(decorated.length, points.length);
    assert.deepEqual(decorated.map(({ x, y, sourceIndex }) => ({ x, y, sourceIndex })), points);
    assert.equal(decorated.at(-1).observationEnded, true);
    assert.equal(points.at(-1).observationEnded, undefined);
});
