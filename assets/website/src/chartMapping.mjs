/**
 * Pure helpers for course modal chart point construction and x-axis mapping.
 */

import { buildSectionActivityTimeline } from './historicalComparison.mjs';

export {
    buildInstructorAssignmentTimeline,
    buildProfessorAverageChartPoints,
    buildProfessorChartPoints,
    buildSectionActivityTimeline,
    courseHasProfessor,
    createHistoricalCoordinateMapper,
    getInstructorAtSnapshot,
    getHistoricalMilestoneAlignment,
    normalizeHistoricalChartDomain,
    normalizeHistoricalDomain,
    normalizeInstructorName,
    normalizeProfessorIdentity,
} from './historicalComparison.mjs';

// Chart.js's "before" mode draws the transition at the newer observation's
// x-coordinate. Enrollment history points represent observations, so a value
// must not appear before the snapshot that reports it.
export const OBSERVATION_STEP_MODE = 'before';

// Keep full-capacity markers inside the chart area instead of placing their
// radius directly on the 100% boundary. This is the minimum ceiling; charts
// with over-capacity sections expand beyond it.
export const ENROLLMENT_SCALE_MAX = 105;

const SECTION_TYPE_NAMES = {
    L: 'Lecture',
    S: 'Seminar',
    R: 'Recitation',
    D: 'Discussion',
    B: 'Lab',
    Lb: 'Lab',
    Int: 'Internship',
    P: 'Project',
    IS: 'Independent Study',
    T: 'Tutorial',
};

export function getSectionTypeName(value, fallback = 'Other') {
    const compact = String(value ?? fallback).replace(/\s+/g, ' ').trim() || fallback;
    return SECTION_TYPE_NAMES[compact] ?? compact;
}

export function getMajorMilestones(milestones = []) {
    const seenPriorities = new Set();
    return milestones.filter(milestone => {
        if (!milestone.priority) return true;
        if (seenPriorities.has(milestone.priority)) return false;
        seenPriorities.add(milestone.priority);
        return true;
    });
}

export function getEnrollmentScaleMax(values = []) {
    const maxValue = values
        .filter(Number.isFinite)
        .reduce((maximum, value) => Math.max(maximum, value), 0);
    const paddedMax = Math.ceil((maxValue + 5) / 5) * 5;
    return Math.max(ENROLLMENT_SCALE_MAX, paddedMax);
}

export function getSortedUniqueNumbers(values) {
    return [...new Set(values.filter(Number.isFinite))].sort((a, b) => a - b);
}

export function findSteppedPointIndexAtX(xValues, pixelX) {
    const values = (xValues || []).filter(Number.isFinite);
    if (!Number.isFinite(pixelX) || values.length === 0) return null;
    if (pixelX < values[0] || pixelX > values.at(-1)) return null;

    let selectedIndex = 0;
    for (let index = 1; index < values.length; index++) {
        if (values[index] > pixelX) break;
        selectedIndex = index;
    }
    return selectedIndex;
}

export function getMedianPositiveGap(values) {
    const sorted = getSortedUniqueNumbers(values);
    const gaps = [];
    for (let i = 1; i < sorted.length; i++) {
        const gap = sorted[i] - sorted[i - 1];
        if (gap > 0) gaps.push(gap);
    }
    if (gaps.length === 0) return 0;
    gaps.sort((a, b) => a - b);
    const middle = Math.floor(gaps.length / 2);
    return gaps.length % 2 === 0 ? (gaps[middle - 1] + gaps[middle]) / 2 : gaps[middle];
}

export function getAdaptiveTimelineGapCap(timestamps, allTimes) {
    const oneHour = 60 * 60 * 1000;
    const oneDay = 24 * oneHour;
    const medianGap = getMedianPositiveGap(timestamps) || getMedianPositiveGap(allTimes);
    if (!medianGap) return oneDay;
    return Math.min(Math.max(medianGap * 6, oneHour), oneDay);
}

export function getXScaleBounds(xValues) {
    const sorted = getSortedUniqueNumbers(xValues);
    if (sorted.length === 0) {
        return { min: 0, max: 1, minRange: 1 };
    }
    if (sorted.length === 1) {
        const center = sorted[0];
        return { min: center - 1, max: center + 1, minRange: 1 };
    }

    const min = sorted[0];
    const max = sorted[sorted.length - 1];
    const range = Math.max(max - min, 1);
    const medianGap = getMedianPositiveGap(sorted) || range;
    const padding = Math.min(Math.max(medianGap * 0.25, range * 0.01), range * 0.03);
    const minRange = Math.min(Math.max(medianGap * 2, range * 0.03), range);

    return {
        min: min - padding,
        max: max + padding,
        minRange: Math.max(minRange, Number.EPSILON),
    };
}

function toTimestamp(snapshot) {
    if (!snapshot) return null;
    const timestamp = new Date(snapshot.ts).getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
}

function formatSnapshotLabel(timestamp) {
    return new Date(timestamp).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function getHistorySnapshotIndices(history) {
    return (history || [])
        .map(point => point.i)
        .filter(Number.isInteger);
}

export function buildCourseChartDomain(course, snapshots) {
    if (!course || !Array.isArray(snapshots)) return [];

    const indices = [
        ...getHistorySnapshotIndices(course.ah),
        ...Object.values(course.s || {}).flatMap(section => getHistorySnapshotIndices(section.h)),
    ].filter(idx => idx >= 0 && idx < snapshots.length && toTimestamp(snapshots[idx]) !== null);

    if (indices.length === 0) return [];

    const minIdx = Math.min(...indices);
    // Registrar snapshots are complete observations. An unchanged course is
    // still observed through the latest snapshot even when its compact history
    // contains no new value-change record.
    const maxIdx = snapshots.length - 1;
    const domain = [];
    for (let snapshotIdx = minIdx; snapshotIdx <= maxIdx; snapshotIdx++) {
        const timestamp = toTimestamp(snapshots[snapshotIdx]);
        if (timestamp !== null) {
            domain.push({
                snapshotIdx,
                timestamp,
                label: formatSnapshotLabel(timestamp),
            });
        }
    }
    return domain;
}

function getCourseSections(course) {
    return course?.s || course?.sections || {};
}

function getCourseEvents(course) {
    return course?.ev || course?.events || [];
}

function buildCourseSectionActivity(course, snapshots) {
    const events = getCourseEvents(course);
    return new Map(Object.entries(getCourseSections(course)).map(([sectionCode, section]) => [
        sectionCode,
        buildSectionActivityTimeline(sectionCode, section, events, snapshots, course),
    ]));
}

function isSectionActiveAtSnapshot(activity, sectionCode, snapshotIdx) {
    return activity.get(sectionCode)?.isActiveAt(snapshotIdx) ?? false;
}

function getCapacityChangesBySnapshot(course, activity) {
    const changes = new Map();
    for (const [sectionCode, section] of Object.entries(getCourseSections(course))) {
        let previousCapacity = null;
        for (const point of section.h || []) {
            if (!Number.isInteger(point.i) || !Number.isFinite(point.c)) continue;
            if (!isSectionActiveAtSnapshot(activity, sectionCode, point.i)) continue;
            if (previousCapacity !== null && point.c !== previousCapacity) {
                const snapshotChanges = changes.get(point.i) || [];
                snapshotChanges.push({
                    sectionCode,
                    previousCapacity,
                    capacity: point.c,
                });
                changes.set(point.i, snapshotChanges);
            }
            previousCapacity = point.c;
        }
    }
    return changes;
}

function getSectionFillAtSnapshot(section, snapshotIdx) {
    let fill = null;
    for (const point of section?.h || []) {
        if (!Number.isInteger(point.i) || point.i > snapshotIdx) continue;
        if (Number.isFinite(point.f)) fill = point.f;
    }
    return fill;
}

function getSectionStateAtSnapshot(section, snapshotIdx) {
    let state = null;
    for (const point of section?.h || []) {
        if (!Number.isInteger(point.i) || point.i > snapshotIdx) continue;
        if (Number.isFinite(point.e) && Number.isFinite(point.c)) state = point;
    }
    return state;
}

function getOpeningCapacity(section) {
    return (section?.h || []).find(point => Number.isFinite(point.c) && point.c > 0)?.c ?? null;
}

function getCourseLevelsAtSnapshot(course, snapshotIdx, activity) {
    const levels = Object.entries(getCourseSections(course)).flatMap(([sectionCode, section]) => {
        if (!isSectionActiveAtSnapshot(activity, sectionCode, snapshotIdx)) return [];
        const state = getSectionStateAtSnapshot(section, snapshotIdx);
        const openingCapacity = getOpeningCapacity(section);
        if (!state || !openingCapacity) return [];
        return [{
            sectionType: section.t || section.type || 'Other',
            enrollment: state.e,
            capacity: state.c,
            enrollmentLevel: (state.e / openingCapacity) * 100,
            capacityLevel: (state.c / openingCapacity) * 100,
        }];
    });
    if (levels.length === 0) return null;
    const average = key => levels.reduce((total, value) => total + value[key], 0) / levels.length;
    const totalsByType = new Map();
    for (const level of levels) {
        const totals = totalsByType.get(level.sectionType) || {
            sectionType: level.sectionType,
            enrollment: 0,
            capacity: 0,
        };
        totals.enrollment += level.enrollment;
        totals.capacity += level.capacity;
        totalsByType.set(level.sectionType, totals);
    }
    const limitingType = [...totalsByType.values()].sort((first, second) => {
        const firstAvailable = Math.max(first.capacity - first.enrollment, 0);
        const secondAvailable = Math.max(second.capacity - second.enrollment, 0);
        return firstAvailable - secondAvailable
            || first.sectionType.localeCompare(second.sectionType);
    })[0];
    return {
        enrollment: limitingType.enrollment,
        capacity: limitingType.capacity,
        enrollmentLevel: average('enrollmentLevel'),
        capacityLevel: average('capacityLevel'),
    };
}

function getAverageFillAtSnapshot(course, snapshotIdx, activity) {
    const fills = Object.entries(getCourseSections(course))
        .filter(([sectionCode]) => isSectionActiveAtSnapshot(activity, sectionCode, snapshotIdx))
        .map(([, section]) => getSectionFillAtSnapshot(section, snapshotIdx))
        .filter(Number.isFinite);
    if (fills.length === 0) return null;
    return fills.reduce((total, fill) => total + fill, 0) / fills.length;
}

export function buildAverageChartPoints(course, snapshots) {
    const activity = buildCourseSectionActivity(course, snapshots);
    const pointsBySnapshot = new Map(
        (course?.ah || [])
            .filter(point => Number.isInteger(point.i))
            .map(point => [point.i, point]),
    );
    const capacityChanges = getCapacityChangesBySnapshot(course, activity);
    for (const snapshotIdx of capacityChanges.keys()) {
        if (pointsBySnapshot.has(snapshotIdx)) continue;
        const fill = getAverageFillAtSnapshot(course, snapshotIdx, activity);
        if (fill !== null) pointsBySnapshot.set(snapshotIdx, { i: snapshotIdx, f: fill });
    }

    return [...pointsBySnapshot.entries()]
        .sort(([first], [second]) => first - second)
        .flatMap(([snapshotIdx, point]) => {
            const timestamp = toTimestamp(snapshots?.[point.i]);
            if (timestamp === null) return [];
            const snapshotCapacityChanges = capacityChanges.get(snapshotIdx) || [];
            const levels = getCourseLevelsAtSnapshot(course, snapshotIdx, activity);
            const chartPoint = {
                snapshotIdx,
                timestamp,
                label: formatSnapshotLabel(timestamp),
                fill: Math.round(point.f * 100),
                enrollment: levels?.enrollment ?? null,
                capacity: levels?.capacity ?? null,
                prevCapacity: null,
                capacityChanged: snapshotCapacityChanges.length > 0,
                enrollmentLevel: levels?.enrollmentLevel ?? Math.round(point.f * 100),
                capacityLevel: levels?.capacityLevel ?? 100,
            };
            if (snapshotCapacityChanges.length > 0) {
                chartPoint.capacityChanges = snapshotCapacityChanges;
            }
            return [chartPoint];
        });
}

export function buildSectionChartPoints(section, snapshots) {
    let prevCapacity = null;
    const openingCapacity = getOpeningCapacity(section);
    return (section?.h || []).flatMap(point => {
        const timestamp = toTimestamp(snapshots?.[point.i]);
        if (timestamp === null) return [];

        const capacityChanged = prevCapacity !== null && point.c !== prevCapacity;
        const chartPoint = {
            snapshotIdx: point.i,
            timestamp,
            label: formatSnapshotLabel(timestamp),
            fill: Math.round(point.f * 100),
            enrollment: point.e,
            capacity: point.c,
            prevCapacity,
            capacityChanged,
            enrollmentLevel: openingCapacity ? (point.e / openingCapacity) * 100 : Math.round(point.f * 100),
            capacityLevel: openingCapacity ? (point.c / openingCapacity) * 100 : 100,
        };
        prevCapacity = point.c;
        return [chartPoint];
    });
}

export function limitPointsAroundMilestones(points, milestones, maximum = 2) {
    const milestoneTimes = (milestones || [])
        .map(milestone => new Date(milestone?.time).getTime())
        .filter(Number.isFinite)
        .sort((first, second) => first - second);
    if (milestoneTimes.length === 0 || maximum < 0) return [...(points || [])];
    const firstMilestone = milestoneTimes[0];
    const lastMilestone = milestoneTimes.at(-1);
    const before = (points || []).filter(point => point.timestamp < firstMilestone);
    const during = (points || []).filter(point => (
        point.timestamp >= firstMilestone && point.timestamp <= lastMilestone
    ));
    const after = (points || []).filter(point => point.timestamp > lastMilestone);
    return [...before.slice(-maximum), ...during, ...after.slice(0, maximum)];
}

export function extendSteppedSeriesToDomainEnd(points, domainEnd) {
    const series = [...(points || [])];
    const last = series.at(-1);
    if (!last || !Number.isFinite(domainEnd) || last.x >= domainEnd) return series;
    return [...series, {
        x: domainEnd,
        y: last.y,
        synthetic: true,
        ...('sourceIndex' in last ? { sourceIndex: last.sourceIndex } : {}),
    }];
}

export function buildObservedCapacityPoints(capacityValues, xValues) {
    return (capacityValues || []).flatMap((y, index) => (
        Number.isFinite(y) && Number.isFinite(xValues?.[index])
            ? [{ x: xValues[index], y, sourceIndex: index }]
            : []
    ));
}

function getDomainTimes(domain) {
    return getSortedUniqueNumbers((domain || []).map(point => point.timestamp));
}

function getFallbackDomain(points, domain) {
    if (domain && domain.length > 0) return domain;
    return (points || []).map(point => ({
        snapshotIdx: point.snapshotIdx,
        timestamp: point.timestamp,
        label: point.label,
    }));
}

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

function createPiecewiseInverse(sourceValues, mappedValues) {
    if (sourceValues.length === 0 || mappedValues.length === 0) return value => value;
    if (sourceValues.length === 1 || mappedValues.length === 1) return () => sourceValues[0];
    return (mappedValue) => {
        const value = clamp(mappedValue, mappedValues[0], mappedValues.at(-1));
        for (let index = 0; index < mappedValues.length - 1; index++) {
            if (value > mappedValues[index + 1]) continue;
            const mappedSpan = mappedValues[index + 1] - mappedValues[index];
            const fraction = mappedSpan === 0 ? 0 : (value - mappedValues[index]) / mappedSpan;
            return sourceValues[index]
                + fraction * (sourceValues[index + 1] - sourceValues[index]);
        }
        return sourceValues.at(-1);
    };
}

/**
 * Keep major registration phases equal while distributing intermediate
 * eligibility milestones evenly inside each phase.
 */
export function getHierarchicalPhasedMapper(points, domain, milestones, majorMilestones) {
    const chartPoints = points || [];
    const canonicalDomain = getFallbackDomain(chartPoints, domain);
    const domainTimes = getDomainTimes(canonicalDomain);
    const allTimes = getSortedUniqueNumbers(
        (milestones || []).map(milestone => new Date(milestone.time).getTime()),
    );
    const majorTimes = getSortedUniqueNumbers(
        (majorMilestones || []).map(milestone => new Date(milestone.time).getTime()),
    );
    if (majorTimes.length < 2) return getTimelineMapper(points, domain);

    const sourceValues = [];
    const mappedValues = [];
    const firstData = domainTimes[0] ?? majorTimes[0];
    if (firstData < majorTimes[0]) {
        sourceValues.push(firstData);
        mappedValues.push(-2);
    }
    for (let majorIndex = 0; majorIndex < majorTimes.length - 1; majorIndex++) {
        const start = majorTimes[majorIndex];
        const end = majorTimes[majorIndex + 1];
        const intermediate = allTimes.filter(time => time > start && time < end);
        const phaseTimes = [start, ...intermediate, end];
        const phaseWidth = 100 / (phaseTimes.length - 1);
        phaseTimes.forEach((time, index) => {
            if (sourceValues.at(-1) === time) return;
            sourceValues.push(time);
            mappedValues.push((majorIndex * 100) + (index * phaseWidth));
        });
    }
    const lastData = domainTimes.at(-1) ?? majorTimes.at(-1);
    if (lastData > majorTimes.at(-1)) {
        sourceValues.push(lastData);
        mappedValues.push(((majorTimes.length - 1) * 100) + 2);
    }
    const mapTime = (time) => {
        if (time <= sourceValues[0]) return mappedValues[0];
        for (let index = 0; index < sourceValues.length - 1; index++) {
            if (time > sourceValues[index + 1]) continue;
            const span = sourceValues[index + 1] - sourceValues[index];
            const fraction = span === 0 ? 0 : (time - sourceValues[index]) / span;
            return mappedValues[index]
                + fraction * (mappedValues[index + 1] - mappedValues[index]);
        }
        return mappedValues.at(-1);
    };
    return {
        xValues: chartPoints.map(point => mapTime(point.timestamp)),
        domainXValues: domainTimes.map(mapTime),
        mapTime,
        unmapX: createPiecewiseInverse(sourceValues, mappedValues),
    };
}

export function getTimelineMapper(points, domain) {
    const chartPoints = points || [];
    const canonicalDomain = getFallbackDomain(chartPoints, domain);
    const domainTimes = getDomainTimes(canonicalDomain);

    if (domainTimes.length === 0) {
        return { xValues: [], domainXValues: [], mapTime: t => t, unmapX: x => x };
    }

    const maxGapMs = getAdaptiveTimelineGapCap(domainTimes, domainTimes);
    const timeMap = new Map();
    let currentClipped = domainTimes[0];
    timeMap.set(domainTimes[0], currentClipped);

    for (let i = 1; i < domainTimes.length; i++) {
        const diff = domainTimes[i] - domainTimes[i - 1];
        const effectiveDiff = Math.min(diff, maxGapMs);
        currentClipped += effectiveDiff;
        timeMap.set(domainTimes[i], currentClipped);
    }

    const mapTime = t => {
        if (timeMap.has(t)) return timeMap.get(t);
        for (let i = 0; i < domainTimes.length - 1; i++) {
            if (t >= domainTimes[i] && t <= domainTimes[i + 1]) {
                const frac = (t - domainTimes[i]) / (domainTimes[i + 1] - domainTimes[i]);
                return timeMap.get(domainTimes[i]) + frac * (timeMap.get(domainTimes[i + 1]) - timeMap.get(domainTimes[i]));
            }
        }
        if (t < domainTimes[0]) return timeMap.get(domainTimes[0]);
        return timeMap.get(domainTimes[domainTimes.length - 1]);
    };

    const mappedDomainTimes = domainTimes.map(time => timeMap.get(time));
    return {
        xValues: chartPoints.map(point => mapTime(point.timestamp)),
        domainXValues: mappedDomainTimes,
        mapTime,
        unmapX: createPiecewiseInverse(domainTimes, mappedDomainTimes),
    };
}

export function getSnapshotsMapper(points, domain) {
    const chartPoints = points || [];
    const canonicalDomain = getFallbackDomain(chartPoints, domain);
    const sortedDomain = [...canonicalDomain]
        .filter(point => Number.isInteger(point.snapshotIdx) && Number.isFinite(point.timestamp))
        .sort((a, b) => a.snapshotIdx - b.snapshotIdx);

    const snapshotOrdinal = new Map(sortedDomain.map((point, index) => [point.snapshotIdx, index]));
    const domainTimes = sortedDomain.map(point => point.timestamp);

    const mapTime = (t) => {
        if (domainTimes.length === 0) return 0;
        if (t <= domainTimes[0]) return 0;
        if (t >= domainTimes[domainTimes.length - 1]) return domainTimes.length - 1;

        for (let i = 0; i < domainTimes.length - 1; i++) {
            if (t >= domainTimes[i] && t <= domainTimes[i + 1]) {
                const frac = (t - domainTimes[i]) / (domainTimes[i + 1] - domainTimes[i]);
                return i + frac;
            }
        }
        return 0;
    };

    return {
        xValues: chartPoints.map(point => snapshotOrdinal.get(point.snapshotIdx) ?? mapTime(point.timestamp)),
        domainXValues: sortedDomain.map((_, index) => index),
        mapTime,
        unmapX: createPiecewiseInverse(
            domainTimes,
            sortedDomain.map((_, index) => index),
        ),
    };
}

export function getChartMapper(mode, points, domain, milestones, majorMilestones = null) {
    if (mode === 'phased') {
        return getHierarchicalPhasedMapper(
            points,
            domain,
            milestones,
            majorMilestones ?? getMajorMilestones(milestones),
        );
    }
    if (mode === 'snapshots') return getSnapshotsMapper(points, domain);
    return getTimelineMapper(points, domain);
}

/**
 * Final renderer-neutral chart values shared by the dashboard and preview card.
 */
export function buildChartPresentation({
    points,
    domain,
    milestones = [],
    phaseMilestones = milestones,
    majorMilestones = null,
    mode = 'phased',
}) {
    const visiblePoints = limitPointsAroundMilestones(points, milestones);
    const visibleDomain = limitPointsAroundMilestones(domain, milestones);
    const mapper = getChartMapper(
        mode,
        visiblePoints,
        visibleDomain,
        phaseMilestones,
        majorMilestones,
    );
    return {
        visiblePoints,
        visibleDomain,
        enrollmentValues: visiblePoints.map(point => point.enrollmentLevel ?? point.fill),
        capacityValues: visiblePoints.map(point => point.capacityLevel),
        ...mapper,
    };
}
