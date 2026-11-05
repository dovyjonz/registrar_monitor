import { renderCard } from './card';
import { parsePreviewRoute, validateState } from './model';

const IMMUTABLE = 'public, max-age=31536000, immutable';
// The ASSETS binding resolves only the path, so build state URLs on a fixed
// origin instead of the client-controlled request URL.
const ASSETS_ORIGIN = 'https://assets.invalid';
const MAX_STATE_BYTES = 512_000;

function failure(status: number, message: string): Response {
  return new Response(message, {
    status,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const declared = Number(response.headers.get('Content-Length') || 0);
  if (declared > MAX_STATE_BYTES) throw new RangeError('preview state is too large');
  if (!response.body) throw new SyntaxError('preview state has no body');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_STATE_BYTES) {
      await reader.cancel();
      throw new RangeError('preview state is too large');
    }
    chunks.push(value);
  }
  const payload = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    payload.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder().decode(payload));
}

export async function handlePreviewRequest(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  cache: Cache = caches.default,
): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return failure(404, 'Not found');
  }
  const identity = parsePreviewRoute(new URL(request.url).pathname);
  if (!identity) return failure(404, 'Not found');

  // Key on origin + pathname so query strings and fragments cannot force re-renders.
  const keyUrl = new URL(request.url);
  keyUrl.search = '';
  keyUrl.hash = '';
  const cacheKey = new Request(keyUrl.href, { method: 'GET' });
  const cached = await cache.match(cacheKey);
  if (cached) {
    return request.method === 'HEAD'
      ? new Response(null, { status: cached.status, headers: cached.headers })
      : cached;
  }

  const stateUrl = new URL(`/data/previews/${identity.kind}/${identity.hash}.json`, ASSETS_ORIGIN);
  let stateResponse: Response;
  try {
    stateResponse = await env.ASSETS.fetch(new Request(stateUrl));
  } catch (error) {
    console.error(JSON.stringify({ message: 'preview state fetch failed', ...identity, error: String(error) }));
    return failure(502, 'Preview state unavailable');
  }
  if (!stateResponse.ok) return failure(404, 'Preview state not found');
  let rawState: unknown;
  try {
    rawState = await readBoundedJson(stateResponse);
  } catch (error) {
    if (error instanceof RangeError) return failure(502, 'Preview state is too large');
    return failure(404, 'Preview state is invalid');
  }
  const state = validateState(rawState, identity);
  if (!state) return failure(404, 'Preview state does not match the route');

  let rendered: Response;
  try {
    rendered = await env.BROWSER.quickAction('screenshot', {
      html: renderCard(state),
      viewport: { width: 1200, height: 630 },
      screenshotOptions: { type: 'png', fullPage: false },
    });
  } catch (error) {
    console.error(JSON.stringify({ message: 'preview render failed', ...identity, error: String(error) }));
    return failure(502, 'Preview rendering failed');
  }
  if (!rendered.ok) {
    console.error(JSON.stringify({ message: 'preview render rejected', ...identity, status: rendered.status }));
    return failure(502, 'Preview rendering failed');
  }

  const headers = new Headers({ 'Cache-Control': IMMUTABLE, 'Content-Type': 'image/png' });
  const length = rendered.headers.get('Content-Length');
  if (length) headers.set('Content-Length', length);
  const response = new Response(rendered.body, {
    status: 200,
    headers,
  });
  ctx.waitUntil(cache.put(cacheKey, response.clone()));
  return request.method === 'HEAD'
    ? new Response(null, { status: response.status, headers: response.headers })
    : response;
}
