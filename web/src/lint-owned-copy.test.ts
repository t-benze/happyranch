import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
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

  it('C1-C5 authoritative real disk scan has no owned omissions, stale exceptions or inline waiver', async () => {
    const scriptPath = resolve(webRoot, 'scripts/i18n-source-inventory.mjs');
    const { inventorySource, auditCopyExceptions } = await import(scriptPath);
    expect(auditCopyExceptions(inventorySource(webRoot))).toEqual({ omissions: [], stale: [] });
    const results = await eslint.lintFiles(['src/**/*.{ts,tsx}']);
    const errors = results.flatMap(result => result.messages.filter(message => message.ruleId === rule).map(message => `${result.filePath}:${message.line} ${message.message}`));
    expect(errors).toEqual([]);
  }, 60_000);
});
