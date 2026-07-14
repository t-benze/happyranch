/**
 * SkillAssignmentPanel — per-agent assignment + config-review surface for a
 * CUSTOM (user-authored) skill (THR-092 Slice 5 of 6). Rendered from
 * SkillDetailPage in place of the read-only provenance list, and ONLY for
 * editable custom skills — bundled / system-contract skills stay read-only
 * (applied by context, no per-agent controls), so this component never mounts
 * for them (Slice-2 source-gating is preserved upstream).
 *
 * Flow: the operator toggles agents (Assign / Unassign) to build a queue of
 * not-yet-committed changes, previewed optimistically in the table; then
 * "Review & apply" opens the config-review summary — a review-before-commit
 * list of exactly what will change, with the guidance-visibility note — and
 * the commit applies the queued changes one per agent via the assign route.
 *
 * Copy discipline (hard): guidance-visibility language only. The api request
 * verb is 'allow' / 'remove' but that is REQUEST-BODY ONLY — the visible labels
 * are Assign / Unassign / Review & apply. NO permission / approve / admit /
 * grant / materialize / "pending" / "active" wording. Assigning a skill changes
 * what an agent is SHOWN as guidance — never the tools or commands it can use.
 * All derivation lives in the unit-tested `skills-assign` + `skills-detail`
 * modules.
 */
import { useState, type ReactNode } from 'react';
import {
  BadgeCheck,
  CircleDashed,
  ClipboardCheck,
  Info,
  TriangleAlert,
  Users,
} from 'lucide-react';
import { useAssignSkill, useSkillStatus } from '@/hooks/skills';
import type { ValidationTone } from './skills-catalog';
import { type AgentAssignmentFacts, type AgentEffectiveStatus } from './skills-detail';
import {
  CONFIG_REVIEW_NOTE,
  changeCount,
  isChanged,
  previewProvenance,
  reviewChanges,
  toggleAssignment,
  toggleLabel,
  type PendingAssignments,
} from './skills-assign';

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

function Eyebrow({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="text-fg-subtle text-overline mb-2 tracking-wider uppercase">
      {children}
    </div>
  );
}

export function SkillAssignmentPanel({
  skillId,
}: {
  skillId: string;
}): JSX.Element {
  const status = useSkillStatus(skillId);
  const assign = useAssignSkill();
  const [queue, setQueue] = useState<PendingAssignments>({});
  const [reviewOpen, setReviewOpen] = useState(false);
  const [applied, setApplied] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  const section = (body: ReactNode) => (
    <section className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6">
      <Eyebrow>Assign to agents</Eyebrow>
      <h2 className="text-h2 text-fg mb-1">Which agents see this skill</h2>
      <p className="text-fg-muted text-body-sm">
        Assign this skill to an agent to show it as guidance at that agent’s next
        session. This changes what the agent is shown — it never changes the
        tools or commands available to it.
      </p>
      {body}
    </section>
  );

  if (status.isLoading) {
    return section(
      <div
        className="border-border-subtle bg-surface-subtle mt-4 h-32 animate-pulse rounded-md border"
        aria-hidden="true"
      />,
    );
  }

  if (status.isError || !status.data) {
    return section(
      <p className="text-fg-muted border-border-subtle bg-surface-subtle text-body-sm mt-4 flex items-start gap-2.5 rounded-md border p-3">
        <TriangleAlert
          size={15}
          aria-hidden="true"
          className="text-fg-subtle mt-0.5 shrink-0"
        />
        Per-agent assignment is unavailable right now. Try again in a moment.
      </p>,
    );
  }

  const assignments: AgentAssignmentFacts[] = status.data.assignments;
  const changes = reviewChanges(assignments, queue);
  const pendingCount = changeCount(assignments, queue);
  const applying = assign.isPending;

  function toggle(a: AgentAssignmentFacts): void {
    setApplied(false);
    setApplyError(null);
    setQueue((q) => toggleAssignment(a, q));
  }

  async function apply(): Promise<void> {
    setApplyError(null);
    try {
      for (const c of changes) {
        await assign.mutateAsync({
          agentId: c.agent,
          skillId,
          body: { action: c.action },
        });
      }
      setQueue({});
      setReviewOpen(false);
      setApplied(true);
    } catch {
      setApplyError(
        'Could not apply every change. Review the list and try again.',
      );
    }
  }

  return section(
    <>
      <ul className="mt-4 flex flex-col gap-2.5">
        {assignments.map((a) => {
          const p = previewProvenance(a, queue);
          const changed = isChanged(a, queue);
          const Icon = TONE_ICON[STATUS_TONE[p.status]];
          const label = toggleLabel(a, queue);
          return (
            <li
              key={a.agent}
              data-agent={a.agent}
              data-status={p.status}
              data-changed={changed ? 'true' : 'false'}
              className="border-border-subtle bg-surface-subtle flex flex-wrap items-start justify-between gap-x-4 gap-y-2 rounded-md border p-3"
            >
              <div className="min-w-0">
                <div className="text-fg font-mono text-sm font-semibold break-all">
                  {a.agent}
                </div>
                <p className="text-fg-muted text-body-sm mt-1">{p.reason}</p>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {changed && (
                  <span className="text-attention-text text-2xs inline-flex items-center gap-1 font-semibold">
                    <span
                      aria-hidden="true"
                      className="bg-attention h-1.5 w-1.5 rounded-full"
                    />
                    will change
                  </span>
                )}
                <span
                  className={`text-mono-sm inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                    TONE_CHIP[STATUS_TONE[p.status]]
                  }`}
                >
                  <Icon size={11} aria-hidden="true" className="shrink-0" />
                  {p.statusLabel}
                </span>
                <button
                  type="button"
                  onClick={() => toggle(a)}
                  disabled={applying}
                  aria-label={`${label} ${a.agent}`}
                  className="border-border-default bg-surface-subtle text-fg hover:bg-bg-subtle text-body-sm inline-flex items-center rounded-md border px-2.5 py-1 font-semibold disabled:opacity-60"
                >
                  {label}
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {/* ── Applied confirmation ─────────────────────────────────── */}
      {applied && pendingCount === 0 && (
        <p
          className="text-status-open border-tier-green-line bg-tier-green-tint text-body-sm mt-4 flex items-start gap-2.5 rounded-md border p-3"
          role="status"
        >
          <BadgeCheck size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
          Changes applied — they take effect at each agent’s next session.
        </p>
      )}

      {/* ── Config-review: review-before-commit summary ──────────── */}
      {pendingCount > 0 && (
        <div className="border-border-default bg-bg-subtle mt-4 rounded-md border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-fg text-body-sm inline-flex items-center gap-2 font-semibold">
              <Users size={15} aria-hidden="true" className="text-fg-subtle" />
              {pendingCount} {pendingCount === 1 ? 'change' : 'changes'} to review
            </span>
            {!reviewOpen && (
              <button
                type="button"
                onClick={() => setReviewOpen(true)}
                className="bg-accent text-on-accent hover:bg-accent-hover text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold"
              >
                <ClipboardCheck size={14} aria-hidden="true" />
                Review &amp; apply
              </button>
            )}
          </div>

          {reviewOpen && (
            <div className="mt-3" data-testid="config-review">
              <ul className="border-border-subtle divide-border-subtle bg-surface-raised divide-y rounded-md border">
                {changes.map((c) => (
                  <li
                    key={c.agent}
                    data-review-agent={c.agent}
                    className="flex flex-wrap items-baseline gap-x-2 gap-y-1 p-3"
                  >
                    <span className="text-fg text-body-sm font-semibold">
                      {c.label}
                    </span>
                    <span className="text-fg-muted text-body-sm min-w-0 break-words">
                      {c.summary}
                    </span>
                  </li>
                ))}
              </ul>

              <p className="text-fg-muted border-border-subtle bg-surface-subtle text-body-sm mt-3 flex items-start gap-2.5 rounded-md border p-3">
                <Info
                  size={15}
                  aria-hidden="true"
                  className="text-fg-subtle mt-0.5 shrink-0"
                />
                {CONFIG_REVIEW_NOTE}
              </p>

              {applyError && (
                <p className="text-attention-text text-body-sm mt-2" role="alert">
                  {applyError}
                </p>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={apply}
                  disabled={applying}
                  className="bg-accent text-on-accent hover:bg-accent-hover text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold disabled:opacity-60"
                >
                  {applying
                    ? 'Applying…'
                    : `Apply ${pendingCount} ${pendingCount === 1 ? 'change' : 'changes'}`}
                </button>
                <button
                  type="button"
                  onClick={() => setReviewOpen(false)}
                  disabled={applying}
                  className="text-fg-muted hover:text-fg text-body-sm inline-flex items-center rounded-md px-2 py-1.5 font-semibold disabled:opacity-60"
                >
                  Keep editing
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>,
  );
}
