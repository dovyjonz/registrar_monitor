import { handlePreviewRequest } from './handler';

// Only /preview/* reaches this Worker (assets.run_worker_first); every other
// request is answered by Static Assets without a Worker invocation. The Telegram
// Mini App API is served by the bot process, not by this Worker.
export default {
  fetch(request, env, ctx): Promise<Response> {
    return handlePreviewRequest(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
