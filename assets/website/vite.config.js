import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    // Output to public/assets
    outDir: resolve(__dirname, 'public/assets'),
    // Generated HTML can reference an earlier hashed bundle until Python patches
    // every page, so preserve prior bundles during standalone frontend builds.
    emptyOutDir: false,
    assetsDir: '', // Put assets directly in outDir to avoid assets/assets/
    manifest: true, // Generate manifest.json for Python to read
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'src/main.js'),
        prototype: resolve(__dirname, 'src/prototype.js'),
      }
    }
  },
  publicDir: false, // Disable public dir copying to avoid recursion/conflicts
  // Base URL for assets in production
  base: '/assets/'
});
