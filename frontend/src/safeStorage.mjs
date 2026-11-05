// localStorage can throw (blocked storage, private windows) or hold corrupt
// values. Every helper degrades to a fallback instead of breaking the app.

function defaultStorage() {
    try {
        return globalThis.localStorage ?? null;
    } catch {
        return null;
    }
}

/** Read a string value; return `fallback` when missing, invalid, or not allowed. */
export function readStoredString(key, fallback, options = {}) {
    const { allowed, storage = defaultStorage() } = options;
    try {
        const value = storage?.getItem(key);
        if (typeof value !== 'string' || value === '') return fallback;
        if (allowed && !allowed.includes(value)) return fallback;
        return value;
    } catch {
        return fallback;
    }
}

/** Read a JSON array of strings; non-string entries are dropped, anything else yields []. */
export function readStoredStringArray(key, options = {}) {
    const { storage = defaultStorage() } = options;
    try {
        const raw = storage?.getItem(key);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        return parsed.filter(item => typeof item === 'string');
    } catch {
        return [];
    }
}

/** Write a string; returns whether the write succeeded. */
export function writeStoredString(key, value, options = {}) {
    const { storage = defaultStorage() } = options;
    try {
        if (!storage) return false;
        storage.setItem(key, String(value));
        return true;
    } catch {
        return false;
    }
}

/** Write a JSON-serializable value; returns whether the write succeeded. */
export function writeStoredJson(key, value, options) {
    let serialized;
    try {
        serialized = JSON.stringify(value);
    } catch {
        return false;
    }
    return writeStoredString(key, serialized, options);
}
