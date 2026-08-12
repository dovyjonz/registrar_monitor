import { cloudflareTest } from '@cloudflare/vitest-pool-workers';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [
    cloudflareTest({
      miniflare: {
        compatibilityDate: '2026-08-12',
        compatibilityFlags: ['nodejs_compat'],
        bindings: {
          PAGES_ORIGIN: 'https://registrar-monitor.pages.dev',
        },
      },
    }),
  ],
});
