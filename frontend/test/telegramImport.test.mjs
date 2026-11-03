import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
    buildTelegramImportText,
    telegramImportPresentation,
} from '../src/telegramImport.mjs';

describe('Telegram bookmark imports', () => {
    it('builds a portable deterministic paste command', () => {
        assert.equal(
            buildTelegramImportText('Fall 2026', ['MATH 101', 'CSCI 115']),
            '/import\nFall 2026\nCSCI 115\nMATH 101',
        );
    });

    it('uses compact singular and counted action labels', () => {
        assert.deepEqual(telegramImportPresentation(1), {
            label: 'Copy for bot',
            accessibleName: 'Copy 1 starred course for the Telegram bot',
        });
        assert.deepEqual(telegramImportPresentation(3), {
            label: 'Copy 3 for bot',
            accessibleName: 'Copy 3 starred courses for the Telegram bot',
        });
        assert.equal(telegramImportPresentation(0), null);
    });

    it('supports selections much larger than a deep-link payload', () => {
        const selection = buildTelegramImportText(
            'Fall 2026',
            Array.from({ length: 20 }, (_, index) => `COURSE ${100 + index}`),
        );
        assert.equal(selection.split('\n').length, 22);
    });
});
