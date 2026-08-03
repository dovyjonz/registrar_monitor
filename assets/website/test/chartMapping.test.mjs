import test from 'node:test';
import assert from 'node:assert/strict';
import { _steppedLineTo } from 'chart.js/helpers';

import {
    OBSERVATION_STEP_MODE,
    buildAverageChartPoints,
    buildCourseChartDomain,
    buildSectionChartPoints,
    getChartMapper,
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
    });
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
