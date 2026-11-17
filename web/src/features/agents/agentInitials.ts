/**
 * Agent avatar initials, derived CLIENT-SIDE from the agent name (no backend
 * field): a two-token name (split on `_`, `-`, or space) → first letter of
 * each of the first two parts (engineering_manager → 'EM'); a single-token
 * name → its first two letters (consultant → 'CO'). Moved unchanged from the
 * retired AgentAvatar (THR-376 PR9); the chip itself is the design-system
 * `Avatar`.
 */
export function agentInitials(name: string): string {
  const parts = name.split(/[_\s-]+/).filter(Boolean);
  const letters =
    parts.length >= 2
      ? parts[0][0] + parts[1][0]
      : (parts[0] ?? name).slice(0, 2);
  return letters.toUpperCase();
}
