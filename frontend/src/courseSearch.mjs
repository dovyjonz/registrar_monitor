function normalize(text) {
    return String(text ?? '').normalize('NFD').replace(/\p{M}/gu, '')
        .toLowerCase().replace(/\s+/g, ' ').trim();
}

function words(text) {
    return text.match(/[\p{L}\p{N}]+/gu) ?? [];
}

// At most one insertion, deletion, substitution, or adjacent transposition.
function nearWord(query, word) {
    if (query === word) return true;
    if (query.length < 4 || Math.abs(query.length - word.length) > 1) return false;
    let index = 0;
    while (query[index] === word[index] && index < Math.min(query.length, word.length)) index++;
    if (query.length === word.length) {
        return query.slice(index + 1) === word.slice(index + 1)
            || (query[index] === word[index + 1] && query[index + 1] === word[index]
                && query.slice(index + 2) === word.slice(index + 2));
    }
    return query.length > word.length
        ? query.slice(index + 1) === word.slice(index)
        : query.slice(index) === word.slice(index + 1);
}

function titleFragment(title = '', tokens) {
    if (title.length <= 32) return title;
    const titleWords = [...title.matchAll(/[\p{L}\p{N}]+/gu)];
    const anchor = [...tokens].sort((a, b) => b.length - a.length).map(token => (
        titleWords.find(word => normalize(word[0]).startsWith(token) || nearWord(token, normalize(word[0])))
    )).find(Boolean);
    const start = anchor?.index ?? 0;
    const fragment = title.slice(start, start + 48).trimEnd();
    return `${start > 0 ? '…' : ''}${fragment}${start + 48 < title.length ? '…' : ''}`;
}

export function searchCourses(courses, query) {
    const normalized = normalize(query);
    const tokens = words(normalized);
    if (!tokens.length) return [];
    const direct = text => tokens.every(token => words(text).some(word => word.startsWith(token)));
    return courses.flatMap(course => {
        const code = normalize(course.code);
        const fields = [
            { text: code, tier: code === normalized ? 0 : 1, reason: '' },
            { text: normalize(course.title), tier: 2, reason: titleFragment(course.title, tokens) },
            ...(course.instructors ?? []).map(name => ({
                text: normalize(name), tier: 3, reason: `Prof. ${name}`,
            })),
        ];
        let match = fields.find(field => direct(field.text));
        if (!match && tokens.some(token => token.length >= 4)) {
            const fuzzy = fields.find(field => tokens.every(token => (
                words(field.text).some(word => word.startsWith(token) || nearWord(token, word))
            )));
            if (fuzzy) match = { ...fuzzy, tier: 4, reason: fuzzy.reason || course.code };
        }
        return match ? [{ code: course.code, tier: match.tier, reason: match.reason }] : [];
    }).sort((a, b) => a.tier - b.tier
        || (normalize(a.code) < normalize(b.code) ? -1 : normalize(a.code) > normalize(b.code) ? 1 : 0));
}
