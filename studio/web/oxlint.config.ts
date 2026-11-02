import { defineConfig } from "oxlint";

export default defineConfig({
  ignorePatterns: ["dist/**", "tools/oxlint/code-quality/**"],
  jsPlugins: [
    { name: "code-quality", specifier: "./tools/oxlint/code-quality/index.ts" },
  ],
  rules: {
    "code-quality/no-chained-type-assertions": "error",
    "code-quality/no-conditional-empty-object-spread": "error",
    "code-quality/no-known-value-widening": "error",
    "code-quality/no-module-mocking": "error",
    "code-quality/no-object-parameters": "error",
    "code-quality/no-reflect-apply": "error",
    "code-quality/no-reflect-get": "error",
    "code-quality/no-runtime-typeof": "error",
    "code-quality/no-shape-in-symbol-names": "error",
    "code-quality/no-unknown-parameters": "error",
    "code-quality/no-unknown-returns": "error",
    "code-quality/no-unknown-type-aliases": "error",
    "code-quality/no-unsafe-dictionary-type": "error",
    "code-quality/no-widen-then-assert": "error",
    "code-quality/require-safety-comment-for-type-assertion": "error",
  },
});
