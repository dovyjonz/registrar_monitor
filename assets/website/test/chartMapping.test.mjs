import test from 'node:test';
import assert from 'node:assert/strict';
import { _steppedLineTo } from 'chart.js/helpers';

import {
    ENROLLMENT_SCALE_MAX,
    OBSERVATION_STEP_MODE,
    buildAverageChartPoints,
    buildCourseChartDomain,
    buildSectionChartPoints,
    extendSteppedSeriesToDomainEnd,
    getChartMapper,
    getEnrollmentScaleMax,
    getPhasedMapper,
    limitPointsBeforeFirstMilestone,
} from '../src/chartMapping.mjs';

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

test('phased mapping gives edge history less space than registration phases', () => {
    const mapper = getPhasedMapper(
        [{ timestamp: 0 }, { timestamp: 400 }],
        [{ timestamp: 0 }, { timestamp: 400 }],
        [100, 200, 300].map(time => ({ time: new Date(time).toISOString() })),
    );

    assert.equal(mapper.mapTime(100) - mapper.mapTime(0), 35);
    assert.equal(mapper.mapTime(200) - mapper.mapTime(100), 100);
    assert.equal(mapper.mapTime(400) - mapper.mapTime(300), 35);
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

test('phased charts retain only the last two observations before opening', () => {
    const firstMilestone = { time: '2026-01-21T10:00:00Z', label: 'Open' };
    const points = snapshots.map((snapshot, snapshotIdx) => ({
        snapshotIdx,
        timestamp: Date.parse(snapshot.ts),
    }));

    assert.deepEqual(
        limitPointsBeforeFirstMilestone(points, [firstMilestone], 2)
            .map(point => point.snapshotIdx),
        [1, 2, 3, 4],
    );
});

test('historical stepped lines extend their final observation to the comparison domain end', () => {
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
