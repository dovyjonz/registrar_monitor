/**
 * Pure helpers for reconstructing historical professor comparisons.
 *
 * The frontend adapter exposes compact v3 department payloads, but the
 * helpers also accept the verbose field names used by the source contract so
 * the calculation remains easy to exercise independently.
 */

const PLACEHOLDER_INSTRUCTOR_NAMES = new Set([
    'tba',
    'tba tba',
    'tbd',
    'n/a',
    'na',
    'unknown',
    'not available',
    'to be announced',
    '-',
    '–',
    '\u2014',
]);

export function normalizeInstructorName(value) {
    if (typeof value !== 'string') return '';
    const normalized = value
        .normalize('NFKC')
        .trim()
        .replace(/\s+/gu, ' ')
        .toLowerCase();
    if (
        !normalized
        || PLACEHOLDER_INSTRUCTOR_NAMES.has(normalized)
        || /^tba\d*(?: tba\d*)?$/u.test(normalized)
    ) return '';
    return normalized;
}

function getCourseSections(course) {
    return course?.sections || course?.s || {};
}

function getSectionHistory(course, sectionCode, section) {
    return section?.history || section?.h || course?.sectionHistory?.[sectionCode] || [];
}

function getCourseEvents(course, events) {
    return Array.isArray(events) ? events : (course?.events || course?.ev || []);
}

function getEventType(event) {
    return event?.eventType || event?.et || '';
}

function getEventSectionCode(event) {
    return event?.sectionCode || event?.sc || '';
}

function getEventSnapshotIndex(event) {
    const index = event?.timestampIdx ?? event?.snapshotIdx ?? event?.i;
    return Number.isInteger(index) && index >= 0 ? index : null;
}

function getEventOldValue(event) {
    return event?.oldValue ?? event?.ov;
}

function getEventNewValue(event) {
    return event?.newValue ?? event?.nv;
}

function getHistorySnapshotIndex(point) {
    const index = point?.timestampIdx ?? point?.snapshotIdx ?? point?.i;
    return Number.isInteger(index) && index >= 0 ? index : null;
}

function getHistoryFill(point) {
    const fill = point?.fill ?? point?.f;
    return Number.isFinite(fill) ? fill : null;
}

function getSnapshotTimestamp(snapshot) {
    if (typeof snapshot === 'string') return snapshot;
    return snapshot?.ts || snapshot?.timestamp || snapshot?.observedAt || null;
}

function formatSnapshotLabel(timestamp) {
    const parsed = new Date(timestamp).getTime();
    if (!Number.isFinite(parsed)) return '';
    return new Date(parsed).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function getSectionEvents(events, sectionCode, types = null) {
    return getCourseEvents(null, events)
        .filter(event => getEventSectionCode(event) === sectionCode)
        .filter(event => !types || types.has(getEventType(event)))
        .map(event => ({ event, snapshotIdx: getEventSnapshotIndex(event) }))
        .filter(item => item.snapshotIdx !== null)
        .sort((first, second) => first.snapshotIdx - second.snapshotIdx);
}

function getFirstHistoryIndex(history) {
    return history
        .map(getHistorySnapshotIndex)
        .filter(Number.isInteger)
        .sort((first, second) => first - second)[0] ?? null;
}

function getLastKnownHistoryIndex(course, sectionCode, section) {
    return getSectionHistory(course, sectionCode, section)
        .map(getHistorySnapshotIndex)
        .filter(Number.isInteger)
        .sort((first, second) => second - first)[0] ?? null;
}

/**
 * Reconstruct active intervals for one section. An interval's end is
 * exclusive, so a section removed at snapshot N does not contribute at N.
 */
export function buildSectionActivityTimeline(
    sectionCode,
    section,
    events = [],
    snapshots = [],
    course = null,
) {
    const history = getSectionHistory(course, sectionCode, section);
    const additions = getSectionEvents(
        events,
        sectionCode,
        new Set(['section_added']),
    );
    const removals = getSectionEvents(
        events,
        sectionCode,
        new Set(['section_removed']),
    );
    const firstHistoryIdx = getFirstHistoryIndex(history);
    const fallbackEnd = Math.max(
        Array.isArray(snapshots) && snapshots.length > 0 ? snapshots.length : 0,
        (getLastKnownHistoryIndex(course, sectionCode, section) ?? -1) + 1,
        ...[...additions, ...removals].map(({ snapshotIdx }) => snapshotIdx + 1),
        1,
    );
    const intervals = [];

    const transitions = [
        ...additions.map(({ snapshotIdx }) => ({ snapshotIdx, kind: 'added' })),
        ...removals.map(({ snapshotIdx }) => ({ snapshotIdx, kind: 'removed' })),
    ].sort((first, second) => (
        first.snapshotIdx - second.snapshotIdx
        || (first.kind === 'added' ? -1 : 1)
    ));
    const firstTransitionIdx = transitions[0]?.snapshotIdx ?? null;
    let active = firstHistoryIdx !== null
        && (firstTransitionIdx === null || firstHistoryIdx <= firstTransitionIdx);
    let openStart = active ? firstHistoryIdx : null;

    for (const { snapshotIdx, kind } of transitions) {
        if (kind === 'added') {
            if (!active) {
                active = true;
                openStart = snapshotIdx;
            }
            continue;
        }
        if (active && openStart !== null) {
            intervals.push({ start: openStart, end: snapshotIdx });
        }
        active = false;
        openStart = null;
    }
    if (active && openStart !== null) {
        intervals.push({ start: openStart, end: fallbackEnd });
    }

    return {
        intervals,
        isActiveAt(snapshotIdx) {
            return intervals.some(({ start, end }) => (
                snapshotIdx >= start && snapshotIdx < end
            ));
        },
    };
}

function getCurrentInstructor(section) {
    return section?.instructor ?? section?.in ?? '';
}

function getLatestKnownInstructor(sectionCode, events) {
    const instructorEvents = getSectionEvents(
        events,
        sectionCode,
        new Set(['instructor_changed']),
    );
    const latestEvent = instructorEvents.at(-1)?.event;
    return latestEvent ? normalizeInstructorName(getEventNewValue(latestEvent)) : '';
}

/**
 * Return the instructor assigned at a snapshot. Events are walked backward
 * from the current/last-known value so an old instructor is never applied
 * retroactively after a change event.
 */
export function getInstructorAtSnapshot(
    sectionCode,
    section,
    events = [],
    snapshotIdx,
) {
    const instructorEvents = getSectionEvents(
        events,
        sectionCode,
        new Set(['instructor_changed']),
    ).toReversed();
    let assignment = normalizeInstructorName(getCurrentInstructor(section));
    if (!assignment) assignment = getLatestKnownInstructor(sectionCode, events);

    for (const { event, snapshotIdx: eventIdx } of instructorEvents) {
        if (eventIdx <= snapshotIdx) break;
        const oldAssignment = normalizeInstructorName(getEventOldValue(event));
        if (!oldAssignment) return '';
        assignment = oldAssignment;
    }
    return assignment;
}

/**
 * Build the reconstructed instructor assignment at each relevant snapshot.
 */
export function buildInstructorAssignmentTimeline(
    sectionCode,
    section,
    events = [],
    snapshotIndexes = [],
) {
    return [...new Set(snapshotIndexes)]
        .filter(Number.isInteger)
        .sort((first, second) => first - second)
        .map(snapshotIdx => ({
            snapshotIdx,
            instructor: getInstructorAtSnapshot(
                sectionCode,
                section,
                events,
                snapshotIdx,
            ),
        }));
}

function getSectionCodes(course, events) {
    const sectionCodes = new Set([
        ...Object.keys(getCourseSections(course)),
        ...Object.keys(course?.sectionHistory || {}),
    ]);
    for (const event of getCourseEvents(course, events)) {
        const sectionCode = getEventSectionCode(event);
        if (sectionCode) sectionCodes.add(sectionCode);
    }
    return [...sectionCodes].sort();
}

function getRelevantSnapshotIndexes(course, sectionCodes, events, snapshots) {
    const indexes = new Set();
    const maxIndex = Array.isArray(snapshots) && snapshots.length > 0
        ? snapshots.length - 1
        : Number.POSITIVE_INFINITY;
    for (const sectionCode of sectionCodes) {
        const section = getCourseSections(course)[sectionCode];
        for (const point of getSectionHistory(course, sectionCode, section)) {
            const snapshotIdx = getHistorySnapshotIndex(point);
            if (snapshotIdx !== null && snapshotIdx <= maxIndex) indexes.add(snapshotIdx);
        }
    }
    for (const event of getCourseEvents(course, events)) {
        const snapshotIdx = getEventSnapshotIndex(event);
        if (snapshotIdx !== null && snapshotIdx <= maxIndex) indexes.add(snapshotIdx);
    }
    return [...indexes].sort((first, second) => first - second);
}

function getFillAtOrBefore(sectionHistory, snapshotIdx) {
    let latestFill = null;
    const points = [...sectionHistory]
        .map(point => ({ point, snapshotIdx: getHistorySnapshotIndex(point) }))
        .filter(item => item.snapshotIdx !== null && item.snapshotIdx <= snapshotIdx)
        .sort((first, second) => first.snapshotIdx - second.snapshotIdx);
    for (const { point } of points) {
        const fill = getHistoryFill(point);
        if (fill !== null) latestFill = fill;
    }
    return latestFill;
}

function getEnrollmentStateAtOrBefore(sectionHistory, snapshotIdx) {
    let latest = null;
    for (const point of sectionHistory || []) {
        const pointIndex = getHistorySnapshotIndex(point);
        const enrollment = point?.enrollment ?? point?.e;
        const capacity = point?.capacity ?? point?.c;
        if (pointIndex === null || pointIndex > snapshotIdx) continue;
        if (Number.isFinite(enrollment) && Number.isFinite(capacity)) {
            latest = { enrollment, capacity };
        }
    }
    return latest;
}

function getOpeningCapacity(sectionHistory) {
    for (const point of sectionHistory || []) {
        const capacity = point?.capacity ?? point?.c;
        if (Number.isFinite(capacity) && capacity > 0) return capacity;
    }
    return null;
}

function sectionSeriesEndsByRemoval(
    sectionCode,
    events,
    finalSnapshotIdx,
    professor,
    section,
) {
    const transitions = getSectionEvents(
        events,
        sectionCode,
        new Set(['section_removed', 'instructor_changed']),
    ).filter(({ snapshotIdx }) => snapshotIdx > finalSnapshotIdx);
    for (const { event, snapshotIdx } of transitions) {
        if (getEventType(event) === 'section_removed') return true;
        if (getInstructorAtSnapshot(sectionCode, section, events, snapshotIdx) !== professor) {
            return false;
        }
    }
    return false;
}

/**
 * Build an equal-weight professor fill series. The returned fill is the
 * displayed whole percentage; fillRatio preserves the exact mean for callers
 * that need the unrounded value.
 */
export function buildProfessorAverageChartPoints(
    course,
    selectedProfessor,
    snapshots,
    events = null,
) {
    const professor = normalizeInstructorName(selectedProfessor);
    if (!course || !professor || !Array.isArray(snapshots)) return [];

    const courseEvents = getCourseEvents(course, events);
    const sectionCodes = getSectionCodes(course, courseEvents);
    const relevantIndexes = getRelevantSnapshotIndexes(
        course,
        sectionCodes,
        courseEvents,
        snapshots,
    );
    const sections = getCourseSections(course);
    const points = [];

    for (const snapshotIdx of relevantIndexes) {
        const contributions = [];
        for (const sectionCode of sectionCodes) {
            const section = sections[sectionCode];
            const activity = buildSectionActivityTimeline(
                sectionCode,
                section,
                courseEvents,
                snapshots,
                course,
            );
            if (!activity.isActiveAt(snapshotIdx)) continue;

            const instructor = getInstructorAtSnapshot(
                sectionCode,
                section,
                courseEvents,
                snapshotIdx,
            );
            if (instructor !== professor) continue;

            const history = getSectionHistory(course, sectionCode, section);
            const fill = getFillAtOrBefore(history, snapshotIdx);
            const state = getEnrollmentStateAtOrBefore(history, snapshotIdx);
            const openingCapacity = getOpeningCapacity(history);
            if (fill !== null) {
                contributions.push({ sectionCode, fill, state, openingCapacity });
            }
        }

        const timestamp = getSnapshotTimestamp(snapshots[snapshotIdx]);
        if (contributions.length === 0 || !timestamp) continue;
        const fillRatio = contributions.reduce((total, value) => total + value.fill, 0)
            / contributions.length;
        const indexed = contributions.filter(value => value.state && value.openingCapacity);
        const averageLevel = key => indexed.length > 0
            ? indexed.reduce((total, value) => (
                total + (value.state[key] / value.openingCapacity) * 100
            ), 0) / indexed.length
            : null;
        points.push({
            snapshotIdx,
            timestamp: new Date(timestamp).getTime(),
            label: formatSnapshotLabel(timestamp),
            fill: Math.round(fillRatio * 100),
            fillRatio,
            averageFill: fillRatio,
            enrollment: null,
            capacity: null,
            capacityChanged: false,
            enrollmentLevel: averageLevel('enrollment') ?? fillRatio * 100,
            capacityLevel: averageLevel('capacity') ?? 100,
            contributingSections: contributions.length,
            contributorCount: contributions.length,
            contributingSectionCodes: contributions.map(value => value.sectionCode),
        });
    }
    const chartPoints = points.filter(point => Number.isFinite(point.timestamp));
    const finalPoint = chartPoints.at(-1);
    if (finalPoint && finalPoint.contributingSectionCodes.length > 0) {
        const endedByRemoval = finalPoint.contributingSectionCodes.every(sectionCode => (
            sectionSeriesEndsByRemoval(
                sectionCode,
                courseEvents,
                finalPoint.snapshotIdx,
                professor,
                sections[sectionCode],
            )
        ));
        if (endedByRemoval) finalPoint.removalEnded = true;
    }
    for (const point of chartPoints) delete point.contributingSectionCodes;
    return chartPoints;
}

export function courseHasProfessor(course, selectedProfessor, snapshots, events = null) {
    const professor = normalizeInstructorName(selectedProfessor);
    if (!course || !professor) return false;
    const courseEvents = getCourseEvents(course, events);
    const sections = getCourseSections(course);
    const sectionCodes = getSectionCodes(course, courseEvents);
    const relevantIndexes = getRelevantSnapshotIndexes(
        course,
        sectionCodes,
        courseEvents,
        snapshots,
    );
    for (const sectionCode of sectionCodes) {
        const section = sections[sectionCode];
        if (normalizeInstructorName(getCurrentInstructor(section)) === professor) return true;
        if (normalizeInstructorName(getLatestKnownInstructor(sectionCode, courseEvents)) === professor) return true;
        for (const snapshotIdx of relevantIndexes) {
            if (getInstructorAtSnapshot(sectionCode, section, courseEvents, snapshotIdx) === professor) {
                return true;
            }
        }
    }
    return false;
}

/**
 * Scale a historical mapped x-series into the current mapped x-domain.
 * Single-point or degenerate source domains are anchored at the target start.
 */
export function normalizeHistoricalDomain(
    historicalXValues,
    historicalDomainXValues,
    currentDomainXValues,
) {
    const values = (historicalXValues || []).map(Number).filter(Number.isFinite);
    const source = (historicalDomainXValues || []).map(Number).filter(Number.isFinite);
    const target = (currentDomainXValues || []).map(Number).filter(Number.isFinite);
    if (values.length === 0 || source.length === 0 || target.length === 0) return [];

    const sourceMin = Math.min(...source);
    const sourceMax = Math.max(...source);
    const targetMin = Math.min(...target);
    const targetMax = Math.max(...target);
    if (values.length === 1 || sourceMax <= sourceMin || targetMax <= targetMin) {
        return values.map(() => targetMin);
    }
    return values.map(value => (
        targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin)
    ));
}

function getMilestoneTime(milestone) {
    const time = new Date(milestone?.time).getTime();
    return Number.isFinite(time) ? time : null;
}

function getMilestoneLabel(milestone) {
    return typeof milestone?.label === 'string'
        ? milestone.label.trim().toLocaleLowerCase()
        : '';
}

function getOrderedMilestones(milestones) {
    return (Array.isArray(milestones) ? milestones : [])
        .map((milestone, index) => ({
            milestone,
            index,
            time: getMilestoneTime(milestone),
            label: getMilestoneLabel(milestone),
        }))
        .filter(item => item.time !== null && item.label)
        .sort((first, second) => first.time - second.time || first.index - second.index);
}

/**
 * Pair historical and current milestones by label and occurrence order.
 * Matching by label keeps an older term aligned when it has fewer deadlines.
 */
export function getHistoricalMilestoneAlignment({
    historicalMilestones = [],
    currentMilestones = [],
    historicalMapTime = value => value,
    currentMapTime = value => value,
} = {}) {
    const currentByKey = new Map();
    const currentOccurrences = new Map();
    for (const item of getOrderedMilestones(currentMilestones)) {
        const occurrence = currentOccurrences.get(item.label) || 0;
        currentOccurrences.set(item.label, occurrence + 1);
        const key = `${item.label}\u0000${occurrence}`;
        currentByKey.set(key, item);
    }

    const alignment = [];
    const historicalOccurrences = new Map();
    for (const item of getOrderedMilestones(historicalMilestones)) {
        const occurrence = historicalOccurrences.get(item.label) || 0;
        historicalOccurrences.set(item.label, occurrence + 1);
        const current = currentByKey.get(`${item.label}\u0000${occurrence}`);
        if (!current) continue;

        const historicalX = Number(historicalMapTime(item.time));
        const currentX = Number(currentMapTime(current.time));
        if (!Number.isFinite(historicalX) || !Number.isFinite(currentX)) continue;
        alignment.push({
            historical: item.milestone,
            current: current.milestone,
            historicalX,
            currentX,
        });
    }
    return alignment;
}

/**
 * Map a historical chart's x-coordinates into the current chart. Matching
 * milestone coordinates are used as piecewise anchors from the shared domain
 * origin. After the last shared anchor, the final segment extrapolates without
 * forcing the historical endpoint onto the current endpoint. Terms with no
 * matching milestones retain regular endpoint-to-endpoint scaling.
 */
export function createHistoricalCoordinateMapper({
    historicalDomainXValues = [],
    currentDomainXValues = [],
    historicalMilestones = [],
    currentMilestones = [],
    historicalMapTime = value => value,
    currentMapTime = value => value,
} = {}) {
    const source = [...new Set((historicalDomainXValues || [])
        .map(Number)
        .filter(Number.isFinite))].sort((first, second) => first - second);
    const target = [...new Set((currentDomainXValues || [])
        .map(Number)
        .filter(Number.isFinite))].sort((first, second) => first - second);
    if (source.length === 0 || target.length === 0) {
        return { mapX: () => Number.NaN, anchors: [] };
    }

    const sourceMin = source[0];
    const sourceMax = source.at(-1);
    const targetMin = target[0];
    const targetMax = target.at(-1);
    const fallbackMap = value => {
        if (!Number.isFinite(value)) return Number.NaN;
        if (sourceMax <= sourceMin || targetMax <= targetMin) return targetMin;
        return targetMin + ((value - sourceMin) / (sourceMax - sourceMin))
            * (targetMax - targetMin);
    };

    if (sourceMax <= sourceMin || targetMax <= targetMin) {
        return {
            mapX: fallbackMap,
            anchors: [],
            sourceDomain: source,
            targetDomain: target,
        };
    }

    const anchors = getHistoricalMilestoneAlignment({
        historicalMilestones,
        currentMilestones,
        historicalMapTime,
        currentMapTime,
    });
    const pointsBySource = new Map([[sourceMin, targetMin]]);
    for (const anchor of anchors) {
        pointsBySource.set(anchor.historicalX, anchor.currentX);
    }
    const candidatePoints = [...pointsBySource.entries()]
        .sort(([first], [second]) => first - second)
        .map(([sourceX, targetX]) => ({ sourceX, targetX }));
    // A current term can stop before future milestone anchors while the
    // historical term already spans the full semester. In that case the
    // sourceMax -> targetMax endpoint would sit after those anchors in source
    // time but before them in target time, causing the rendered line to fold
    // backward. Keep only anchors that preserve chronological x-order; the
    // final valid segment naturally extrapolates beyond the last anchor.
    const points = candidatePoints.reduce((chronological, point) => {
        if (chronological.length === 0 || point.targetX >= chronological.at(-1).targetX) {
            chronological.push(point);
        }
        return chronological;
    }, []);

    if (anchors.length === 0 || points.length < 2) {
        return {
            mapX: fallbackMap,
            anchors,
            sourceDomain: source,
            targetDomain: target,
        };
    }

    const mapX = value => {
        const numericValue = Number(value);
        if (!Number.isFinite(numericValue)) return Number.NaN;
        if (points.length < 2) return fallbackMap(numericValue);

        let left = points[0];
        let right = points[1];
        if (numericValue <= left.sourceX) {
            right = points[1];
        } else {
            for (let index = 1; index < points.length; index++) {
                right = points[index];
                left = points[index - 1];
                if (numericValue <= right.sourceX) break;
            }
        }
        const sourceRange = right.sourceX - left.sourceX;
        if (sourceRange <= 0) return right.targetX;
        return left.targetX + ((numericValue - left.sourceX) / sourceRange)
            * (right.targetX - left.targetX);
    };

    return {
        mapX,
        anchors,
        sourceDomain: source,
        targetDomain: target,
    };
}

export const normalizeProfessorIdentity = normalizeInstructorName;
export const buildProfessorChartPoints = buildProfessorAverageChartPoints;
export const normalizeHistoricalChartDomain = normalizeHistoricalDomain;
