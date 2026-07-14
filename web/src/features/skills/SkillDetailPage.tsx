/**
 * SkillDetailPage — single-skill detail + per-agent effective/provenance
 * (THR-092 Slice 2 of 6). Reached from a Catalog card.
 *
 * Two screens in one route (handoff §5 folds the per-agent status table into
 * skill detail):
 *   1. SKILL DETAIL — source (bundled path / SKILL.md content), the skill-level
 *      validation badge + a "needs attention" label when validation failed, and
 *      a SOURCE-GATED affordance: bundled / system-contract skills are read-only
 *      (lock, NO edit / toggle / re-validate control); custom (user-authored)
 *      skills show an EDIT entry point that targets the Slice-4 edit screen.
 *   2. PER-AGENT EFFECTIVE / PROVENANCE — each agent's assigned-vs-effective
 *      state, a "takes effect next session" indicator on assigned-not-yet-
 *      effective, and a plain-language reason WHY the skill is / isn't effective
 *      for that agent.
 *
 * Copy discipline (hard): NO user-facing "active"; assigned-not-yet-effective
 * reads "Takes effect next session". This is guidance VISIBILITY, never
 * permission — no approve / admit / materialize-now language anywhere. All
 * derivation lives in the unit-tested `skills-detail` module.
 */
import { type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BadgeCheck,
  CircleDashed,
  Info,
  Lock,
  Package,
  Pencil,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useSkillDetail } from '@/hooks/skills';
import { SkillAssignmentPanel } from './SkillAssignmentPanel';
import { SkillStatusBadge } from './SkillStatusBadge';
import type { ValidationTone } from './skills-catalog';
import {
  agentProvenanceList,
  assignmentRollup,
  editRoutePath,
  isEditableSkill,
  needsAttention,
  readOnlyReason,
  skillSource,
  validationIssues,
  type AgentEffectiveStatus,
} from './skills-detail';

const STATUS_TONE: Record<AgentEffectiveStatus, ValidationTone> = {
  effective: 'positive',
  not_yet_effective: 'attention',
  not_assigned: 'neutral',
};

const TONE_CHIP: Record<ValidationTone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  attention: 'text-attention-text bg-attention-soft',
  neutral: 'text-fg-muted border border-border-default bg-transparent',
};

const TONE_ICON: Record<ValidationTone, typeof BadgeCheck> = {
  positive: BadgeCheck,
  attention: CircleDashed,
  neutral: CircleDashed,
};

/** Uppercase section caption — matches the catalog eyebrow styling. */
function Eyebrow({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="text-fg-subtle text-overline mb-2 tracking-wider uppercase">
      {children}
    </div>
  );
}

export function SkillDetailPage(): JSX.Element {
  const { slug, skillId } = useParams<{ slug: string; skillId: string }>();
  const query = useSkillDetail(skillId);

  const backLink = (
    <Link
      to={`/orgs/${slug ?? ''}/skills`}
      className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"
    >
      <ArrowLeft size={15} aria-hidden="true" />
      Back to skills
    </Link>
  );

  if (query.isLoading) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-5 md:px-7 md:py-6">
        {backLink}
        <div
          className="border-border-subtle bg-surface-subtle h-40 animate-pulse rounded-md border"
          aria-hidden="true"
        />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-5 md:px-7 md:py-6">
        {backLink}
        <EmptyState
          icon={<TriangleAlert size={28} />}
          title="Could not load this skill"
          body="This skill is unavailable right now, or the link is out of date."
        />
      </div>
    );
  }

  const skill = query.data;
  const source = skillSource(skill);
  const editable = isEditableSkill(skill);
  const lockReason = readOnlyReason(skill);
  const attention = needsAttention(skill);
  const issues = validationIssues(skill);
  const assignments = skill.assignments ?? [];
  const provenance = agentProvenanceList(assignments);
  const rollup = assignmentRollup(assignments);
  const SourceIcon = source === 'bundled' ? Package : Sparkles;

  return (
    // `break-words` (inherited overflow-wrap) keeps long mono identifiers from
    // forcing horizontal overflow of the content region on narrow viewports.
    <div className="mx-auto w-full max-w-4xl px-4 py-5 break-words md:px-7 md:py-6">
      {backLink}

      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span
                className={`text-2xs inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-bold tracking-wide uppercase ${
                  source === 'bundled'
                    ? 'bg-info-soft text-info'
                    : 'bg-accent-soft text-accent-text'
                }`}
              >
                <SourceIcon size={10} aria-hidden="true" />
                {source}
              </span>
              {skill.system_contract && (
                <span className="text-2xs text-fg-muted bg-bg-subtle border-border-default inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-bold tracking-wide uppercase">
                  <Lock size={10} aria-hidden="true" />
                  system contract
                </span>
              )}
              <SkillStatusBadge
                state={
                  skill.validation_state as
                    | 'in_catalog'
                    | 'validated'
                    | 'failed_validation'
                }
              />
              <span className="text-mono-sm text-fg-subtle">
                v{skill.version}
              </span>
            </div>
            <h1 className="text-fg font-mono text-lg leading-snug font-semibold break-all">
              {skill.name}
            </h1>
            <p className="text-fg-muted text-body-sm mt-2 max-w-2xl leading-relaxed">
              {skill.summary}
            </p>
          </div>

          {/* Source-gated affordance: Edit for custom, lock for read-only. */}
          <div className="shrink-0">
            {editable ? (
              <Link
                to={editRoutePath(slug ?? '', skill.skill_id)}
                className="border-border-default bg-surface-subtle text-fg hover:bg-bg-subtle text-body-sm inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
              >
                <Pencil size={14} aria-hidden="true" />
                Edit skill
              </Link>
            ) : (
              <span
                className="text-fg-subtle inline-flex items-center gap-1.5 text-xs"
                title={lockReason ?? undefined}
              >
                <Lock size={14} aria-hidden="true" />
                Read-only
              </span>
            )}
          </div>
        </div>

        {/* Read-only rationale (bundled / system contract). */}
        {lockReason && (
          <p className="text-fg-subtle border-border-subtle mt-4 border-t pt-3 text-xs">
            {lockReason}
          </p>
        )}
      </header>

      {/* ── Needs-attention banner (failed technical validation) ── */}
      {attention && (
        <section
          className="border-attention/40 bg-attention-soft mt-4 rounded-md border p-4"
          aria-label="Needs attention"
        >
          <div className="text-attention-text flex items-center gap-2 text-sm font-semibold">
            <TriangleAlert size={15} aria-hidden="true" />
            Needs attention
          </div>
          <p className="text-fg-muted text-body-sm mt-1.5">
            This skill did not pass validation, so it is not shown to any agent
            yet. Editing it keeps the draft — nothing is lost. Fix the items
            below and re-validate.
          </p>
          {issues.length > 0 && (
            <ul className="text-fg-muted text-body-sm mt-2 list-disc space-y-1 pl-5">
              {issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ── Source / SKILL.md content ─────────────────────────── */}
      <section className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6">
        <Eyebrow>Source</Eyebrow>
        <p className="text-mono-sm text-fg-muted break-words">{skill.source}</p>

        {skill.when_to_use && (
          <div className="mt-5">
            <Eyebrow>When to use</Eyebrow>
            <p className="text-fg-muted text-body-sm leading-relaxed">
              {skill.when_to_use}
            </p>
          </div>
        )}

        {skill.description && (
          <div className="mt-5">
            <Eyebrow>Guidance (SKILL.md)</Eyebrow>
            <p className="text-fg-muted text-body-sm leading-relaxed">
              {skill.description}
            </p>
          </div>
        )}
      </section>

      {/* ── Per-agent effective / provenance ──────────────────────
          Custom (user-authored) skills get the interactive assignment +
          config-review surface (Slice-5); bundled / system-contract skills
          stay READ-ONLY (applied by context, no per-agent controls), keeping
          the Slice-2 source-gating intact. */}
      {editable ? (
        <SkillAssignmentPanel slug={slug ?? ''} skillId={skill.skill_id} />
      ) : (
      <section className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6">
        <Eyebrow>Per-agent visibility</Eyebrow>
        <h2 className="text-h2 text-fg mb-1">Where this skill is effective</h2>
        <p className="text-fg-muted text-body-sm">
          Which agents can see this skill as guidance, and why. Assigning a
          skill changes guidance visibility only — it never changes the tools or
          commands an agent can use.
        </p>

        {assignments.length > 0 ? (
          <>
            {/* Rollup */}
            <div className="text-fg-subtle mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
              <span className="inline-flex items-center gap-1.5">
                Assigned{' '}
                <span className="text-fg-muted font-semibold tabular-nums">
                  {rollup.assigned}
                </span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                Effective{' '}
                <span className="text-accent-text font-semibold tabular-nums">
                  {rollup.effective}
                </span>
              </span>
              {rollup.notYetEffective > 0 && (
                <span className="text-attention-text inline-flex items-center gap-1.5 font-semibold">
                  <span
                    aria-hidden="true"
                    className="bg-attention h-1.5 w-1.5 rounded-full"
                  />
                  {rollup.notYetEffective} takes effect next session
                </span>
              )}
            </div>

            <ul className="mt-4 flex flex-col gap-2.5">
              {provenance.map((p) => {
                const Icon = TONE_ICON[STATUS_TONE[p.status]];
                return (
                  <li
                    key={p.agent}
                    data-agent={p.agent}
                    data-status={p.status}
                    className="border-border-subtle bg-surface-subtle flex flex-wrap items-start justify-between gap-x-4 gap-y-2 rounded-md border p-3"
                  >
                    <div className="min-w-0">
                      <div className="text-fg font-mono text-sm font-semibold break-all">
                        {p.agent}
                      </div>
                      <p className="text-fg-muted text-body-sm mt-1">
                        {p.reason}
                      </p>
                    </div>
                    <span
                      className={`text-mono-sm inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                        TONE_CHIP[STATUS_TONE[p.status]]
                      }`}
                    >
                      <Icon size={11} aria-hidden="true" className="shrink-0" />
                      {p.statusLabel}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : skill.system_contract ? (
          <p className="text-fg-muted border-border-subtle bg-surface-subtle text-body-sm mt-4 flex items-start gap-2.5 rounded-md border p-3">
            <Info size={15} aria-hidden="true" className="text-fg-subtle mt-0.5 shrink-0" />
            Applied to agents by context, not per-agent assignment. This
            contract is shown to every agent its predicate matches.
          </p>
        ) : (
          <p className="text-fg-subtle text-body-sm mt-4">
            No agents are assigned this skill yet.
          </p>
        )}
      </section>
      )}

      {/* ── Guidance-only footer ──────────────────────────────── */}
      <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mt-4 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
        <Info size={15} aria-hidden="true" className="text-fg-subtle shrink-0" />
        <span>
          <b className="text-fg font-semibold">Guidance visibility only.</b>{' '}
          Skills shape what an agent is told — they never change the tools or
          commands an agent can use.
        </span>
      </div>
    </div>
  );
}
