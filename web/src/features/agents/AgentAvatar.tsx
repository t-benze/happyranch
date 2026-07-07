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
  role: AgentSummary['role'];
  /** `sm` = 36px roster chip, `lg` = 48px detail-hero chip. */
  size?: 'sm' | 'lg';
  className?: string;
}

export function AgentAvatar({
  name,
  role,
  size = 'sm',
  className = '',
}: AgentAvatarProps): JSX.Element {
  // NOTE: this project's `--spacing-8` is a custom 4rem (64px) t-shirt token,
  // so `h-8` is NOT 32px. Use the linear-scale `h-9`/`h-12` for the intended
  // 36px roster / 48px hero chips (Direction-A `a-agents`).
  const sizing =
    size === 'lg'
      ? 'h-12 w-12 rounded-xl text-base'
      : 'h-9 w-9 rounded-lg text-xs';
  return (
    <span
      aria-hidden="true"
      className={`text-text-inverse flex shrink-0 items-center justify-center font-semibold ${sizing} ${
        role === 'manager' ? 'bg-agent-manager' : 'bg-agent-worker'
      } ${className}`}
    >
      {agentInitials(name)}
    </span>
  );
}
