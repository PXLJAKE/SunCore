// SPDX-License-Identifier: Apache-2.0
import js from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import svelteParser from 'svelte-eslint-parser';
import tseslint from 'typescript-eslint';
import noHardcodedText from './eslint/no-hardcoded-text.js';

const local = { rules: { 'no-hardcoded-text': noHardcodedText } };

export default tseslint.config(
  {
    ignores: [
      'dist',
      'node_modules',
      'playwright-report',
      'test-results',
      'src/lib/api/schema.d.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs['flat/recommended'],
  {
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: {
      parser: svelteParser,
      parserOptions: { parser: tseslint.parser, extraFileExtensions: ['.svelte'] },
    },
  },
  {
    files: ['**/*.ts'],
    languageOptions: { parser: tseslint.parser },
  },
  {
    files: ['src/**/*.svelte'],
    plugins: { local },
    rules: { 'local/no-hardcoded-text': 'error' },
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'svelte/require-each-key': 'error',
    },
  },
);
