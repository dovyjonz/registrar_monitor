import assert from 'node:assert/strict';
import test from 'node:test';

import {
    loadDepartmentPayload,
    loadSemesterManifest,
} from '../src/manifestData.mjs';

function response(body, ok = true, status = 200) {
    return { ok, status, json: async () => body };
}

test('startup fetches pointer, current manifest, and summary only', async () => {
    const requests = [];
    const fetchImpl = async (url, options = {}) => {
        requests.push({ url: String(url), cache: options.cache });
        if (String(url).endsWith('/manifest.json')) {
            return response({ current: 'manifests/current.json', previous: null });
        }
        if (String(url).endsWith('/manifests/current.json')) {
            return response({
                summary: { url: '../../blobs/summary.json' },
                departments: { CSCI: { url: '../../blobs/csci.json' } },
            });
        }
        if (String(url).endsWith('/blobs/summary.json')) {
            return response({ data: { cr: { 'CSCI 101': {} } }, milestones: [] });
        }
        throw new Error(`unexpected request ${url}`);
    };

    const loaded = await loadSemesterManifest(
        'https://example.test/data/summer-2026/manifest.json',
        { fetchImpl },
    );

    assert.equal(loaded.stale, false);
    assert.deepEqual(requests.map(item => item.url), [
        'https://example.test/data/summer-2026/manifest.json',
        'https://example.test/data/summer-2026/manifests/current.json',
        'https://example.test/data/blobs/summary.json',
    ]);
    assert.equal(requests[0].cache, 'no-cache');
});

test('broken current manifest falls back to declared previous manifest', async () => {
    const fetchImpl = async url => {
        const value = String(url);
        if (value.endsWith('/manifest.json')) {
            return response({
                current: 'manifests/broken.json',
                previous: 'manifests/previous.json',
            });
        }
        if (value.endsWith('/manifests/broken.json')) {
            return response({}, false, 503);
        }
        if (value.endsWith('/manifests/previous.json')) {
            return response({ summary: { url: '../../blobs/previous.json' } });
        }
        if (value.endsWith('/blobs/previous.json')) {
            return response({ data: { sem: 'Summer 2026', cr: {} } });
        }
        throw new Error(`unexpected request ${url}`);
    };

    const loaded = await loadSemesterManifest(
        'https://example.test/data/summer-2026/manifest.json',
        { fetchImpl },
    );

    assert.equal(loaded.stale, true);
    assert.match(loaded.manifestUrl, /previous\.json$/);
});

test('department payload is promise-cached and fetched once', async () => {
    let fetchCount = 0;
    const fetchImpl = async () => {
        fetchCount += 1;
        return response({ courses: { 'CSCI 101': { ah: [] } } });
    };
    const cache = new Map();
    const manifest = {
        departments: { CSCI: { url: '../../blobs/csci.json' } },
    };
    const manifestUrl =
        'https://example.test/data/summer-2026/manifests/current.json';

    const [first, second] = await Promise.all([
        loadDepartmentPayload('CSCI', manifest, manifestUrl, cache, { fetchImpl }),
        loadDepartmentPayload('CSCI', manifest, manifestUrl, cache, { fetchImpl }),
    ]);

    assert.equal(fetchCount, 1);
    assert.equal(first, second);
});
