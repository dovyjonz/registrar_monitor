import test from 'node:test';
import assert from 'node:assert/strict';

import {
    formatPriorityCompact,
    formatPriorityFull,
} from '../src/registrationSemantics.mjs';

test('uses the shared compact and full registration-priority vocabulary', () => {
    assert.equal(formatPriorityCompact('1', 'Y4+'), 'P1 · Y4+');
    assert.equal(formatPriorityFull('1', 'Y4+'), 'Priority 1 — Year 4+');
    assert.equal(formatPriorityFull('3', 'ALL'), 'Priority 3 — All students');
});
