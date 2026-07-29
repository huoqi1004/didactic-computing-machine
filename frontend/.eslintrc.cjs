/* eslint-env node */
/**
 * Agent-Native ERP Frontend - ESLint 配置
 *
 * 基础配置: react-hooks + @typescript-eslint
 *
 * 依赖安装 (如尚未安装):
 *   npm install -D eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin eslint-plugin-react eslint-plugin-react-hooks
 */
module.exports = {
  // 根目录标记, 阻止 ESLint 向上级目录继续查找配置
  root: true,

  // 解析器: TypeScript ESLint 解析器
  parser: '@typescript-eslint/parser',

  // 解析器选项
  parserOptions: {
    ecmaVersion: 2020,
    sourceType: 'module',
    ecmaFeatures: {
      jsx: true,
    },
  },

  // 运行环境
  env: {
    browser: true,
    es2020: true,
    node: true,
  },

  // 插件
  plugins: [
    'react',
    'react-hooks',
    '@typescript-eslint',
  ],

  // 继承的规则集
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
  ],

  // 规则设置
  rules: {
    // --- TypeScript 规则 ---
    '@typescript-eslint/no-unused-vars': [
      'warn',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/no-explicit-any': 'warn',
    '@typescript-eslint/explicit-module-boundary-types': 'off',
    '@typescript-eslint/no-non-null-assertion': 'warn',

    // --- React 规则 ---
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    'react/jsx-uses-react': 'off',

    // --- React Hooks 规则 ---
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',

    // --- 通用规则 ---
    'no-console': ['warn', { allow: ['warn', 'error'] }],
    'no-debugger': 'warn',
    'prefer-const': 'error',
    'no-var': 'error',
    'eqeqeq': ['error', 'always'],
  },

  // 设置
  settings: {
    react: {
      version: 'detect',
    },
  },

  // 忽略文件
  ignorePatterns: [
    'dist/',
    'node_modules/',
    '*.config.js',
    '*.config.ts',
    'vite-env.d.ts',
  ],
};
