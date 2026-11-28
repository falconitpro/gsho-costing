// D-210: линтер интерфейса в CI. Начинаем с правил, которые ловят настоящие ошибки, а не стиль:
// хуки React только на верхнем уровне компонента (иначе экран падает при смене данных), неиспользуемые переменные и импорты.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  { linterOptions: { reportUnusedDisableDirectives: "off" } },   // в коде есть отметки для exhaustive-deps — правило включим при унификации
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" }],
      // стиль и «мягкие» правила — не блокируют сборку, разбираются при унификации интерфейса
      "@typescript-eslint/no-explicit-any": "off",
      "no-empty": "off",
      "no-useless-escape": "off",
      "@typescript-eslint/no-empty-object-type": "off",
      "prefer-const": "off",
      "@typescript-eslint/no-unused-expressions": "off",   // «условие && действие» — принятый в коде приём
    },
  },
);
