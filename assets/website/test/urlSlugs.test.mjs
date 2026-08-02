import test from 'node:test';
import assert from 'node:assert/strict';

import {
    courseToSlug,
    getManifestUrl,
    semesterToSlug,
} from '../src/urlSlugs.mjs';

test('semester slugs match generated share-page directories', () => {
    assert.equal(semesterToSlug('Summer 2026'), 'summer-2026');
});

test('course slugs remove filesystem-unsafe characters like the Python generator', () => {
    assert.equal(courseToSlug('ANT 214/SOC 214'), 'ant-214soc-214');
    assert.equal(courseToSlug('CSCI 101'), 'csci-101');
});

test('manifest URL reads only the generated body pointer', () => {
    assert.equal(
        getManifestUrl({ body: { dataset: {} } }),
        '',
    );
    assert.equal(
        getManifestUrl(
            { body: { dataset: { manifestUrl: 'data/spring-2026/manifest.json' } } },
        ),
        'data/spring-2026/manifest.json',
    );
});
