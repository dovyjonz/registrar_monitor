import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    // Output to public/assets
    outDir: resolve(__dirname, 'public/assets'),
    // Ensure we don't delete other files in public if any (though we probably should control it)
    emptyOutDir: true,
    assetsDir: '', // Put assets directly in outDir to avoid assets/assets/
    manifest: true, // Generate manifest.json for Python to read
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'src/main.js'),
      }
    }
  },
  publicDir: false, // Disable public dir copying to avoid recursion/conflicts
  // Base URL for assets in production
  base: '/assets/'
});
