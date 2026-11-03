const MANIFEST_VERSION = 1;
const DATA_MODEL_VERSION_V3 = 3;
const SUMMARY_SCHEMA_VERSION = 1;
const DEPARTMENT_SCHEMA_VERSION = 1;
const SHA256_PATTERN = /^[0-9a-f]{64}$/i;

export class UnsupportedSchemaError extends Error {
    constructor(contract, version) {
        const renderedVersion = version === undefined
            ? 'missing'
            : JSON.stringify(version);
        super(`Unsupported ${contract} version: ${renderedVersion}`);
        this.name = 'UnsupportedSchemaError';
        this.contract = contract;
        this.version = version;
    }
}

export class IntegrityError extends Error {
    constructor(message) {
        super(message);
        this.name = 'IntegrityError';
    }
}

function isRecord(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function requireRecord(value, context) {
    if (!isRecord(value)) {
        throw new Error(`${context} must be an object`);
    }
    return value;
}

function requireString(value, context) {
    if (typeof value !== 'string' || value.length === 0) {
        throw new Error(`${context} must be a non-empty string`);
    }
    return value;
}

function requireText(value, context) {
    if (typeof value !== 'string') {
        throw new Error(`${context} must be a string`);
    }
    return value;
}

function requireFiniteNumber(value, context) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`${context} must be a finite number`);
    }
    return value;
}

function requireNonNegativeInteger(value, context) {
    if (!Number.isInteger(value) || value < 0) {
        throw new Error(`${context} must be a non-negative integer`);
    }
    return value;
}

function requireSupportedVersion(actual, expected, contract) {
    if (actual !== expected) {
        throw new UnsupportedSchemaError(contract, actual);
    }
}

function requireArray(value, context) {
    if (!Array.isArray(value)) {
        throw new Error(`${context} must be an array`);
    }
    return value;
}

function validateSnapshot(value, context) {
    if (value === null) return;
    const snapshot = requireRecord(value, context);
    const fields = ['id', 'observedAt', 'overallFill'];
    for (const field of fields) {
        const fieldValue = snapshot[field];
        if (field === 'id') {
            if (!Number.isInteger(fieldValue)) {
                throw new Error(`${context}.id must be an integer`);
            }
        } else if (field === 'observedAt') {
            requireString(fieldValue, `${context}.observedAt`);
        } else {
            requireFiniteNumber(fieldValue, `${context}.overallFill`);
        }
    }
}

function validateMilestones(milestones, context) {
    for (const [index, milestoneValue] of requireArray(milestones, context).entries()) {
        const milestone = requireRecord(milestoneValue, `${context}[${index}]`);
        for (const [key, value] of Object.entries(milestone)) {
            requireText(value, `${context}[${index}].${key}`);
        }
    }
}

function validateBlobReference(
    reference,
    context,
    schemaVersion = DEPARTMENT_SCHEMA_VERSION,
) {
    const value = requireRecord(reference, context);
    requireSupportedVersion(
        value.schemaVersion,
        schemaVersion,
        `${context} schema`,
    );
    requireString(value.url, `${context}.url`);
    if (typeof value.sha256 !== 'string' || !SHA256_PATTERN.test(value.sha256)) {
        throw new Error(`${context}.sha256 must be a SHA-256 hex digest`);
    }
    if (value.bytes !== undefined) {
        requireNonNegativeInteger(value.bytes, `${context}.bytes`);
    }
    return value;
}

export function validatePointer(pointer) {
    const value = requireRecord(pointer, 'manifest pointer');
    requireSupportedVersion(
        value.manifestVersion,
        MANIFEST_VERSION,
        'manifest pointer',
    );
    requireString(value.current, 'manifest pointer.current');
    if (value.previous !== null && value.previous !== undefined) {
        requireString(value.previous, 'manifest pointer.previous');
    }
    return value;
}

export function validateManifest(manifest) {
    const value = requireRecord(manifest, 'immutable manifest');
    requireSupportedVersion(
        value.manifestVersion,
        MANIFEST_VERSION,
        'manifest',
    );
    requireSupportedVersion(
        value.dataModelVersion,
        DATA_MODEL_VERSION_V3,
        'data model',
    );
    requireString(value.buildId, 'manifest.buildId');
    requireString(value.semester, 'manifest.semester');
    requireString(value.generatedAt, 'manifest.generatedAt');
    validateSnapshot(
        value.currentSnapshot,
        'manifest.currentSnapshot',
    );
    validateBlobReference(
        value.summary,
        'manifest.summary',
        SUMMARY_SCHEMA_VERSION,
    );

    const departments = requireRecord(value.departments, 'manifest.departments');
    for (const [department, reference] of Object.entries(departments)) {
        requireString(department, 'manifest department name');
        validateBlobReference(
            reference,
            `manifest.departments.${department}`,
        );
    }
    return value;
}

function validateSummaryCourse(code, courseValue, context) {
    const course = requireRecord(courseValue, context);
    if (course.code !== code) {
        throw new Error(`${context}.code does not match its course key`);
    }
    requireString(course.department, `${context}.department`);
    requireText(course.title, `${context}.title`);
    if (course.instructors !== undefined) {
        for (const name of requireArray(course.instructors, `${context}.instructors`)) {
            requireText(name, `${context}.instructors[]`);
        }
    }
    requireFiniteNumber(course.averageFill, `${context}.averageFill`);
    if (typeof course.isFilled !== 'boolean') {
        throw new Error(`${context}.isFilled must be a boolean`);
    }
    for (const field of ['sectionCount', 'fullSectionCount']) {
        requireNonNegativeInteger(course[field], `${context}.${field}`);
    }
}

function validateV3Summary(summary, semester) {
    requireSupportedVersion(
        summary.schemaVersion,
        SUMMARY_SCHEMA_VERSION,
        'summary',
    );
    if (summary.kind !== 'semester-summary') {
        throw new Error('summary.kind is unsupported');
    }
    if (summary.semester !== semester) {
        throw new Error('summary semester does not match the manifest');
    }
    if (summary.lastReportTime !== null) {
        requireText(summary.lastReportTime, 'summary.lastReportTime');
    }
    requireNonNegativeInteger(summary.snapshotCount, 'summary.snapshotCount');
    validateSnapshot(summary.currentSnapshot, 'summary.currentSnapshot');
    validateMilestones(summary.milestones, 'summary.milestones');

    const courses = requireRecord(summary.courses, 'summary.courses');
    for (const [code, course] of Object.entries(courses)) {
        requireString(code, 'summary course code');
        validateSummaryCourse(code, course, `summary.courses.${code}`);
    }
    return summary;
}

export function validateSummary(summary, semester) {
    const value = requireRecord(summary, 'semester summary');
    return validateV3Summary(value, semester);
}

function validateTimestampIndex(value, timestampCount, context) {
    const index = requireNonNegativeInteger(value, `${context}.timestampIdx`);
    if (index >= timestampCount) {
        throw new Error(`${context}.timestampIdx is outside the timestamp table`);
    }
}

function validateV3HistoryPoint(pointValue, timestampCount, context, section) {
    const point = requireRecord(pointValue, context);
    validateTimestampIndex(point.timestampIdx, timestampCount, context);
    requireFiniteNumber(point.fill, `${context}.fill`);
    if (section) {
        requireNonNegativeInteger(point.enrollment, `${context}.enrollment`);
        requireNonNegativeInteger(point.capacity, `${context}.capacity`);
    }
}

function validateV3DepartmentCourse(
    code,
    courseValue,
    department,
    timestampCount,
) {
    const context = `department ${department}.courses.${code}`;
    const course = requireRecord(courseValue, context);
    if (course.code !== code) throw new Error(`${context}.code does not match its course key`);
    if (course.department !== department) {
        throw new Error(`${context}.department does not match the department payload`);
    }
    requireText(course.title, `${context}.title`);
    requireFiniteNumber(course.averageFill, `${context}.averageFill`);
    if (typeof course.isFilled !== 'boolean') {
        throw new Error(`${context}.isFilled must be a boolean`);
    }

    const sections = requireRecord(course.sections, `${context}.sections`);
    for (const [sectionCode, sectionValue] of Object.entries(sections)) {
        const sectionContext = `${context}.sections.${sectionCode}`;
        const section = requireRecord(sectionValue, sectionContext);
        requireText(section.type, `${sectionContext}.type`);
        requireText(section.instructor, `${sectionContext}.instructor`);
        requireNonNegativeInteger(section.currentEnrollment, `${sectionContext}.currentEnrollment`);
        requireNonNegativeInteger(section.currentCapacity, `${sectionContext}.currentCapacity`);
        requireFiniteNumber(section.currentFill, `${sectionContext}.currentFill`);
    }

    const averageHistory = requireArray(course.averageHistory, `${context}.averageHistory`);
    for (const [index, point] of averageHistory.entries()) {
        validateV3HistoryPoint(point, timestampCount, `${context}.averageHistory[${index}]`, false);
    }
    const sectionHistory = requireRecord(course.sectionHistory, `${context}.sectionHistory`);
    for (const [sectionCode, pointsValue] of Object.entries(sectionHistory)) {
        const points = requireArray(
            pointsValue,
            `${context}.sectionHistory.${sectionCode}`,
        );
        for (const [index, point] of points.entries()) {
            validateV3HistoryPoint(
                point,
                timestampCount,
                `${context}.sectionHistory.${sectionCode}[${index}]`,
                true,
            );
        }
    }
    const events = requireArray(course.events, `${context}.events`);
    for (const [index, eventValue] of events.entries()) {
        const event = requireRecord(eventValue, `${context}.events[${index}]`);
        if (Object.hasOwn(event, 'timestampIdx')) {
            validateTimestampIndex(
                event.timestampIdx,
                timestampCount,
                `${context}.events[${index}]`,
            );
        }
    }
}

function validateV3Department(payload, semester, department) {
    requireSupportedVersion(
        payload.schemaVersion,
        DEPARTMENT_SCHEMA_VERSION,
        'department',
    );
    if (payload.kind !== 'department-detail') {
        throw new Error('department.kind is unsupported');
    }
    if (payload.semester !== semester) {
        throw new Error('department semester does not match the manifest');
    }
    if (payload.department !== department) {
        throw new Error('department name does not match the requested department');
    }
    const timestamps = requireArray(payload.timestamps, 'department.timestamps');
    for (const [index, timestamp] of timestamps.entries()) {
        requireText(timestamp, `department.timestamps[${index}]`);
    }
    const courses = requireRecord(payload.courses, 'department.courses');
    for (const [code, course] of Object.entries(courses)) {
        validateV3DepartmentCourse(code, course, department, timestamps.length);
    }
    return payload;
}

export function validateDepartmentPayload(payload, semester, department) {
    const value = requireRecord(payload, 'department payload');
    return validateV3Department(value, semester, department);
}

async function fetchJson(url, { fetchImpl, signal, cache } = {}) {
    const requestOptions = { signal };
    if (cache) requestOptions.cache = cache;
    const response = await fetchImpl(url, requestOptions);
    if (!response.ok) {
        throw new Error(`Failed to load ${url}: HTTP ${response.status}`);
    }
    return response.json();
}

async function fetchBytes(url, { fetchImpl, signal, cache } = {}) {
    const requestOptions = { signal };
    if (cache) requestOptions.cache = cache;
    const response = await fetchImpl(url, requestOptions);
    if (!response.ok) {
        throw new Error(`Failed to load ${url}: HTTP ${response.status}`);
    }
    return new Uint8Array(await response.arrayBuffer());
}

async function sha256Hex(bytes, cryptoImpl = globalThis.crypto) {
    if (!cryptoImpl?.subtle?.digest) {
        throw new Error('Web Crypto SHA-256 is unavailable');
    }
    const digest = await cryptoImpl.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => (
        byte.toString(16).padStart(2, '0')
    )).join('');
}

async function fetchVerifiedJson(url, reference, options) {
    const bytes = await fetchBytes(url, options);
    if (reference.bytes !== undefined && bytes.byteLength !== reference.bytes) {
        throw new IntegrityError(
            `Byte length mismatch for ${url}: expected ${reference.bytes}, got ${bytes.byteLength}`,
        );
    }
    const actualHash = await sha256Hex(bytes, options.cryptoImpl);
    if (actualHash !== reference.sha256.toLowerCase()) {
        throw new IntegrityError(`SHA-256 mismatch for ${url}`);
    }
    let text;
    try {
        text = new TextDecoder().decode(bytes);
        return JSON.parse(text);
    } catch (error) {
        throw new Error(`Invalid JSON in ${url}: ${error.message}`, { cause: error });
    }
}

function adaptV3Summary(payload, semester) {
    const summary = validateV3Summary(payload, semester);
    const courses = Object.fromEntries(
        Object.entries(summary.courses).map(([code, course]) => [
            code,
            {
                code,
                d: course.department,
                ti: course.title,
                instructors: course.instructors ?? [],
                af: course.averageFill,
                if: course.isFilled,
                s: {},
                sectionCount: course.sectionCount,
                fullSectionCount: course.fullSectionCount,
                ...(course.availability ? { availability: course.availability } : {}),
            },
        ]),
    );
    return {
        data: {
            sem: summary.semester,
            lrt: summary.lastReportTime,
            sn: [],
            snapshotCount: summary.snapshotCount,
            cr: courses,
        },
        milestones: summary.milestones,
        semester: summary.semester,
    };
}

function adaptV3HistoryPoint(point, section) {
    return {
        i: point.timestampIdx,
        f: point.fill,
        ...(section ? { e: point.enrollment, c: point.capacity } : {}),
    };
}

function adaptV3Department(payload, semester, department) {
    const value = validateV3Department(payload, semester, department);
    const snapshots = value.timestamps.map(timestamp => ({ ts: timestamp }));
    return {
        ...value,
        courses: Object.fromEntries(
            Object.entries(value.courses).map(([code, course]) => {
                const sections = Object.fromEntries(
                    Object.entries(course.sections).map(([sectionCode, section]) => [
                        sectionCode,
                        {
                            sid: section.sectionId,
                            t: section.type,
                            in: section.instructor,
                            ce: section.currentEnrollment,
                            cc: section.currentCapacity,
                            cf: section.currentFill,
                            h: (course.sectionHistory[sectionCode] || []).map(point => (
                                adaptV3HistoryPoint(point, true)
                            )),
                        },
                    ]),
                );
                const events = course.events.map(event => ({
                    ...event,
                    et: event.eventType,
                    sc: event.sectionCode,
                    ov: event.oldValue,
                    nv: event.newValue,
                    st: Object.hasOwn(event, 'timestampIdx')
                        ? value.timestamps[event.timestampIdx]
                        : undefined,
                }));
                return [
                    code,
                    {
                        code,
                        d: course.department,
                        ti: course.title,
                        af: course.averageFill,
                        if: course.isFilled,
                        s: sections,
                        ah: course.averageHistory.map(point => (
                            adaptV3HistoryPoint(point, false)
                        )),
                        ev: events,
                        sn: snapshots,
                        ...(course.availability ? { availability: course.availability } : {}),
                    },
                ];
            }),
        ),
    };
}

export function adaptPreviewCourseState(state) {
    const value = requireRecord(state, 'course preview state');
    if (value.kind !== 'course') throw new Error('preview state kind is unsupported');
    const timestamps = requireArray(value.timestamps, 'preview state timestamps');
    const course = requireRecord(value.course, 'preview state course');
    const department = requireString(course.department, 'preview course department');
    validateV3DepartmentCourse(
        course.code,
        course,
        department,
        timestamps.length,
    );
    const payload = adaptV3Department(
        {
            schemaVersion: DEPARTMENT_SCHEMA_VERSION,
            kind: 'department-detail',
            semester: value.semester,
            department,
            timestamps,
            courses: { [course.code]: course },
        },
        value.semester,
        department,
    );
    return payload.courses[course.code];
}

async function loadManifestVersion(reference, pointerUrl, options) {
    const manifestUrl = new URL(reference, pointerUrl).href;
    const manifest = validateManifest(await fetchJson(manifestUrl, options));
    const summaryUrl = new URL(manifest.summary.url, manifestUrl).href;
    const rawPayload = await fetchVerifiedJson(
        summaryUrl,
        manifest.summary,
        options,
    );
    const payload = adaptV3Summary(rawPayload, manifest.semester);
    return { manifest, manifestUrl, payload };
}

export async function loadSemesterManifest(
    pointerUrl,
    { fetchImpl = fetch, signal, cryptoImpl = globalThis.crypto } = {},
) {
    const baseUrl = globalThis.location?.href || 'http://localhost/';
    const absolutePointerUrl = new URL(pointerUrl, baseUrl).href;
    const options = { fetchImpl, signal, cryptoImpl };
    const pointer = validatePointer(await fetchJson(absolutePointerUrl, {
        ...options,
        cache: 'no-cache',
    }));

    try {
        return {
            ...(await loadManifestVersion(pointer.current, absolutePointerUrl, options)),
            stale: false,
        };
    } catch (currentError) {
        if (!pointer.previous) throw currentError;
        return {
            ...(await loadManifestVersion(pointer.previous, absolutePointerUrl, options)),
            stale: true,
        };
    }
}

export function loadDepartmentPayload(
    department,
    manifest,
    manifestUrl,
    cache,
    { fetchImpl = fetch, signal, cryptoImpl = globalThis.crypto } = {},
) {
    if (!cache.has(department)) {
        const request = Promise.resolve().then(async () => {
            const validatedManifest = validateManifest(manifest);
            const reference = validatedManifest.departments?.[department];
            if (!reference) {
                throw new Error(`No static payload for department ${department}`);
            }
            const url = new URL(reference.url, manifestUrl).href;
            const rawPayload = await fetchVerifiedJson(url, reference, {
                fetchImpl,
                signal,
                cryptoImpl,
            });
            return adaptV3Department(
                rawPayload,
                validatedManifest.semester,
                department,
            );
        });
        cache.set(department, request);
        request.catch(() => cache.delete(department));
    }
    return cache.get(department);
}
