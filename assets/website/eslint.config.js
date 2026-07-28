const js = require('@eslint/js');
const globals = require('globals');

module.exports = [
    {
        ignores: ['dist/**', 'node_modules/**', 'public/**'],
    },
    js.configs.recommended,
    {
        files: ['**/*.{js,mjs}'],
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: 'module',
            globals: {
                ...globals.browser,
                ...globals.node,
            },
        },
        rules: {
            complexity: ['error', 20],
            'eqeqeq': 'error',
            'max-depth': ['error', 4],
            'max-nested-callbacks': ['error', 4],
            'max-params': ['error', 5],
            'no-duplicate-imports': 'error',
            'no-else-return': 'error',
            'no-eval': 'error',
            'no-implied-eval': 'error',
            'no-implicit-coercion': 'error',
            'no-lonely-if': 'error',
            'no-new-func': 'error',
            'no-return-assign': 'error',
            'no-template-curly-in-string': 'error',
            'no-throw-literal': 'error',
            'no-unneeded-ternary': 'error',
            'no-useless-concat': 'error',
            'no-useless-return': 'error',
            'no-var': 'error',
            'object-shorthand': 'error',
            'prefer-const': 'error',
            'prefer-object-has-own': 'error',
            'prefer-template': 'error',
        },
        linterOptions: {
            noInlineConfig: true,
            reportUnusedDisableDirectives: 'error',
        },
    },
];
