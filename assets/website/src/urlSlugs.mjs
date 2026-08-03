export function semesterToSlug(semester) {
    return semester.toLowerCase().replace(/\s+/g, '-');
}

/**
 * Put the prior year's matching term first when looking for historical data.
 * The caller still controls which semesters are eligible; this only changes
 * the order of the supplied candidates.
 */
export function prioritizeHistoricalSemesters(currentSemester, semesters) {
    const candidates = Array.isArray(semesters) ? [...semesters] : [];
    const match = /^(Fall|Spring|Summer)\s+(\d{4})$/iu.exec(
        String(currentSemester || '').trim(),
    );
    if (!match) return candidates;

    const priorSemester = `${match[1]} ${Number(match[2]) - 1}`.toLocaleLowerCase();
    const priorIndex = candidates.findIndex(semester => (
        String(semester).trim().toLocaleLowerCase() === priorSemester
    ));
    if (priorIndex <= 0) return candidates;

    return [
        candidates[priorIndex],
        ...candidates.slice(0, priorIndex),
        ...candidates.slice(priorIndex + 1),
    ];
}

export function courseToSlug(courseCode) {
    return courseCode
        .toLowerCase()
        .replace(/\s+/g, '-')
        .replace(/[/\\:*?"<>|]/g, '');
}

export function getManifestUrl(documentObject) {
    return documentObject.body?.dataset?.manifestUrl || '';
}
