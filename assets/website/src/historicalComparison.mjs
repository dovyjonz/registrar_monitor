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
    '—',
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
        const fills = [];
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

            const fill = getFillAtOrBefore(
                getSectionHistory(course, sectionCode, section),
                snapshotIdx,
            );
            if (fill !== null) fills.push(fill);
        }

        const timestamp = getSnapshotTimestamp(snapshots[snapshotIdx]);
        if (fills.length === 0 || !timestamp) continue;
        const fillRatio = fills.reduce((total, fill) => total + fill, 0) / fills.length;
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
            contributingSections: fills.length,
            contributorCount: fills.length,
        });
    }
    return points.filter(point => Number.isFinite(point.timestamp));
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

export const normalizeProfessorIdentity = normalizeInstructorName;
export const buildProfessorChartPoints = buildProfessorAverageChartPoints;
export const normalizeHistoricalChartDomain = normalizeHistoricalDomain;
