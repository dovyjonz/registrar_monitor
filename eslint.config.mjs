import js from "@eslint/js";
import globals from "globals";
import { defineConfig } from "eslint/config";

export default defineConfig([
  {
    files: ["**/*.{js,mjs,cjs}"],
    plugins: { js },
    extends: ["js/recommended"],
    languageOptions: {
      globals: {
        ...globals.browser,
        // Chart.js global
        Chart: "readonly",
        // Template-injected globals (defined in HTML before app.js loads)
        DATA: "readonly",
        MILESTONES: "readonly",
        COMBINED_DATA: "readonly",
      }
    }
  },
]);
