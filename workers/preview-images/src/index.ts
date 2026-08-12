import { handlePreviewRequest } from './handler';

export default {
  fetch(request, env, ctx): Promise<Response> {
    return handlePreviewRequest(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
