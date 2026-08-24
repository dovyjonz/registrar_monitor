export function buildTelegramImportText(semester, courseCodes) {
    if (!/^(Fall|Spring|Summer) \d{4}$/.test(semester)) {
        throw new Error('Invalid semester');
    }
    const courses = [...new Set(courseCodes.map(code => code.trim()).filter(Boolean))].sort();
    if (!courses.length || courses.some(code => code.includes('\n') || code.length > 80)) {
        throw new Error('No valid bookmarked courses');
    }
    return ['/import', semester, ...courses].join('\n');
}
