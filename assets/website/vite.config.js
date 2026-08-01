import { defineConfig } from 'vite';
import { resolve } from 'path';

const outputDir = process.env.REGISTRAR_VITE_OUTPUT_DIR
  ? resolve(process.env.REGISTRAR_VITE_OUTPUT_DIR)
  : resolve(__dirname, 'public/assets');
const inputs = process.env.REGISTRAR_MAIN_ONLY === '1'
  ? { main: resolve(__dirname, 'src/main.js') }
  : {
      main: resolve(__dirname, 'src/main.js'),
      prototype: resolve(__dirname, 'src/prototype.js'),
    };

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
      input: inputs
    }
  },
  publicDir: false, // Disable public dir copying to avoid recursion/conflicts
  // Base URL for assets in production
  base: '/assets/'
});
