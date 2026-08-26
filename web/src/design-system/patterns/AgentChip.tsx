/**
 * AgentChip — agent name preceded by a 6px role-colored dot. Per
 * DESIGN.md `components.agent_chip`. Used wherever an agent identity is
 * rendered (message header, participant list, roster, …).
 *
 * Pure prop-driven. No hooks, no data fetches.
 */

interface AgentChipProps {
  name: string;
  role: 'manager' | 'worker' | 'founder';
  /** Allow long identities to wrap when truncation would hide required data. */
  wrap?: boolean;
}

const DOT_BG: Record<AgentChipProps['role'], string> = {
  manager: 'bg-agent-manager',
  worker: 'bg-agent-worker',
  founder: 'bg-agent-founder',
};

export function AgentChip({ name, role, wrap = false }: AgentChipProps): JSX.Element {
  return (
    <span className="text-fg inline-flex items-center gap-2 text-sm">
      <span
        aria-hidden="true"
        className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${DOT_BG[role]}`}
      />
      <span
        className={wrap ? 'min-w-0 break-all' : 'truncate'}
      >
        {name}
      </span>
    </span>
  );
}

export const meta = {
  name: "AgentChip",
  layer: "pattern",
  import: "@/design-system/patterns/AgentChip",
  variants: { role: ["manager", "worker", "founder"] },
  consumes: ["components.agent_chip"],
  example: "<AgentChip name='engineering_head' role='manager' />",
} as const;
