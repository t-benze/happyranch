/**
 * @/lib/i18n/catalog — the typed, static, co-loaded en/zh-CN message contract.
 *
 * Design rules enforced here:
 *   - `MessageKey` is the literal key union derived from the English catalog;
 *     the Chinese catalog is typed `Record<MessageKey, MessageValue>` so a
 *     missing key is a **compile-time** error.
 *   - Placeholders are named (`{name}`) and there is no positional ordering, so
 *     translated slots may move freely.
 *   - Plural messages declare explicit per-locale forms: English requires
 *     `one` + `other`, Chinese requires `other`. `validateCatalogParity`
 *     re-checks key set, shape, required/unexpected plural forms and
 *     placeholder sets (known drift fails tests).
 *   - Rendering never injects HTML: `translate` returns a plain string and
 *     `renderTranslated` returns escaped text/React nodes.
 *   - An unexpected runtime gap (a key absent even from English) renders
 *     nothing — never a raw key.
 */
import { createElement, Fragment, type ReactNode } from 'react';
import { DEFAULT_LOCALE, type Locale } from './locale';
import { en } from './locales/en';
import { zhCN } from './locales/zh-CN';

export type PluralCategory = 'one' | 'other';

export type PluralForms = {
  other: string;
} & Partial<Record<PluralCategory, string>>;

export type MessageValue = string | PluralForms;

/** Runtime-facing catalog shape (key -> message). */
export type Catalog = Record<string, MessageValue>;

/** Literal key union — the typed-key contract. */
export type MessageKey = keyof typeof en;

export type MessageParams = Record<string, string | number>;
export type NodeParams = Record<string, ReactNode | string | number>;

export const catalogs: Record<Locale, Catalog> = {
  en: en as unknown as Catalog,
  'zh-CN': zhCN,
};

/** Plural categories a locale must declare, and may not exceed. */
export const PLURAL_FORMS_BY_LOCALE: Record<Locale, readonly PluralCategory[]> = {
  en: ['one', 'other'],
  'zh-CN': ['other'],
};

const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;

export function extractPlaceholders(template: string): string[] {
  const found = new Set<string>();
  for (const match of template.matchAll(PLACEHOLDER)) found.add(match[1]);
  return [...found].sort();
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

export type CatalogParityIssueKind =
  | 'missing-key'
  | 'unexpected-key'
  | 'shape-mismatch'
  | 'placeholder-mismatch'
  | 'plural-form-missing'
  | 'plural-form-unexpected';

export interface CatalogParityIssue {
  key: string;
  locale: Locale;
  kind: CatalogParityIssueKind;
  detail: string;
}

/** Structural parity check between the English and Chinese catalogs. */
export function validateCatalogParity(
  enCatalog: Catalog = catalogs.en,
  zhCatalog: Catalog = catalogs['zh-CN'],
): CatalogParityIssue[] {
  const issues: CatalogParityIssue[] = [];
  const enKeys = new Set(Object.keys(enCatalog));
  const zhKeys = new Set(Object.keys(zhCatalog));

  for (const key of enKeys) {
    if (!zhKeys.has(key)) {
      issues.push({ key, locale: 'zh-CN', kind: 'missing-key', detail: 'missing from zh-CN' });
    }
  }
  for (const key of zhKeys) {
    if (!enKeys.has(key)) {
      issues.push({ key, locale: 'zh-CN', kind: 'unexpected-key', detail: 'not present in en' });
    }
  }

  for (const key of enKeys) {
    if (!zhKeys.has(key)) continue;
    const enValue = enCatalog[key];
    const zhValue = zhCatalog[key];
    const enIsPlural = typeof enValue !== 'string';
    const zhIsPlural = typeof zhValue !== 'string';

    if (enIsPlural !== zhIsPlural) {
      issues.push({ key, locale: 'zh-CN', kind: 'shape-mismatch', detail: 'string vs plural object' });
      continue;
    }

    if (!enIsPlural) {
      const enPlaceholders = extractPlaceholders(enValue as string);
      const zhPlaceholders = extractPlaceholders(zhValue as string);
      if (!sameList(enPlaceholders, zhPlaceholders)) {
        issues.push({
          key,
          locale: 'zh-CN',
          kind: 'placeholder-mismatch',
          detail: `en={${enPlaceholders.join(',')}} zh-CN={${zhPlaceholders.join(',')}}`,
        });
      }
      continue;
    }

    const enForms = enValue as PluralForms;
    const zhForms = zhValue as PluralForms;
    const categoriesByLocale: Array<[Locale, PluralForms]> = [
      ['en', enForms],
      ['zh-CN', zhForms],
    ];

    const placeholderBands: Array<{ locale: Locale; form: string; placeholders: string[] }> = [];
    for (const [locale, forms] of categoriesByLocale) {
      const required = PLURAL_FORMS_BY_LOCALE[locale];
      for (const category of required) {
        const template = forms[category];
        if (typeof template !== 'string') {
          issues.push({ key, locale, kind: 'plural-form-missing', detail: category });
        } else {
          placeholderBands.push({ locale, form: category, placeholders: extractPlaceholders(template) });
        }
      }
      for (const category of Object.keys(forms) as PluralCategory[]) {
        if (!required.includes(category)) {
          issues.push({ key, locale, kind: 'plural-form-unexpected', detail: category });
        }
      }
    }

    const baseline = placeholderBands[0]?.placeholders ?? [];
    for (const band of placeholderBands) {
      if (!sameList(baseline, band.placeholders)) {
        issues.push({
          key,
          locale: band.locale,
          kind: 'placeholder-mismatch',
          detail: `${band.form}={${band.placeholders.join(',')}} vs {${baseline.join(',')}}`,
        });
      }
    }
  }

  return issues;
}

export function assertCatalogParity(
  enCatalog: Catalog = catalogs.en,
  zhCatalog: Catalog = catalogs['zh-CN'],
): void {
  const issues = validateCatalogParity(enCatalog, zhCatalog);
  if (issues.length > 0) {
    const summary = issues.map((issue) => `${issue.locale}:${issue.key} (${issue.kind})`).join(', ');
    throw new Error(`i18n catalog parity failure: ${summary}`);
  }
}

/**
 * Runtime English fallback. Known gaps are caught by the parity test and by
 * typecheck; an unexpected runtime gap resolves to `undefined` so callers can
 * render nothing rather than a raw key.
 */
export function lookupMessage(locale: Locale, key: string): MessageValue | undefined {
  const local = catalogs[locale] as unknown as Record<string, MessageValue | undefined>;
  const value = local?.[key];
  if (value !== undefined) return value;
  const fallback = catalogs[DEFAULT_LOCALE] as unknown as Record<string, MessageValue | undefined>;
  return fallback?.[key];
}

export function selectPluralCategory(locale: Locale, count: number): PluralCategory {
  if (locale === 'en') return count === 1 ? 'one' : 'other';
  return 'other';
}

/** Named-slot interpolation. Missing values become empty, never `{name}`. */
export function interpolate(template: string, params: MessageParams = {}): string {
  return template.replace(PLACEHOLDER, (_match, name: string) => {
    const value = params[name];
    return value === undefined || value === null ? '' : String(value);
  });
}

function pluralTemplate(
  locale: Locale,
  value: PluralForms,
  params: Record<string, unknown>,
): string {
  const raw = params.count;
  const count = typeof raw === 'number' ? raw : Number(raw);
  const category = Number.isFinite(count) ? selectPluralCategory(locale, count) : 'other';
  return value[category] ?? value.other;
}

/** Plain-string translation (safe: output is text, never HTML). */
export function translate(locale: Locale, key: MessageKey, params: MessageParams = {}): string {
  const value = lookupMessage(locale, key);
  if (value === undefined) return '';
  const template = typeof value === 'string' ? value : pluralTemplate(locale, value, params);
  return interpolate(template, params);
}

function renderTemplate(template: string, params: NodeParams): ReactNode {
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const match of template.matchAll(PLACEHOLDER)) {
    const at = match.index ?? 0;
    if (at > cursor) parts.push(template.slice(cursor, at));
    const value = params[match[1]];
    parts.push(value === undefined || value === null ? '' : value);
    cursor = at + match[0].length;
  }
  if (cursor < template.length) parts.push(template.slice(cursor));
  return createElement(
    Fragment,
    null,
    ...parts.map((part, partIndex) => createElement(Fragment, { key: partIndex }, part)),
  );
}

/**
 * React-node translation. String parameters and translated text render as
 * escaped text; callers may pass elements for intentional inline structure.
 * There is deliberately no `dangerouslySetInnerHTML` path.
 */
export function renderTranslated(
  locale: Locale,
  key: MessageKey,
  params: NodeParams = {},
): ReactNode {
  const value = lookupMessage(locale, key);
  if (value === undefined) return null;
  const template = typeof value === 'string' ? value : pluralTemplate(locale, value, params);
  return renderTemplate(template, params);
}

export { en, zhCN };
