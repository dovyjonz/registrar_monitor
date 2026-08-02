/**
 * Pure helpers for course modal chart point construction and x-axis mapping.
 */

export {
    buildInstructorAssignmentTimeline,
    buildProfessorAverageChartPoints,
    buildProfessorChartPoints,
    buildSectionActivityTimeline,
    courseHasProfessor,
    getInstructorAtSnapshot,
    normalizeHistoricalChartDomain,
    normalizeHistoricalDomain,
    normalizeInstructorName,
    normalizeProfessorIdentity,
} from './historicalComparison.mjs';

export function getSortedUniqueNumbers(values) {
    return [...new Set(values.filter(Number.isFinite))].sort((a, b) => a - b);
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
    const padding = Math.min(Math.max(medianGap * 0.5, range * 0.02), range * 0.08);
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
    const maxIdx = Math.max(...indices);
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

function getCapacityChangesBySnapshot(course) {
    const changes = new Map();
    for (const [sectionCode, section] of Object.entries(course?.s || {})) {
        let previousCapacity = null;
        for (const point of section.h || []) {
            if (!Number.isInteger(point.i) || !Number.isFinite(point.c)) continue;
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

function getAverageFillAtSnapshot(course, snapshotIdx) {
    const fills = Object.values(course?.s || {})
        .map(section => getSectionFillAtSnapshot(section, snapshotIdx))
        .filter(Number.isFinite);
    if (fills.length === 0) return null;
    return fills.reduce((total, fill) => total + fill, 0) / fills.length;
}

export function buildAverageChartPoints(course, snapshots) {
    const pointsBySnapshot = new Map(
        (course?.ah || [])
            .filter(point => Number.isInteger(point.i))
            .map(point => [point.i, point]),
    );
    const capacityChanges = getCapacityChangesBySnapshot(course);
    for (const snapshotIdx of capacityChanges.keys()) {
        if (pointsBySnapshot.has(snapshotIdx)) continue;
        const fill = getAverageFillAtSnapshot(course, snapshotIdx);
        if (fill !== null) pointsBySnapshot.set(snapshotIdx, { i: snapshotIdx, f: fill });
    }

    return [...pointsBySnapshot.entries()]
        .sort(([first], [second]) => first - second)
        .flatMap(([snapshotIdx, point]) => {
            const timestamp = toTimestamp(snapshots?.[point.i]);
            if (timestamp === null) return [];
            const snapshotCapacityChanges = capacityChanges.get(snapshotIdx) || [];
            const chartPoint = {
                snapshotIdx,
                timestamp,
                label: formatSnapshotLabel(timestamp),
                fill: Math.round(point.f * 100),
                enrollment: null,
                capacity: null,
                prevCapacity: null,
                capacityChanged: snapshotCapacityChanges.length > 0,
            };
            if (snapshotCapacityChanges.length > 0) {
                chartPoint.capacityChanges = snapshotCapacityChanges;
            }
            return [chartPoint];
        });
}

export function buildSectionChartPoints(section, snapshots) {
    let prevCapacity = null;
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
        };
        prevCapacity = point.c;
        return [chartPoint];
    });
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

export function getPhasedMapper(points, domain, milestones) {
    const chartPoints = points || [];
    const canonicalDomain = getFallbackDomain(chartPoints, domain);
    const domainTimes = getDomainTimes(canonicalDomain);

    if (!milestones || milestones.length < 2) {
        return {
            xValues: chartPoints.map(point => point.timestamp),
            domainXValues: domainTimes,
            mapTime: t => t,
        };
    }

    const mTimes = milestones.map(m => new Date(m.time).getTime()).filter(Number.isFinite).sort((a, b) => a - b);
    if (mTimes.length < 2) {
        return {
            xValues: chartPoints.map(point => point.timestamp),
            domainXValues: domainTimes,
            mapTime: t => t,
        };
    }

    const firstData = domainTimes.length ? domainTimes[0] : mTimes[0];
    const lastData = domainTimes.length ? domainTimes[domainTimes.length - 1] : mTimes[mTimes.length - 1];

    const allBounds = [Math.min(firstData, mTimes[0]), ...mTimes, Math.max(lastData, mTimes[mTimes.length - 1])];
    const bounds = getSortedUniqueNumbers(allBounds);
    const segCount = bounds.length - 1;
    if (segCount <= 0) {
        return {
            xValues: chartPoints.map(point => point.timestamp),
            domainXValues: domainTimes,
            mapTime: t => t,
        };
    }

    const segWidth = 100;
    const mapTime = (t) => {
        for (let s = 0; s < segCount; s++) {
            if (t <= bounds[s + 1]) {
                const frac = bounds[s + 1] === bounds[s] ? 0.5 : (t - bounds[s]) / (bounds[s + 1] - bounds[s]);
                return s * segWidth + frac * segWidth;
            }
        }
        return (segCount - 1) * segWidth + segWidth;
    };

    return {
        xValues: chartPoints.map(point => mapTime(point.timestamp)),
        domainXValues: domainTimes.map(mapTime),
        mapTime,
    };
}

export function getTimelineMapper(points, domain) {
    const chartPoints = points || [];
    const canonicalDomain = getFallbackDomain(chartPoints, domain);
    const domainTimes = getDomainTimes(canonicalDomain);

    if (domainTimes.length === 0) {
        return { xValues: [], domainXValues: [], mapTime: t => t };
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

    return {
        xValues: chartPoints.map(point => mapTime(point.timestamp)),
        domainXValues: domainTimes.map(mapTime),
        mapTime,
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
    };
}

export function getChartMapper(mode, points, domain, milestones) {
    if (mode === 'phased') return getPhasedMapper(points, domain, milestones);
    if (mode === 'snapshots') return getSnapshotsMapper(points, domain);
    return getTimelineMapper(points, domain);
}
