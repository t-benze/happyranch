/**
 * @/lib/format — the ONE canonical display-number formatter (THR-099
 * number-overflow fix).
 *
 * `formatTokens` is the relocated canonical compact formatter — it previously
 * lived in `features/audit/audit-narrative.ts` and was re-exported through
 * `@/hooks/tokens`. It now lives in this neutral shared module so any feature
 * can import it without crossing a feature boundary. `@/hooks/tokens` keeps a
 * back-compat re-export so existing Dashboard/Threads imports don't churn.
 *
 * `formatCount` is the exact-grouped counter helper the three folded locals
 * (`fmtNum` in UsagePage, `fmtInt` in HealthPage, `fmtTokens` in TraceTree)
 * converged onto for their sub-1000 / count paths. It NEVER compacts — a count
 * of 1000 is "1,000", not "1.0K" — so precise small counts stay exact.
 *
 * DISPLAY METRICS ONLY. Exact identifiers (task/thread/job/PR IDs, ports,
 * hashes, exit codes, config values) must NEVER be routed through these — they
 * keep the IdBadge / truncate+title pattern and their full literal value.
 */

/**
 * Compact, locale-aware token/sum formatter: `346_100 → "346.1K"`,
 * `3_707_054 → "3.7M"`. Below 1000 it returns `String(n)` verbatim (the
 * canonical winner over the folded locals' `toLocaleString()` — locked by
 * lib/format.test.ts).
 */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/**
 * Exact, thousands-grouped integer counter: `1000 → "1,000"`. Counts stay
 * precise (never compacted) — Health / session / event counts must read the
 * literal figure.
 */
export function formatCount(n: number): string {
  return n.toLocaleString();
}
