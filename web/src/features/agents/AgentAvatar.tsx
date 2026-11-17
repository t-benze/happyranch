/**
 * AgentAvatar — role-colored initial chip shared by the roster row and the
 * detail hero (Direction-A `a-agents`).
 *
 * Initials are derived CLIENT-SIDE from the agent name (no backend field):
 * a two-token name (split on `_`, `-`, or space) → first letter of each of
 * the first two parts (engineering_manager → 'EM'); a single-token name →
 * its first two letters (consultant → 'CO'). The fill is the real `role`
 * field (manager/worker) via design tokens — no raw hex, no invented status.
 */
import type { AgentSummary } from '@/lib/api/types';

export function agentInitials(name: string): string {
  const parts = name.split(/[_\s-]+/).filter(Boolean);
  const letters =
    parts.length >= 2
      ? parts[0][0] + parts[1][0]
      : (parts[0] ?? name).slice(0, 2);
  return letters.toUpperCase();
}

interface AgentAvatarProps {
  name: string;
  displayName?: string;
  role: AgentSummary['role'];
  /** `sm` = 36px roster chip, `lg` = 48px detail-hero chip. */
  size?: 'sm' | 'lg';
  className?: string;
}

export function AgentAvatar({
  name,
  displayName,
  role,
  size = 'sm',
  className = '',
}: AgentAvatarProps): JSX.Element {
  // Spacing is Tailwind's linear 4px scale (THR-376 PR4): `h-9`/`h-12` are
  // the intended 36px roster / 48px hero chips (Direction-A `a-agents`).
  const sizing =
    size === 'lg'
      ? 'h-12 w-12 rounded-md text-base'
      : 'h-9 w-9 rounded-lg text-xs';
  return (
    <span
      aria-hidden="true"
      className={`text-text-inverse flex shrink-0 items-center justify-center font-semibold ${sizing} ${
        role === 'manager' ? 'bg-agent-manager' : 'bg-agent-worker'
      } ${className}`}
    >
      {agentInitials(displayName ?? name)}
    </span>
  );
}
