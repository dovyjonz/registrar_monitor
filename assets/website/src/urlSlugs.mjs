export function semesterToSlug(semester) {
    return semester.toLowerCase().replace(/\s+/g, '-');
}

/**
 * Exclude semesters with incompatible registration behavior, then put the
 * prior year's matching term first. Summer compares only with Summer; Fall
 * and Spring may compare with each other but never with Summer.
 */
export function prioritizeHistoricalSemesters(currentSemester, semesters) {
    const candidates = Array.isArray(semesters) ? [...semesters] : [];
    const match = /^(Fall|Spring|Summer)\s+(\d{4})$/iu.exec(
        String(currentSemester || '').trim(),
    );
    if (!match) return candidates;

    const currentTerm = match[1].toLocaleLowerCase();
    const eligibleCandidates = candidates.filter((semester) => {
        const candidateMatch = /^(Fall|Spring|Summer)\s+\d{4}$/iu.exec(
            String(semester || '').trim(),
        );
        if (!candidateMatch) return false;
        const candidateTerm = candidateMatch[1].toLocaleLowerCase();
        return currentTerm === 'summer'
            ? candidateTerm === 'summer'
            : candidateTerm !== 'summer';
    });

    const priorSemester = `${match[1]} ${Number(match[2]) - 1}`.toLocaleLowerCase();
    const priorIndex = eligibleCandidates.findIndex(semester => (
        String(semester).trim().toLocaleLowerCase() === priorSemester
    ));
    if (priorIndex <= 0) return eligibleCandidates;

    return [
        eligibleCandidates[priorIndex],
        ...eligibleCandidates.slice(0, priorIndex),
        ...eligibleCandidates.slice(priorIndex + 1),
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
