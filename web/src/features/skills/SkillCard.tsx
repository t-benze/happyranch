/**
 * SkillCard — one catalog row (THR-092 Slice 1). Shared primitive reused by
 * later Skills slices (detail, effective view). Pure prop-driven.
 *
 * Copy discipline (hard): no user-facing "active"; system-contract / bundled
 * read-only skills carry NO toggle, edit, or unassign control — only a lock
 * affordance. Rollups use guidance-visibility language: Assigned / Effective /
 * Takes effect next session. Never permission / approve / admit wording.
 */
import { Lock, Package, Sparkles } from 'lucide-react';
import type { CatalogSkillItem } from '@/hooks/skills';
import { SkillStatusBadge } from './SkillStatusBadge';
import { isBundled, isReadOnly, sourceLabel } from './skills-catalog';

function SourceBadge({ item }: { item: CatalogSkillItem }): JSX.Element {
  const label = sourceLabel(item);
  const cls =
    label === 'bundled'
      ? 'bg-info-soft text-info'
      : 'bg-accent-soft text-accent-text';
  return (
    <span
      className={`text-2xs inline-flex items-center rounded-full px-2 py-0.5 font-bold tracking-wide uppercase ${cls}`}
    >
      {label}
    </span>
  );
}

function ContractBadge(): JSX.Element {
  return (
    <span className="text-2xs text-fg-muted bg-bg-subtle border-border-default inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-bold tracking-wide uppercase">
      <Lock size={10} aria-hidden="true" />
      system contract
    </span>
  );
}

export function SkillCard({ item }: { item: CatalogSkillItem }): JSX.Element {
  const readOnly = isReadOnly(item);
  const bundled = isBundled(item);
  const SourceIcon = bundled ? Package : Sparkles;
  const iconTint = readOnly
    ? 'bg-bg-subtle text-fg-muted'
    : bundled
      ? 'bg-info-soft text-info'
      : 'bg-accent-soft text-accent-text';

  return (
    <article
      className="border-border-default bg-surface-raised flex gap-3.5 rounded-md border p-4"
      data-source={bundled ? 'bundled' : 'custom'}
      data-skill-id={item.skill_id}
    >
      <div
        aria-hidden="true"
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${iconTint}`}
      >
        <SourceIcon size={18} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <SourceBadge item={item} />
          {item.system_contract && <ContractBadge />}
          {/* Skill-level validation badge renders on EVERY catalog row,
              including read-only/system_contract — read-only only suppresses
              INTERACTIVE controls (toggle/unassign), not this status label. */}
          <SkillStatusBadge state={item.validation_state} />
          <span className="text-mono-sm text-fg-subtle">v{item.version}</span>
        </div>

        <h3 className="text-fg font-mono text-sm leading-snug font-semibold">
          {item.name}
        </h3>
        <p className="text-fg-muted text-body-sm mt-1.5 max-w-2xl leading-relaxed">
          {item.summary}
        </p>

        <div className="text-fg-subtle mt-3 flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-xs">
          <span className="inline-flex items-center gap-1.5">
            Assigned{' '}
            <span className="text-fg-muted font-semibold tabular-nums">
              {item.assigned_agent_count}
            </span>
          </span>
          <span className="inline-flex items-center gap-1.5">
            Effective{' '}
            <span className="text-accent-text font-semibold tabular-nums">
              {item.effective_agent_count}
            </span>
          </span>
          {item.has_assigned_not_yet_effective && (
            <span className="text-attention-text inline-flex items-center gap-1.5 font-semibold">
              <span
                aria-hidden="true"
                className="bg-attention h-1.5 w-1.5 rounded-full"
              />
              Takes effect next session
            </span>
          )}
          {readOnly && (
            <span className="text-fg-subtle">
              Read-only — cannot be edited or unassigned
            </span>
          )}
        </div>
      </div>

      {readOnly && (
        <div
          className="text-fg-subtle mt-0.5 shrink-0 self-start"
          title="Read-only system contract"
        >
          <Lock size={15} aria-hidden="true" />
          <span className="sr-only">Read-only system contract</span>
        </div>
      )}
    </article>
  );
}
