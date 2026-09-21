/**
 * English catalog — the source-of-truth key set for the W1 i18n contract.
 *
 * W1 ships the foundation only: this is a deliberately small, first-party
 * catalog (no third-party i18n dependency). Route/page translation is W2-W4;
 * `coverage.ts` tracks every mounted surface that is still English-only so
 * fallback is never mistaken for coverage.
 */
import type { Catalog } from '../catalog';

export const en = {
  'app.name': 'HappyRanch',
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.save': 'Save',
  'common.dismiss': 'Dismiss',
  'common.language': 'Language',
  'common.languageDescription': 'Choose the interface language.',
  'common.englishOnlyNotice': 'This surface is not yet available in Chinese.',
  'common.translatedProbe': 'Translated probe',
  'common.greeting': 'Hello, {name}',
  'common.movableSlots': '{subject} before {object}',
  'common.itemCount': {
    one: '{count} item',
    other: '{count} items',
  },
  'common.filesSelected': {
    one: '{count} file selected by {name}',
    other: '{count} files selected by {name}',
  },
  'coverage.title': 'Translation coverage',
  'coverage.englishOnly': 'English only — not yet migrated',
  'coverage.notApplicable': 'No user-facing copy',
  'coverage.summary': '{translated} of {total} namespaces translated',
} as const satisfies Catalog;

export default en;
