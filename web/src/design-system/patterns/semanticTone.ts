/**
 * semanticTone — shared status/type → colour-tone map (THR-099 Deliverable-2
 * Batch 1). The design uses ONE consistent colour vocabulary across surfaces;
 * the current app renders near-monochrome grey pills (or the wrong words) via
 * per-feature ad-hoc maps. This module is the single source of truth those
 * feature badges converge onto in Batches 2–3.
 *
 * Two pure layers:
 *   1. tone  →  Tailwind token classes (text + tinted bg). EXISTING named
 *      tokens only — no new tokens, no arbitrary values (eslint hard error in
 *      features). blue = --color-info(+ -soft); amber = --color-attention-*;
 *      green = --color-status-open/--color-tier-green-tint; red =
 *      --color-status-escalated/--color-tier-red-tint; grey = --color-status-
 *      archived + border.
 *   2. semantic value → tone. Covers the vocabulary the design mockups name:
 *      kb type (sop/reference/ruling), thread state (open/archived), and
 *      job/audit/dashboard outcomes (exit codes, done/merged/superseded/
 *      accepted). Unknown values fall back to `neutral` — an explicit,
 *      safe grey default.
 *
 * Pure data + pure functions: no React, no hooks, no `.tsx` — so it carries no
 * registry `meta` block and adds no design-system registry surface.
 */

export type Tone = 'positive' | 'info' | 'attention' | 'danger' | 'neutral';

/** Tone → Tailwind token classes (text colour + tinted fill). */
export const TONE_CLASS: Record<Tone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  info: 'text-info bg-info-soft',
  attention: 'text-attention-text bg-attention-soft',
  danger: 'text-status-escalated bg-tier-red-tint',
  neutral: 'text-status-archived border border-border-default bg-transparent',
};

/**
 * Semantic value → tone. Keyed on lower-cased values; see the design
 * vocabulary in THR-099 §2 theme 2.
 */
const VALUE_TONE: Record<string, Tone> = {
  // kb entry type
  sop: 'positive',
  reference: 'info',
  ruling: 'attention',
  // thread state — design vocabulary is open/archived (NOT active/done)
  open: 'info',
  archived: 'neutral',
  // job / audit / dashboard activity outcomes
  done: 'positive',
  merged: 'info',
  superseded: 'neutral',
  accepted: 'positive',
};

/**
 * Resolve a semantic status/type value to its tone. Case- and
 * whitespace-insensitive. `exit N` outcomes resolve by code: 0 → positive
 * (green), any non-zero → danger (red). Unknown values → neutral.
 */
export function toneFor(value: string): Tone {
  const key = value.trim().toLowerCase();
  if (key.startsWith('exit')) {
    const code = key.replace(/[^0-9]/g, '');
    return code === '' || code === '0' ? 'positive' : 'danger';
  }
  return VALUE_TONE[key] ?? 'neutral';
}

/** Convenience: value → the tone's Tailwind token classes. */
export function toneClass(value: string): string {
  return TONE_CLASS[toneFor(value)];
}
