import { defineConfig } from 'vite';
import { resolve } from 'path';

const outputDir = process.env.REGISTRAR_VITE_OUTPUT_DIR
  ? resolve(process.env.REGISTRAR_VITE_OUTPUT_DIR)
  : resolve(import.meta.dirname, 'public/assets');

export default defineConfig({
  build: {
    // Output to public/assets
    outDir: outputDir,
    // Generated HTML can reference an earlier hashed bundle until Python patches
    // every page, so preserve prior bundles during standalone frontend builds.
    emptyOutDir: false,
    assetsDir: '', // Put assets directly in outDir to avoid assets/assets/
    manifest: true, // Generate manifest.json for Python to read
    rollupOptions: {
      input: { main: resolve(import.meta.dirname, 'src/main.js') }
    }
  },
  publicDir: false, // Disable public dir copying to avoid recursion/conflicts
  // Base URL for assets in production
  base: '/assets/'
});
