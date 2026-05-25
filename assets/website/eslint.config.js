const js = require('@eslint/js');
const globals = require('globals');

module.exports = [
    {
        ignores: ['dist/**', 'node_modules/**', 'public/**'],
    },
    js.configs.recommended,
    {
        files: ['**/*.js'],
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: 'module',
            globals: {
                ...globals.browser,
                ...globals.node,
            },
        },
        rules: {
            'no-unused-vars': 'off',
        },
    },
];
