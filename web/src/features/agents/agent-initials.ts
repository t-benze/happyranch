/**
 * Derive up to two uppercase avatar initials from an agent's name.
 *
 * Agent names are dynamic strings discovered from org files (no static
 * enum), so initials are computed generically rather than mapped. The
 * rule: split on word separators (`_`, `-`, whitespace), take the first
 * letter of the first two segments. Single-segment names fall back to the
 * first two letters of that segment.
 *
 *   engineering_manager -> "EM"   code_reviewer -> "CR"
 *   dev_agent           -> "DA"   qa_engineer   -> "QE"
 *   founder             -> "FO"
 */
export function agentInitials(name: string): string {
  const segments = name
    .split(/[_\-\s]+/)
    .filter((s) => /[a-z0-9]/i.test(s));
  if (segments.length === 0) return '?';
  if (segments.length === 1) {
    return segments[0].slice(0, 2).toUpperCase();
  }
  return (segments[0][0] + segments[1][0]).toUpperCase();
}
