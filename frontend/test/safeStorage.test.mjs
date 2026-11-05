import test from 'node:test';
import assert from 'node:assert/strict';

import {
    readStoredString,
    readStoredStringArray,
    writeStoredJson,
    writeStoredString,
} from '../src/safeStorage.mjs';

function memoryStorage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return {
        getItem: key => (data.has(key) ? data.get(key) : null),
        setItem: (key, value) => data.set(key, value),
    };
}

const blockedStorage = {
    getItem() { throw new DOMException('blocked', 'SecurityError'); },
    setItem() { throw new DOMException('quota', 'QuotaExceededError'); },
};

test('string arrays tolerate corrupt JSON, non-arrays, and blocked storage', () => {
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage({ k: '["A 1","B 2"]' }) }), ['A 1', 'B 2']);
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage({ k: '{bad' }) }), []);
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage({ k: '{"a":1}' }) }), []);
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage({ k: 'null' }) }), []);
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage({ k: '["A 1",3,null]' }) }), ['A 1']);
    assert.deepEqual(readStoredStringArray('k', { storage: memoryStorage() }), []);
    assert.deepEqual(readStoredStringArray('k', { storage: blockedStorage }), []);
    assert.deepEqual(readStoredStringArray('k', { storage: null }), []);
});

test('strings fall back when missing, empty, disallowed, or blocked', () => {
    assert.equal(readStoredString('m', 'phased', { storage: memoryStorage({ m: 'timeline' }) }), 'timeline');
    assert.equal(readStoredString('m', 'phased', { storage: memoryStorage() }), 'phased');
    assert.equal(readStoredString('m', 'phased', { storage: memoryStorage({ m: '' }) }), 'phased');
    assert.equal(
        readStoredString('m', 'phased', { storage: memoryStorage({ m: 'bogus' }), allowed: ['phased', 'timeline'] }),
        'phased',
    );
    assert.equal(readStoredString('m', 'phased', { storage: blockedStorage }), 'phased');
});

test('writes report failure instead of throwing', () => {
    const storage = memoryStorage();
    assert.equal(writeStoredString('m', 'steps', { storage }), true);
    assert.equal(storage.getItem('m'), 'steps');
    assert.equal(writeStoredJson('b', ['A 1'], { storage }), true);
    assert.equal(storage.getItem('b'), '["A 1"]');
    assert.equal(writeStoredString('m', 'x', { storage: blockedStorage }), false);
    assert.equal(writeStoredJson('b', [], { storage: blockedStorage }), false);
    assert.equal(writeStoredString('m', 'x', { storage: null }), false);
});
