/**
 * Maps daemon error codes to human-friendly messages for the threads feature.
 * Re-exports from the shared lib so feature folders and shared modules
 * both import from the same source of truth.
 *
 * THR-118 W3a: the Threads surface copy (formerly `THREADS_STRINGS`) now lives
 * in the typed i18n catalog under `threads.*` keys.
 */
export { THREAD_ERROR_STRINGS, describeError } from '@/lib/threadErrors';
