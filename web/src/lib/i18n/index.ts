/**
 * @/lib/i18n — public barrel for the W1 i18n foundation.
 *
 * Exports: locale identity + synchronous resolution + the injectable
 * preference adapter (`locale`), the typed co-loaded catalog + renderer
 * (`catalog`), explicit-locale display formatters (`format`), and the checked
 * mounted-route coverage manifest (`coverage`).
 */
export * from './locale';
export * from './catalog';
export * from './format';
export * from './coverage';
