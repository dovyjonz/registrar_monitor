const COHORT_NAMES = new Map([['ALL', 'All students']]);

export function formatPriorityCompact(priority, label) {
    return `P${priority} · ${label}`;
}

export function formatPriorityFull(priority, label) {
    const cohort = COHORT_NAMES.get(label)
        || (String(label).startsWith('Y') ? `Year ${String(label).slice(1)}` : label);
    return `Priority ${priority} — ${cohort}`;
}
