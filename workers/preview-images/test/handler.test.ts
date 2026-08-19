import { createExecutionContext, waitOnExecutionContext } from 'cloudflare:test';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { handlePreviewRequest } from '../src/handler';

const state = (hash: string) => ({
  schemaVersion: 1,
  kind: 'course',
  hash,
  semester: 'Fall 2026',
  semesterSlug: 'fall-2026',
  slug: 'ant-140',
  code: 'ANT 140',
  title: 'Introduction',
});

function runtime(rendered = new Response('png', { status: 200 })) {
  const values = new Map<string, Response>();
  const cache = {
    async match(request: Request) {
      return values.get(request.url)?.clone();
    },
    async put(request: Request, response: Response) {
      values.set(request.url, response.clone());
    },
  } as Cache;
  const quickAction = vi.fn(async () => rendered.clone());
  const env = {
    PAGES_ORIGIN: 'https://registrar-monitor.pages.dev',
    BROWSER: { quickAction },
  } as Env;
  return { cache, env, quickAction };
}

afterEach(() => vi.restoreAllMocks());

describe('preview request handling', () => {
  it('caches a successful PNG under the immutable request URL', async () => {
    const hash = 'obLD1OX2';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(state(hash)));
    const { cache, env, quickAction } = runtime();
    const request = new Request(`https://example.test/preview/course/fall-2026/ant-140/${hash}.png`);
    const firstContext = createExecutionContext();
    const first = await handlePreviewRequest(request, env, firstContext, cache);
    await waitOnExecutionContext(firstContext);
    const second = await handlePreviewRequest(request, env, createExecutionContext(), cache);

    expect(first.status).toBe(200);
    expect(first.headers.get('Cache-Control')).toContain('immutable');
    expect(second.status).toBe(200);
    expect(quickAction).toHaveBeenCalledTimes(1);
  });

  it('uses a distinct cache identity for a new hash', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const hash = /([A-Za-z0-9_-]{8})\.json$/.exec(String(input))?.[1] ?? '';
      return Response.json(state(hash));
    });
    const { cache, env, quickAction } = runtime();
    for (const hash of ['obLD1OX2', 'ubLD1OX2']) {
      const context = createExecutionContext();
      await handlePreviewRequest(
        new Request(`https://example.test/preview/course/fall-2026/ant-140/${hash}.png`),
        env,
        context,
        cache,
      );
      await waitOnExecutionContext(context);
    }
    expect(quickAction).toHaveBeenCalledTimes(2);
  });

  it('keeps missing state and renderer failures non-cacheable', async () => {
    const hash = 'obLD1OX2';
    const request = new Request(`https://example.test/preview/course/fall-2026/ant-140/${hash}.png`);
    const missingRuntime = runtime();
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('', { status: 404 }));
    const missing = await handlePreviewRequest(
      request, missingRuntime.env, createExecutionContext(), missingRuntime.cache,
    );
    expect(missing.status).toBe(404);
    expect(missing.headers.get('Cache-Control')).toBe('no-store');

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(Response.json(state(hash)));
    const failedRuntime = runtime(new Response('error', { status: 503 }));
    const failed = await handlePreviewRequest(
      request, failedRuntime.env, createExecutionContext(), failedRuntime.cache,
    );
    expect(failed.status).toBe(502);
    expect(failed.headers.get('Cache-Control')).toBe('no-store');
  });

  it('rejects an oversized streamed state without relying on Content-Length', async () => {
    const hash = 'obLD1OX2';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array(512_001));
          controller.close();
        },
      }),
    ));
    const { cache, env, quickAction } = runtime();
    const response = await handlePreviewRequest(
      new Request(`https://example.test/preview/course/fall-2026/ant-140/${hash}.png`),
      env,
      createExecutionContext(),
      cache,
    );

    expect(response.status).toBe(502);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(quickAction).not.toHaveBeenCalled();
  });

  it('serves HEAD from the same immutable cache identity as GET', async () => {
    const hash = 'obLD1OX2';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(state(hash)));
    const { cache, env, quickAction } = runtime();
    const url = `https://example.test/preview/course/fall-2026/ant-140/${hash}.png`;
    const context = createExecutionContext();
    await handlePreviewRequest(new Request(url), env, context, cache);
    await waitOnExecutionContext(context);
    const head = await handlePreviewRequest(
      new Request(url, { method: 'HEAD' }), env, createExecutionContext(), cache,
    );

    expect(head.status).toBe(200);
    expect(await head.text()).toBe('');
    expect(quickAction).toHaveBeenCalledTimes(1);
  });
});
