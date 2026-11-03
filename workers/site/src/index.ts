import { handlePreviewRequest } from './handler';

// Only /preview/* and /api/* reach this Worker (assets.run_worker_first); every
// other request is answered by Static Assets without a Worker invocation.
// Telegram Mini App routes dispatch on /api/miniapp/* here.
export default {
  fetch(request, env, ctx): Promise<Response> {
    return handlePreviewRequest(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
