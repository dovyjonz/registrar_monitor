import test from 'node:test';
import assert from 'node:assert/strict';

import {
    formatPriorityCompact,
    formatPriorityFull,
    getCoursePublicState,
} from '../src/registrationSemantics.mjs';

test('uses the shared compact and full registration-priority vocabulary', () => {
    assert.equal(formatPriorityCompact('1', 'Y4+'), 'P1 · Y4+');
    assert.equal(formatPriorityFull('1', 'Y4+'), 'Priority 1 - Year 4+');
    assert.equal(formatPriorityFull('3', 'ALL'), 'Priority 3 - All students');
});

test('course cards preserve enrollment percentages above capacity', () => {
    assert.deepEqual(
        getCoursePublicState({ averageFill: 1.02, availability: { status: 'full' } }),
        {
            averageFill: 1.02,
            isFilled: false,
            registrationUnavailable: false,
            status: 'full',
            readout: '102%',
            accessibilityCopy: '102% full',
        },
    );
    assert.equal(
        getCoursePublicState({ averageFill: 1, availability: { status: 'full', sentence: 'No seats open.' } }).readout,
        'FULL',
    );
    assert.deepEqual(
        getCoursePublicState({
            averageFill: 1.02,
            availability: {
                status: 'required-type-full',
                compact: 'Labs full',
                sentence: 'Required lab sections have no seats open.',
            },
        }),
        {
            averageFill: 1.02,
            isFilled: true,
            registrationUnavailable: true,
            status: 'full',
            readout: 'FULL',
            accessibilityCopy: 'Labs full. Required lab sections have no seats open.',
        },
    );
});
