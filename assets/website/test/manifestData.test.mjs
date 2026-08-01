import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import test from 'node:test';

import {
    IntegrityError,
    UnsupportedSchemaError,
    loadDepartmentPayload,
    loadSemesterManifest,
} from '../src/manifestData.mjs';

const SEMESTER = 'Summer 2026';
const textEncoder = new TextEncoder();

function jsonResponse(body, ok = true, status = 200) {
    return { ok, status, json: async () => body };
}

function bytesFor(body) {
    return typeof body === 'string'
        ? textEncoder.encode(body)
        : textEncoder.encode(JSON.stringify(body));
}

function bytesResponse(artifact, ok = true, status = 200) {
    return {
        ok,
        status,
        arrayBuffer: async () => artifact.bytes.slice().buffer,
    };
}

async function sha256Hex(bytes) {
    const digest = await webcrypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => (
        byte.toString(16).padStart(2, '0')
    )).join('');
}

async function artifact(body, url) {
    const bytes = bytesFor(body);
    return {
        body,
        bytes,
        reference: {
            url,
            sha256: await sha256Hex(bytes),
            bytes: bytes.byteLength,
        },
    };
}

function emptyV3Summary(semester = SEMESTER) {
    return {
        schemaVersion: 1,
        kind: 'semester-summary',
        semester,
        lastReportTime: null,
        snapshotCount: 0,
        currentSnapshot: null,
        milestones: [],
        courses: {},
    };
}

function emptyV3Department(department, semester = SEMESTER) {
    return {
        schemaVersion: 1,
        kind: 'department-detail',
        semester,
        department,
        timestamps: [],
        courses: {},
    };
}

function v3Manifest(summaryArtifact, departmentArtifacts = {}, overrides = {}) {
    return {
        manifestVersion: 1,
        dataModelVersion: 3,
        buildId: 'v3-build',
        semester: SEMESTER,
        generatedAt: '2026-06-01T00:00:00+00:00',
        currentSnapshot: null,
        summary: { schemaVersion: 1, ...summaryArtifact.reference },
        departments: Object.fromEntries(
            Object.entries(departmentArtifacts).map(([department, value]) => [
                department,
                { schemaVersion: 1, ...value.reference },
            ]),
        ),
        ...overrides,
    };
}

function v2Manifest(summaryArtifact, overrides = {}) {
    return {
        manifestVersion: 1,
        dataModelVersion: 2,
        buildId: 'v2-build',
        semester: SEMESTER,
        generatedAt: '2026-05-31T00:00:00+00:00',
        currentSnapshot: {
            id: 1,
            observedAt: '2026-05-31T00:00:00+00:00',
            overallFill: 0,
        },
        summary: summaryArtifact.reference,
        departments: {},
        ...overrides,
    };
}

function pointer(current = 'manifests/current.json', previous = null) {
    return { manifestVersion: 1, current, previous };
}

test('startup fetches pointer, current manifest, and verified summary only', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const manifest = v3Manifest(summaryArtifact);
    const requests = [];
    const fetchImpl = async (url, options = {}) => {
        requests.push({ url: String(url), cache: options.cache });
        if (String(url).endsWith('/manifest.json')) {
            return jsonResponse(pointer());
        }
        if (String(url).endsWith('/manifests/current.json')) {
            return jsonResponse(manifest);
        }
        if (String(url) === summaryArtifact.reference.url) {
            return bytesResponse(summaryArtifact);
        }
        throw new Error(`unexpected request ${url}`);
    };

    const loaded = await loadSemesterManifest(
        'https://example.test/data/summer-2026/manifest.json',
        { fetchImpl, cryptoImpl: webcrypto },
    );

    assert.equal(loaded.stale, false);
    assert.deepEqual(loaded.payload, {
        data: {
            sem: SEMESTER,
            lrt: null,
            sn: [],
            snapshotCount: 0,
            cr: {},
        },
        milestones: [],
        semester: SEMESTER,
    });
    assert.deepEqual(requests.map(item => item.url), [
        'https://example.test/data/summer-2026/manifest.json',
        'https://example.test/data/summer-2026/manifests/current.json',
        'https://example.test/data/blobs/summary.json',
    ]);
    assert.equal(requests[0].cache, 'no-cache');
    assert.equal(requests[1].cache, undefined);
    assert.equal(requests[2].cache, undefined);
});

test('unsupported pointer versions are rejected before following references', async () => {
    let manifestFetches = 0;
    const fetchImpl = async url => {
        if (String(url).endsWith('/manifest.json')) {
            return jsonResponse({ ...pointer(), manifestVersion: 2 });
        }
        manifestFetches += 1;
        return jsonResponse({});
    };

    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl },
        ),
        error => error instanceof UnsupportedSchemaError,
    );
    assert.equal(manifestFetches, 0);
});

test('unsupported data model versions are rejected without loading blobs', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    let summaryFetches = 0;
    const fetchImpl = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) {
            return jsonResponse(v3Manifest(summaryArtifact, {}, { dataModelVersion: 4 }));
        }
        summaryFetches += 1;
        return bytesResponse(summaryArtifact);
    };

    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl, cryptoImpl: webcrypto },
        ),
        error => error instanceof UnsupportedSchemaError,
    );
    assert.equal(summaryFetches, 0);
});

test('summary hash success and schema validation are enforced', async () => {
    const summary = emptyV3Summary();
    summary.courses['CSCI 101'] = {
        code: 'CSCI 101',
        department: 'CSCI',
        title: 'Intro',
        averageFill: 0.5,
        isFilled: false,
        sectionCount: 1,
        fullSectionCount: 0,
    };
    const summaryArtifact = await artifact(
        summary,
        'https://example.test/data/blobs/summary.json',
    );
    const manifest = v3Manifest(summaryArtifact);
    const fetchImpl = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(manifest);
        return bytesResponse(summaryArtifact);
    };

    const loaded = await loadSemesterManifest(
        'https://example.test/data/summer-2026/manifest.json',
        { fetchImpl, cryptoImpl: webcrypto },
    );
    assert.deepEqual(loaded.payload.data.cr['CSCI 101'], {
        code: 'CSCI 101',
        d: 'CSCI',
        ti: 'Intro',
        af: 0.5,
        if: false,
        s: {},
        sectionCount: 1,
        fullSectionCount: 0,
    });

    const invalidSummary = { ...summary, courses: { 'CSCI 101': { ...summary.courses['CSCI 101'], fullSectionCount: undefined } } };
    const invalidArtifact = await artifact(
        invalidSummary,
        'https://example.test/data/blobs/invalid-summary.json',
    );
    const invalidManifest = v3Manifest(invalidArtifact);
    const invalidFetch = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(invalidManifest);
        return bytesResponse(invalidArtifact);
    };
    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl: invalidFetch, cryptoImpl: webcrypto },
        ),
        /fullSectionCount/,
    );
});

test('hash mismatch and byte-length mismatch reject verified blobs', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const mismatchManifest = v3Manifest(summaryArtifact, {}, {
        summary: { schemaVersion: 1, ...summaryArtifact.reference, sha256: '0'.repeat(64) },
    });
    const mismatchFetch = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(mismatchManifest);
        return bytesResponse(summaryArtifact);
    };
    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl: mismatchFetch, cryptoImpl: webcrypto },
        ),
        error => error instanceof IntegrityError && /SHA-256 mismatch/.test(error.message),
    );

    const lengthManifest = v3Manifest(summaryArtifact, {}, {
        summary: { schemaVersion: 1, ...summaryArtifact.reference, bytes: summaryArtifact.reference.bytes + 1 },
    });
    const lengthFetch = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(lengthManifest);
        return bytesResponse(summaryArtifact);
    };
    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl: lengthFetch, cryptoImpl: webcrypto },
        ),
        error => error instanceof IntegrityError && /Byte length mismatch/.test(error.message),
    );
});

test('summary semester identity mismatch is rejected', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary('Fall 2026'),
        'https://example.test/data/blobs/summary.json',
    );
    const manifest = v3Manifest(summaryArtifact);
    const fetchImpl = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(manifest);
        return bytesResponse(summaryArtifact);
    };

    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl, cryptoImpl: webcrypto },
        ),
        /summary semester does not match/,
    );
});

test('current failure falls back to a validated v2 previous manifest', async () => {
    const v2Summary = {
        semester: SEMESTER,
        milestones: [],
        data: { sem: SEMESTER, lrt: null, sn: [], cr: {} },
    };
    const summaryArtifact = await artifact(
        v2Summary,
        'https://example.test/data/blobs/v2-summary.json',
    );
    const previousManifest = v2Manifest(summaryArtifact);
    const fetchImpl = async url => {
        const value = String(url);
        if (value.endsWith('/manifest.json')) {
            return jsonResponse(pointer('manifests/broken.json', 'manifests/previous.json'));
        }
        if (value.endsWith('/manifests/broken.json')) return jsonResponse({}, false, 503);
        if (value.endsWith('/manifests/previous.json')) return jsonResponse(previousManifest);
        return bytesResponse(summaryArtifact);
    };

    const loaded = await loadSemesterManifest(
        'https://example.test/data/summer-2026/manifest.json',
        { fetchImpl, cryptoImpl: webcrypto },
    );

    assert.equal(loaded.stale, true);
    assert.equal(loaded.manifest.dataModelVersion, 2);
    assert.match(loaded.manifestUrl, /previous\.json$/);
    assert.deepEqual(loaded.payload, v2Summary);
});

test('department payload is validated, promise-cached, and fetched once', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const departmentArtifact = await artifact(
        emptyV3Department('CSCI'),
        'https://example.test/data/blobs/csci.json',
    );
    const manifest = v3Manifest(summaryArtifact, { CSCI: departmentArtifact });
    let fetchCount = 0;
    const fetchImpl = async () => {
        fetchCount += 1;
        return bytesResponse(departmentArtifact);
    };
    const cache = new Map();
    const manifestUrl = 'https://example.test/data/summer-2026/manifests/current.json';

    const [first, second] = await Promise.all([
        loadDepartmentPayload('CSCI', manifest, manifestUrl, cache, {
            fetchImpl,
            cryptoImpl: webcrypto,
        }),
        loadDepartmentPayload('CSCI', manifest, manifestUrl, cache, {
            fetchImpl,
            cryptoImpl: webcrypto,
        }),
    ]);

    assert.equal(fetchCount, 1);
    assert.equal(first, second);
    assert.equal(first.department, 'CSCI');
});

test('v3 department payload adapts local timestamps and detail to the dashboard shape', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const department = emptyV3Department('CSCI');
    department.timestamps = ['2026-06-01T00:00:00+00:00'];
    department.courses['CSCI 101'] = {
        code: 'CSCI 101',
        department: 'CSCI',
        title: 'Intro',
        averageFill: 0.5,
        isFilled: false,
        sections: {
            '1L': {
                sectionId: 10,
                type: 'L',
                instructor: '',
                currentEnrollment: 5,
                currentCapacity: 10,
                currentFill: 0.5,
            },
        },
        averageHistory: [{ timestampIdx: 0, fill: 0.5 }],
        sectionHistory: {
            '1L': [{ timestampIdx: 0, fill: 0.5, enrollment: 5, capacity: 10 }],
        },
        events: [{
            timestampIdx: 0,
            eventType: 'capacity_changed',
            sectionCode: '1L',
            oldValue: '8',
            newValue: '10',
        }],
    };
    const departmentArtifact = await artifact(
        department,
        'https://example.test/data/blobs/csci.json',
    );
    const manifest = v3Manifest(summaryArtifact, { CSCI: departmentArtifact });

    const loaded = await loadDepartmentPayload(
        'CSCI',
        manifest,
        'https://example.test/data/summer-2026/manifests/current.json',
        new Map(),
        {
            fetchImpl: async () => bytesResponse(departmentArtifact),
            cryptoImpl: webcrypto,
        },
    );

    assert.deepEqual(loaded.courses['CSCI 101'], {
        code: 'CSCI 101',
        d: 'CSCI',
        ti: 'Intro',
        af: 0.5,
        if: false,
        s: {
            '1L': {
                sid: 10,
                t: 'L',
                in: '',
                ce: 5,
                cc: 10,
                cf: 0.5,
                h: [{ i: 0, f: 0.5, e: 5, c: 10 }],
            },
        },
        ah: [{ i: 0, f: 0.5 }],
        ev: [{
            timestampIdx: 0,
            eventType: 'capacity_changed',
            sectionCode: '1L',
            oldValue: '8',
            newValue: '10',
            et: 'capacity_changed',
            sc: '1L',
            ov: '8',
            nv: '10',
            st: '2026-06-01T00:00:00+00:00',
        }],
        sn: [{ ts: '2026-06-01T00:00:00+00:00' }],
    });
});

test('department identity mismatch rejects and failed requests are removed for retry', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const wrongDepartmentArtifact = await artifact(
        emptyV3Department('MATH'),
        'https://example.test/data/blobs/csci-wrong.json',
    );
    const manifest = v3Manifest(summaryArtifact, { CSCI: wrongDepartmentArtifact });
    const fetchImpl = async () => bytesResponse(wrongDepartmentArtifact);
    await assert.rejects(
        loadDepartmentPayload(
            'CSCI',
            manifest,
            'https://example.test/data/summer-2026/manifests/current.json',
            new Map(),
            { fetchImpl, cryptoImpl: webcrypto },
        ),
        /department name does not match/,
    );

    const validDepartmentArtifact = await artifact(
        emptyV3Department('CSCI'),
        'https://example.test/data/blobs/csci.json',
    );
    const retryManifest = v3Manifest(summaryArtifact, { CSCI: validDepartmentArtifact });
    const cache = new Map();
    let attempts = 0;
    const retryFetch = async () => {
        attempts += 1;
        if (attempts === 1) return bytesResponse(validDepartmentArtifact, false, 503);
        return bytesResponse(validDepartmentArtifact);
    };
    const options = { fetchImpl: retryFetch, cryptoImpl: webcrypto };
    const args = [
        'CSCI',
        retryManifest,
        'https://example.test/data/summer-2026/manifests/current.json',
        cache,
        options,
    ];
    await assert.rejects(loadDepartmentPayload(...args), /HTTP 503/);
    assert.equal(cache.has('CSCI'), false);
    const retried = await loadDepartmentPayload(...args);
    assert.equal(retried.department, 'CSCI');
    assert.equal(attempts, 2);
});

test('department hash success is accepted and a hash mismatch is rejected', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const departmentArtifact = await artifact(
        emptyV3Department('CSCI'),
        'https://example.test/data/blobs/csci.json',
    );
    const manifest = v3Manifest(summaryArtifact, { CSCI: departmentArtifact });
    const manifestUrl = 'https://example.test/data/summer-2026/manifests/current.json';

    const loaded = await loadDepartmentPayload(
        'CSCI',
        manifest,
        manifestUrl,
        new Map(),
        {
            fetchImpl: async () => bytesResponse(departmentArtifact),
            cryptoImpl: webcrypto,
        },
    );
    assert.equal(loaded.department, 'CSCI');

    const mismatchManifest = v3Manifest(summaryArtifact, {
        CSCI: {
            ...departmentArtifact,
            reference: {
                ...departmentArtifact.reference,
                sha256: '0'.repeat(64),
            },
        },
    });
    await assert.rejects(
        loadDepartmentPayload(
            'CSCI',
            mismatchManifest,
            manifestUrl,
            new Map(),
            {
                fetchImpl: async () => bytesResponse(departmentArtifact),
                cryptoImpl: webcrypto,
            },
        ),
        error => error instanceof IntegrityError && /SHA-256 mismatch/.test(error.message),
    );
});

test('unknown versions are never silently accepted', async () => {
    const summaryArtifact = await artifact(
        emptyV3Summary(),
        'https://example.test/data/blobs/summary.json',
    );
    const manifest = v3Manifest(summaryArtifact, {}, {
        summary: { schemaVersion: 2, ...summaryArtifact.reference },
    });
    const fetchImpl = async url => {
        if (String(url).endsWith('/manifest.json')) return jsonResponse(pointer());
        if (String(url).endsWith('/manifests/current.json')) return jsonResponse(manifest);
        return bytesResponse(summaryArtifact);
    };

    await assert.rejects(
        loadSemesterManifest(
            'https://example.test/data/summer-2026/manifest.json',
            { fetchImpl, cryptoImpl: webcrypto },
        ),
        error => error instanceof UnsupportedSchemaError,
    );
});
