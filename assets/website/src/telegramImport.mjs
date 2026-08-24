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

export function telegramImportPresentation(count) {
    if (!Number.isInteger(count) || count < 1) return null;
    return {
        label: count === 1 ? 'Copy for bot' : `Copy ${count} for bot`,
        accessibleName: count === 1
            ? 'Copy 1 starred course for the Telegram bot'
            : `Copy ${count} starred courses for the Telegram bot`,
    };
}
