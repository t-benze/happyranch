import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";

const webRoot = path.resolve(process.argv[2] ?? path.join(import.meta.dirname, "../web"));
process.chdir(webRoot);
const webRequire = createRequire(path.join(webRoot, "package.json"));
const tailwindApiUtils = webRequire("tailwind-api-utils");
const OriginalTailwindUtils = tailwindApiUtils.TailwindUtils;
let constructions = 0;

tailwindApiUtils.TailwindUtils = class InstrumentedTailwindUtils extends OriginalTailwindUtils {
  constructor(...args) {
    constructions += 1;
    super(...args);
  }
};

const { ESLint } = webRequire("eslint");
const eslint = new ESLint({ cwd: webRoot });
const source = 'export const Probe = () => <div className="flex block" />;\n';

await eslint.lintText(source, { filePath: path.join(webRoot, "src/lint-cache-probe-a.tsx") });
await eslint.lintText(source, { filePath: path.join(webRoot, "src/lint-cache-probe-b.tsx") });

assert.equal(
  constructions,
  1,
  `expected one shared eslint-plugin-tailwindcss compatibility model, constructed ${constructions}`,
);
console.log(`Tailwind compatibility model constructions across two files: ${constructions}`);
