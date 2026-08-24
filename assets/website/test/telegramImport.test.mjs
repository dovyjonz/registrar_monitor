import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
    buildTelegramImportText,
} from '../src/telegramImport.mjs';

describe('Telegram bookmark imports', () => {
    it('builds a portable deterministic paste command', () => {
        assert.equal(
            buildTelegramImportText('Fall 2026', ['MATH 101', 'CSCI 115']),
            '/import\nFall 2026\nCSCI 115\nMATH 101',
        );
    });

    it('supports selections much larger than a deep-link payload', () => {
        const selection = buildTelegramImportText(
            'Fall 2026',
            Array.from({ length: 20 }, (_, index) => `COURSE ${100 + index}`),
        );
        assert.equal(selection.split('\n').length, 22);
    });
});
