const COHORT_NAMES = new Map([['ALL', 'All students']]);

export function formatPriorityCompact(priority, label) {
    return `P${priority} · ${label}`;
}

export function formatPriorityFull(priority, label) {
    const cohort = COHORT_NAMES.get(label)
        || (String(label).startsWith('Y') ? `Year ${String(label).slice(1)}` : label);
    return `Priority ${priority} - ${cohort}`;
}

function getAverageFill(course) {
    const fill = course?.averageFill ?? course?.af;
    return Number.isFinite(fill) ? fill : 0;
}

function getAccessibilityCopy({
    availability,
    ordinaryFull,
    overCapacity,
    percentage,
    registrationUnavailable,
}) {
    if (registrationUnavailable) {
        return `${availability.compact}. ${availability.sentence}`;
    }
    if (overCapacity) return `${percentage} full`;
    if (ordinaryFull) return `FULL. ${availability.sentence}`;
    return `${percentage} full`;
}

export function getCoursePublicState(course) {
    const averageFill = getAverageFill(course);
    const availability = course?.availability || course?.previewState?.availability || null;
    const registrationUnavailable = availability?.status === 'required-type-full';
    const ordinaryFull = availability?.status === 'full';
    const isFilled = Boolean(course?.isFilled ?? course?.if) || registrationUnavailable;
    const overCapacity = averageFill > 1;
    const percentage = `${Math.round(averageFill * 100)}%`;
    return {
        averageFill,
        isFilled,
        registrationUnavailable,
        status: isFilled || averageFill >= 1 ? 'full' : averageFill >= 0.8 ? 'near' : 'open',
        readout: registrationUnavailable || (!overCapacity && ordinaryFull) ? 'FULL' : percentage,
        accessibilityCopy: getAccessibilityCopy({
            availability,
            ordinaryFull,
            overCapacity,
            percentage,
            registrationUnavailable,
        }),
    };
}
