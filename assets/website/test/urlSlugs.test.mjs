import test from 'node:test';
import assert from 'node:assert/strict';

import {
    courseToSlug,
    getEnrollmentJsonUrl,
    semesterToSlug,
} from '../src/urlSlugs.mjs';

test('semester slugs match generated share-page directories', () => {
    assert.equal(semesterToSlug('Summer 2026'), 'summer-2026');
});

test('course slugs remove filesystem-unsafe characters like the Python generator', () => {
    assert.equal(courseToSlug('ANT 214/SOC 214'), 'ant-214soc-214');
    assert.equal(courseToSlug('CSCI 101'), 'csci-101');
});

test('enrollment JSON URL keeps the legacy window fallback for patched pages', () => {
    assert.equal(
        getEnrollmentJsonUrl({ body: { dataset: {} } }, { JSON_URL: 'summer2026.json' }),
        'summer2026.json',
    );
    assert.equal(
        getEnrollmentJsonUrl(
            { body: { dataset: { jsonUrl: 'spring2026.json' } } },
            { JSON_URL: 'summer2026.json' },
        ),
        'spring2026.json',
    );
});
