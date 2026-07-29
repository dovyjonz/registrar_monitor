async function fetchJson(url, { fetchImpl, signal, cache } = {}) {
    const response = await fetchImpl(url, { signal, cache });
    if (!response.ok) {
        throw new Error(`Failed to load ${url}: HTTP ${response.status}`);
    }
    return response.json();
}

async function loadManifestVersion(reference, pointerUrl, options) {
    const manifestUrl = new URL(reference, pointerUrl).href;
    const manifest = await fetchJson(manifestUrl, options);
    if (!manifest.summary?.url) {
        throw new Error(`Manifest ${manifestUrl} has no summary`);
    }
    const summaryUrl = new URL(manifest.summary.url, manifestUrl).href;
    const payload = await fetchJson(summaryUrl, options);
    return { manifest, manifestUrl, payload };
}

export async function loadSemesterManifest(
    pointerUrl,
    { fetchImpl = fetch, signal } = {},
) {
    const baseUrl =
        globalThis.location?.href || 'http://localhost/';
    const absolutePointerUrl = new URL(pointerUrl, baseUrl).href;
    const options = { fetchImpl, signal };
    const pointer = await fetchJson(absolutePointerUrl, {
        ...options,
        cache: 'no-cache',
    });

    try {
        return {
            ...(await loadManifestVersion(
                pointer.current,
                absolutePointerUrl,
                options,
            )),
            stale: false,
        };
    } catch (currentError) {
        if (!pointer.previous) throw currentError;
        return {
            ...(await loadManifestVersion(
                pointer.previous,
                absolutePointerUrl,
                options,
            )),
            stale: true,
        };
    }
}

export function loadDepartmentPayload(
    department,
    manifest,
    manifestUrl,
    cache,
    { fetchImpl = fetch, signal } = {},
) {
    if (!cache.has(department)) {
        const reference = manifest.departments?.[department];
        if (!reference?.url) {
            return Promise.reject(
                new Error(`No static payload for department ${department}`),
            );
        }
        const url = new URL(reference.url, manifestUrl).href;
        const request = fetchJson(url, { fetchImpl, signal });
        cache.set(department, request);
        request.catch(() => cache.delete(department));
    }
    return cache.get(department);
}
