/**
 * Pure, provider-agnostic helpers for the Skills Catalog surface (THR-092
 * Slice 1). Filtering + product-language mappers live here, apart from the
 * JSX, so the copy discipline is unit-tested: NO user-facing "active", NO
 * permission / approve / admit language — skills are guidance visibility only.
 */
import type { CatalogSkillItem } from '@/hooks/skills';

/**
 * Catalog source filter. `Bundled` and `Custom` map 1:1 to the daemon's
 * `?filter=` query param (see `listSkillsCatalog`); `all` sends no param.
 * These are the ONLY catalog filters in v1 — validation state is a per-skill
 * label, never a top-level filter (product_lead handoff §1).
 */
export type CatalogFilter = 'all' | 'Bundled' | 'Custom';

/** Bundled groups platform-provided skills — managed skills AND read-only
 *  system contracts (handoff: "System contracts should appear inside
 *  Bundled"). Custom is user-authored. Mirrors the server's `?filter=`
 *  bucketing so the mock provider and server agree. */
export function isBundled(item: CatalogSkillItem): boolean {
  return item.type === 'managed' || item.type === 'system_contract';
}

export function matchesFilter(
  item: CatalogSkillItem,
  filter: CatalogFilter,
): boolean {
  if (filter === 'all') return true;
  if (filter === 'Bundled') return isBundled(item);
  return item.type === 'user_authored';
}

export function applyFilter(
  items: CatalogSkillItem[],
  filter: CatalogFilter,
): CatalogSkillItem[] {
  return items.filter((item) => matchesFilter(item, filter));
}

/** Read-only system contracts get NO toggle, edit, or unassign affordance
 *  anywhere on the catalog (copy discipline: shown read-only / non-toggleable). */
export function isReadOnly(item: CatalogSkillItem): boolean {
  return item.system_contract || item.visibility_category === 'read_only';
}

export type ValidationTone = 'positive' | 'neutral' | 'attention';

export interface ValidationLabel {
  text: string;
  tone: ValidationTone;
}

/** Per-skill validation_state → product-language status label. "Needs
 *  attention" is the label for a failed technical validation (handoff §3);
 *  never permission wording. */
export function validationLabel(
  state: CatalogSkillItem['validation_state'],
): ValidationLabel {
  switch (state) {
    case 'validated':
      return { text: 'Validated', tone: 'positive' };
    case 'failed_validation':
      return { text: 'Needs attention', tone: 'attention' };
    case 'in_catalog':
    default:
      return { text: 'In catalog', tone: 'neutral' };
  }
}

/** Source badge label — lowercase per the catalog visual direction. */
export function sourceLabel(item: CatalogSkillItem): 'bundled' | 'custom' {
  return isBundled(item) ? 'bundled' : 'custom';
}

/** Header rollup: how many catalog rows carry a failed technical validation
 *  and therefore surface a "Needs attention" status. Drives the "N need
 *  attention" summary pill. Does NOT fold in `has_assigned_not_yet_effective`
 *  — that is a softer "takes effect next session" state, not a failure. */
export function needsAttentionCount(items: CatalogSkillItem[]): number {
  return items.filter((i) => i.validation_state === 'failed_validation').length;
}
