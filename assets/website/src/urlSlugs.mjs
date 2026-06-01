export function semesterToSlug(semester) {
    return semester.toLowerCase().replace(/\s+/g, '-');
}

export function courseToSlug(courseCode) {
    return courseCode
        .toLowerCase()
        .replace(/\s+/g, '-')
        .replace(/[/\\:*?"<>|]/g, '');
}

export function getEnrollmentJsonUrl(documentObject, windowObject) {
    return documentObject.body?.dataset?.jsonUrl || windowObject.JSON_URL || '';
}
