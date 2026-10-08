import { copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const eslint = new ESLint({ cwd: webRoot, allowInlineConfig: false });
const rule = 'owned-copy/no-untranslated-copy';
async function messages(source: string, file = 'src/host/CopyProbe.tsx') {
  const [result] = await eslint.lintText(source, { filePath: resolve(webRoot, file) });
  return result.messages.filter(message => message.ruleId === rule);
}

describe('C1-C5 real-config owned-copy gate', () => {
  it.each([
    'New heading',
    "{'Save now'}",
    '{`Save now`}',
    "{'Save ' + 'now'}",
    "{ready ? label : 'Save now'}",
    "{ready && 'Save now'}",
    "{label || 'Save now'}",
    "{label ?? 'Save now'}",
    '仅中文标题',
  ])('C1 rejects owned JSX/static-expression branch: %s', async child => {
    expect(await messages(`export function Probe() { return <h1>${child}</h1>; }`)).toEqual([
      expect.objectContaining({ ruleId: rule, message: expect.stringContaining('requires en + zh-CN'), line: 1 }),
    ]);
  });

  it.each(['placeholder', 'title', 'description', 'aria-label', 'aria-description', 'aria-valuetext', 'alt'])('C2 distinguishes user-facing %s from machine attributes', async attribute => {
    expect(await messages(`export const Probe = () => <input ${attribute}={'Owned ' + 'label'} className="text-fg" type="text" role="textbox" id="raw-id" aria-labelledby="raw-ref" data-testid="fixture" value="machine" />;`)).toEqual([
      expect.objectContaining({ message: expect.stringContaining(attribute) }),
    ]);
    expect(await messages(`export const Probe = () => <input ${attribute}={t('common.save')} id="raw-id" />;`)).toEqual([]);
  });

  it('C1-C3 accepts translations, props and authored/raw values with machine attributes', async () => {
    const source = `export function Probe({ label, brief, diagnostic, locale }) { return <section id="machine" role="region" data-testid="fixture"><h1>{t('common.save')}</h1><p>{render('key', { label })}</p><p>{renderTranslated(locale, 'key', {})}</p><p>{label}</p><pre>{diagnostic}</pre><p>{brief}</p><a href="/raw" title={label}>{' '}</a></section>; }`;
    expect(await messages(source)).toEqual([]);
  });

  it('C3 exact raw outcome exceptions never exempt an adjacent owned label or inline-disable', async () => {
    const source = readFileSync(resolve(webRoot, 'src/features/jobs/JobsPage.tsx'), 'utf8');
    expect(await messages(source, 'src/features/jobs/JobsPage.tsx')).toEqual([]);
    const added = source.replace('<div', '<div title="New owned title"');
    expect(added).not.toBe(source);
    const result = await messages(`/* eslint-disable ${rule} */\n${added}`, 'src/features/jobs/JobsPage.tsx');
    expect(result).toEqual([expect.objectContaining({ message: expect.stringContaining('New owned title') })]);
  });

  it.each([
    ['DialogContent', 'closeLabel'], ['Markdown', 'mermaidLoadingLabel'], ['TaskCard', 'labels'],
    ['MessageBubble', 'labels'], ['InboxRow', 'labels'], ['StatusBadge', 'waitingLabels'],
  ])('C4 requires presentation for direct mounted %s while preserving shared defaults', async (component, prop) => {
    const extras = component === 'StatusBadge' ? ' blockKind={task.block_kind}' : '';
    expect(await messages(`import { ${component} as Shared } from '@/design-system/patterns/example'; export const Probe = () => <Shared${extras} />;`)).toEqual([
      expect.objectContaining({ message: expect.stringContaining(`localized ${prop}`) }),
    ]);
    expect(await messages(`import { ${component} as Shared } from '@/design-system/patterns/example'; export const Probe = () => <Shared ${prop}={copy}${extras} />;`)).toEqual([]);
  });

  it.each(['src/main.tsx', 'src/App.tsx', 'src/routes.tsx', 'src/host/Probe.tsx', 'src/shared/Probe.tsx', 'src/design-system/patterns/Probe.tsx', 'src/features/agents/Probe.tsx'])('C5 final config covers production %s and preserves other rules', async file => {
    const config = await eslint.calculateConfigForFile(resolve(webRoot, file));
    expect(config.rules[rule][0]).toBe(2);
    expect(config.rules['@typescript-eslint/no-explicit-any'][0]).toBe(2);
    expect(await messages('export const Probe = () => <button>Save now</button>', file)).toEqual([
      expect.objectContaining({ ruleId: rule }),
    ]);
  });

  it('C5 fixtures/catalogs remain outside owned-copy checks', async () => {
    for (const file of ['src/fixture.test.tsx', 'src/Example.stories.tsx', 'src/test/example.tsx', 'src/lib/i18n/locales/example.ts']) {
      expect(await messages('export const Probe = () => <h1>Example copy</h1>', file)).toEqual([]);
    }
  });

  it('E02 real disk additional JSX-constant root is owned while translated/raw controls and import-only stay allowed', async () => {
    const root = mkdtempSync(join(webRoot, '.i18n-source-fixture-'));
    const put = (file: string, body: string): void => {
      mkdirSync(dirname(join(root, file)), { recursive: true });
      writeFileSync(join(root, file), body);
    };
    try {
      for (const file of ['index.html', 'tsconfig.json', 'vite.config.ts', 'eslint.config.js', 'package.json', 'scripts/i18n-source-inventory.mjs']) {
        mkdirSync(dirname(join(root, file)), { recursive: true });
        copyFileSync(join(webRoot, file), join(root, file));
      }
      put('src/App.tsx', 'export function App() { return <p>{label}</p>; }');
      put('src/Unused.tsx', 'export const ImportOnlyDialog = () => <section role="dialog">Unused declaration</section>;');
      const original = "import { App } from './App'; import { ImportOnlyDialog } from './Unused'; import ReactDOM, { createRoot } from 'react-dom/client'; createRoot(document.getElementById('root')!).render(<App />); function uncalled() { const dormant = <h1>Unused root declaration</h1>; createRoot(document.createElement('div')).render(dormant); }";
      put('src/main.tsx', `${original} const extra = <h1>Untranslated owned root heading</h1>; createRoot(document.body.appendChild(document.createElement('div'))).render(extra);`);
      const { inventorySource } = await import(resolve(webRoot, 'scripts/i18n-source-inventory.mjs'));
      const inventory = inventorySource(root);
      expect(inventory.mountedSymbols).toContain('src/App.tsx#App');
      expect(inventory.mountedSymbols).toContain('src/main.tsx#extra');
      expect(inventory.dialogs).toEqual([]);
      expect(inventory.mountedSymbols).not.toContain('src/main.tsx#uncalled');
      expect(inventory.copies).toContainEqual(expect.objectContaining({ path: 'src/main.tsx', symbol: 'extra', literal: 'Untranslated owned root heading' }));
      const isolation = { languageOptions: { parserOptions: { tsconfigRootDir: root } } };
      const eslint = new ESLint({ cwd: root, overrideConfig: isolation, allowInlineConfig: false });
      const [result] = await eslint.lintFiles(['src/main.tsx']);
      expect(result.fatalErrorCount).toBe(0);
      expect(result.messages.filter(message => message.ruleId === rule)).toEqual([
        expect.objectContaining({ message: expect.stringContaining('Untranslated owned root heading') }),
      ]);
      // Each root uses a fresh config module/inventory cache after disk changes.
      put('src/main.tsx', `${original} const extra = <><h1>{t('common.save')}</h1><pre>{rawDiagnostic}</pre><input value="machine-id" /></>; const alias = extra; const attached = ReactDOM.createRoot(document.body.appendChild(document.createElement('div'))); const mounted = attached.render(alias);`);
      copyFileSync(join(root, 'scripts/i18n-source-inventory.mjs'), join(root, 'scripts/positive-inventory.mjs'));
      put('positive.config.js', readFileSync(join(root, 'eslint.config.js'), 'utf8').replace('./scripts/i18n-source-inventory.mjs', './scripts/positive-inventory.mjs'));
      const positive = new ESLint({ cwd: root, overrideConfigFile: join(root, 'positive.config.js'), overrideConfig: isolation, allowInlineConfig: false });
      expect(inventorySource(root).mountedSymbols).toContain('src/main.tsx#extra');
      const [allowed] = await positive.lintFiles(['src/main.tsx']);
      expect(allowed.fatalErrorCount).toBe(0);
      expect(allowed.messages.filter(message => message.ruleId === rule)).toEqual([]);
      put('src/main.tsx', `${original} const extra = makeUnknownTree(); createRoot(document.body.appendChild(document.createElement('div'))).render(extra);`);
      expect(() => inventorySource(root)).toThrow(/unsupported render root src\/main.tsx.*extra/);
      copyFileSync(join(root, 'scripts/i18n-source-inventory.mjs'), join(root, 'scripts/refusal-inventory.mjs'));
      put('refusal.config.js', readFileSync(join(root, 'eslint.config.js'), 'utf8').replace('./scripts/i18n-source-inventory.mjs', './scripts/refusal-inventory.mjs'));
      const refusal = new ESLint({ cwd: root, overrideConfigFile: join(root, 'refusal.config.js'), overrideConfig: isolation, allowInlineConfig: false });
      const [refused] = await refusal.lintFiles(['src/main.tsx']);
      expect(refused.fatalErrorCount).toBe(0);
      expect(refused.messages.filter(message => message.ruleId === rule)).toEqual([
        expect.objectContaining({ message: expect.stringMatching(/unsupported render root src\/main.tsx.*extra/) }),
      ]);
    } finally { rmSync(root, { recursive: true }); }
  });

  it.each(['literal', 'translated', 'declaration-only'] as const)('R1 complete shipping source imported root %s retains real-config ownership controls', async mode => {
    const root = mkdtempSync(join(webRoot, '.i18n-source-fixture-'));
    try {
      cpSync(join(webRoot, 'src'), join(root, 'src'), { recursive: true });
      for (const file of ['index.html', 'tsconfig.json', 'vite.config.ts', 'eslint.config.js', 'package.json', 'scripts/i18n-source-inventory.mjs']) {
        mkdirSync(dirname(join(root, file)), { recursive: true });
        copyFileSync(join(webRoot, file), join(root, file));
      }
      const { inventorySource, auditCopyExceptions } = await import(resolve(webRoot, 'scripts/i18n-source-inventory.mjs'));
      const baseline = inventorySource(root);
      expect(baseline.mountedSymbols).toContain('src/App.tsx#App');
      expect(auditCopyExceptions(baseline)).toEqual({ omissions: [], stale: [] });
      const file = 'src/host/reviewer-extra-root.tsx';
      const literal = 'Imported owned root heading';
      const child = mode === 'translated' ? "<><h1>{t('common.save')}</h1><pre>{rawDiagnostic}</pre><input value=\"machine-id\" /></>" : `<h1>${literal}</h1>`;
      writeFileSync(join(root, file), `import {createRoot} from 'react-dom/client'; const extra=${child};
        export const ImportOnlyDialog = () => <section role="dialog">Unused imported declaration</section>;
        function uncalled() { createRoot(document.createElement('div')).render(<h1>Uncalled root heading</h1>); }
        ${mode === 'declaration-only' ? '' : "createRoot(document.body.appendChild(document.createElement('div'))).render(extra);"}`);
      writeFileSync(join(root, 'src/main.tsx'), readFileSync(join(root, 'src/main.tsx'), 'utf8') + "\nimport './host/reviewer-extra-root';\n");
      const actual = inventorySource(root);
      const results = await new ESLint({ cwd: root, overrideConfig: { languageOptions: { parserOptions: { tsconfigRootDir: root } } }, allowInlineConfig: false }).lintFiles(['src/main.tsx', file]);
      // Parser/setup failures must never substitute for the contract's RED.
      expect(results.map(result => result.fatalErrorCount)).toEqual([0, 0]);
      const owned = results.flatMap(result => result.messages.filter(message => message.ruleId === rule));
      expect(owned, 'R1 actual imported root literal must be rejected by ordinary shipping ESLint').toEqual(mode === 'literal' ? [expect.objectContaining({ message: expect.stringContaining(literal) })] : []);
      expect(actual.modules).toContain(file);
      expect(actual.mountedSymbols.includes(`${file}#extra`)).toBe(mode !== 'declaration-only');
      expect(actual.mountedSymbols).not.toContain(`${file}#ImportOnlyDialog`);
      expect(actual.mountedSymbols).not.toContain(`${file}#uncalled`);
      expect(actual.dialogs).toEqual(baseline.dialogs);
      expect(actual.routes).toEqual(baseline.routes);
      expect(auditCopyExceptions(actual)).toEqual({ omissions: mode === 'literal' ? [expect.objectContaining({ path: file, symbol: 'extra', literal })] : [], stale: [] });
    } finally { rmSync(root, { recursive: true }); }
  });

  it('C1-C5 authoritative real disk scan has no owned omissions, stale exceptions or inline waiver', async () => {
    const scriptPath = resolve(webRoot, 'scripts/i18n-source-inventory.mjs');
    const { inventorySource, auditCopyExceptions } = await import(scriptPath);
    expect(auditCopyExceptions(inventorySource(webRoot))).toEqual({ omissions: [], stale: [] });
    const results = await eslint.lintFiles(['src/**/*.{ts,tsx}']);
    const errors = results.flatMap(result => result.messages.filter(message => message.ruleId === rule).map(message => `${result.filePath}:${message.line} ${message.message}`));
    expect(errors).toEqual([]);
  }, 60_000);
});
