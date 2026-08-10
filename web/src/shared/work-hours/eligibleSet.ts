/**
 * Pure client-side preview of the resulting eligible agent set for a
 * hypothetical work-hours eligibility selector. The SERVER is the single
 * validation authority; this is display/preview logic only.
 */
export function eligibleSet(
  agentNames: string[],
  selector: { mode: string; include: string[]; exclude: string[] },
): string[] {
  return agentNames.filter((name) => {
    if (selector.exclude.includes(name)) return false;
    if (selector.mode === 'whitelist') return selector.include.includes(name);
    return true;
  });
}
