import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // React 19 RC strict rule — codebase uses many legitimate patterns
      // (mounted flags, prop-sync, fetch-on-mount) that this rule mis-flags.
      'react-hooks/set-state-in-effect': 'off',
      // Reading a ref's `.current` for a one-shot static read in JSX is fine
      // here (snapshot value, not animated via refs).
      'react-hooks/refs': 'off',
    },
  },
])
