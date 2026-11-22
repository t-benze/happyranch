import { REMOTE_OPS, isRemoteReadOnlyError, useRemoteReadOnly } from '@/lib/remotePolicy';
import { RemoteReadOnlyNote } from '@/shared/remotePolicy/RemoteReadOnlyNote';
import { OwnerSaveNotice, useConfigOwner } from '@/shared/owners';
import { useIdentityPresentation } from '@/hooks/identities';
import { IdentityName } from '@/shared/identities/IdentityName';
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
import { Link, useParams } from 'react-router-dom';
import {
  BadgeCheck,
  CircleDashed,
  ClipboardCheck,
  Info,
  Pencil,
  TriangleAlert,
  Users,
} from 'lucide-react';
import { useAgentsList } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import { useAssignSkill, useSkillStatus } from '@/hooks/skills';
import type { ValidationTone } from './skills-catalog';
import {
  editRoutePath,
  type AgentAssignmentFacts,
  type AgentEffectiveStatus,
} from './skills-detail';
import {
  CONFIG_REVIEW_NOTE,
  changeCount,
  isChanged,
  previewProvenance,
  reviewChanges,
  rosterAssignments,
  toggleAssignment,
  toggleLabel,
  type PendingAssignments,
} from './skills-assign';
import { classifySkillError, renderSkillErrorInContext, type SkillErrorView } from './strings';

const STATUS_TONE: Record<AgentEffectiveStatus, ValidationTone> = {
  effective: 'positive',
  not_yet_effective: 'attention',
  not_assigned: 'neutral',
};

const TONE_CHIP: Record<ValidationTone, string> = {
  positive: 'text-status-open bg-tier-green-tint',
  attention: 'text-attention-text bg-attention-soft',
  neutral: 'text-text-secondary border border-border-default bg-transparent',
};

const TONE_ICON: Record<ValidationTone, typeof BadgeCheck> = {
  positive: BadgeCheck,
  attention: CircleDashed,
  neutral: CircleDashed,
};

function Eyebrow({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="text-text-muted text-overline mb-2 tracking-wider uppercase">
      {children}
    </div>
  );
}

export function SkillAssignmentPanel({
  slug,
  skillId,
}: {
  slug: string;
  skillId: string;
}): JSX.Element {
  const { t } = useTranslation();
  const identityPresentation = useIdentityPresentation();
  const status = useSkillStatus(skillId);
  // The full candidate roster comes from the real agents source, NOT the status
  // response (which lists only already-assigned agents) — so an unassigned agent
  // can still be surfaced with an Assign control.
  const agents = useAgentsList();
  const assign = useAssignSkill();
  const [queue, setQueue] = useState<PendingAssignments>({});
  const [reviewOpen, setReviewOpen] = useState(false);
  const [applied, setApplied] = useState(false);
  const [applyError, setApplyError] = useState<SkillErrorView | null>(null);
  // THR-387 S6-W2a: queued, unapplied assignment changes are a Skills owner.
  const { slug: ownerOrg } = useParams<{ slug: string }>();
  const owner = useConfigOwner({
    page: 'skills.assignment',
    subject: skillId,
    org: ownerOrg ?? null,
    dirty: Object.keys(queue).length > 0,
    draft: queue,
    onDiscard: () => setQueue({}),
  });
  const remoteReadOnlyHost = useRemoteReadOnly(REMOTE_OPS.assignSkill);

  const section = (body: ReactNode) => (
    <section className="border-border-default bg-surface-raised mt-4 rounded-md border p-6 md:p-8">
      <Eyebrow>{t('skills.assign.eyebrow')}</Eyebrow>
      <h2 className="text-h2 text-text-primary mb-1">{t('skills.assign.title')}</h2>
      <p className="text-text-secondary text-body-sm">{t('skills.assign.description')}</p>
      {body}
    </section>
  );

  if (status.isLoading || agents.isLoading) {
    return section(
      <div
        className="border-border-subtle bg-surface-subtle mt-4 h-32 animate-pulse rounded-md border"
        aria-hidden="true"
      />,
    );
  }

  if (status.isError || !status.data) {
    return section(
      <p className="text-text-secondary border-border-subtle bg-surface-subtle text-body-sm mt-4 flex items-start gap-2.5 rounded-md border p-3">
        <TriangleAlert
          size={15}
          aria-hidden="true"
          className="text-text-muted mt-0.5 shrink-0"
        />
        {renderSkillErrorInContext(
          classifySkillError(status.error, 'skills.assign.unavailable'),
          'skills.assign.unavailable',
          t,
        )}
      </p>,
    );
  }

  // Validation gate: the assign route rejects a change while the current
  // version is not validated, so a not-validated skill can queue nothing. Show
  // a read-only "resolve validation first" state instead of the controls.
  if (!status.data.validated) {
    return section(
      <div
        className="text-text-secondary border-border-subtle bg-surface-subtle mt-4 rounded-md border p-4"
        data-testid="assignment-not-validated"
      >
        <div className="text-text-primary flex items-center gap-2 text-sm font-semibold">
          <TriangleAlert
            size={15}
            aria-hidden="true"
            className="text-text-muted shrink-0"
          />
          {t('skills.assign.notValidatedTitle')}
        </div>
        <p className="text-body-sm mt-1.5">{t('skills.assign.notValidatedBody')}</p>
        <Link
          to={editRoutePath(slug, skillId)}
          className="border-border-default bg-surface-subtle text-text-primary hover:bg-surface-sunken text-body-sm mt-3 inline-flex max-w-full items-start gap-1.5 rounded-md border px-3 py-1.5 font-semibold"
        >
          <Pencil size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{t('skills.assign.revalidate')}</span>
        </Link>
      </div>,
    );
  }

  // Union the real agents roster with the status response so unassigned agents
  // surface as assignable candidates (not just the already-assigned agents the
  // status endpoint returns).
  const roster = (agents.data?.agents ?? []).map((a) => a.name);
  const assignments: AgentAssignmentFacts[] = rosterAssignments(
    roster,
    status.data.assignments,
  );
  const changes = reviewChanges(assignments, queue);
  const pendingCount = changeCount(assignments, queue);
  // Receipt admission counts as applying, like the requests themselves.
  const applying = assign.isPending || owner.busy;
  // S8-2b: assignment is agent configuration; read-only on a remote Host.
  const readOnlyHost = remoteReadOnlyHost;

  function toggle(a: AgentAssignmentFacts): void {
    setApplied(false);
    setApplyError(null);
    setQueue((q) => toggleAssignment(a, q));
  }

  async function apply(): Promise<void> {
    setApplyError(null);
    const result = await owner.track(async () => {
      for (const c of changes) {
        await assign.mutateAsync({
          agentId: c.agent,
          skillId,
          body: { action: c.action },
        });
      }
    });
    if (result.ok) {
      setQueue({});
      setReviewOpen(false);
      setApplied(true);
    } else if (!result.refused && !isRemoteReadOnlyError(result.error)) {
      // A refused apply sent nothing (a prior result is still unknown; the
      // owner notice offers its reconciliation).
      setApplyError(classifySkillError(result.error, 'skills.assign.applyError'));
    }
  }

  return section(
    <>
      {readOnlyHost && <RemoteReadOnlyNote host={readOnlyHost} className="mt-4" />}
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
                <div className="text-text-primary font-mono text-sm font-semibold break-all">
                  <IdentityName canonicalId={a.agent} />
                </div>
                <p className="text-text-secondary text-body-sm mt-1">{t(p.reason)}</p>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {changed && (
                  <span className="text-attention-text text-2xs inline-flex items-center gap-1 font-semibold">
                    <span
                      aria-hidden="true"
                      className="bg-attention h-1.5 w-1.5 rounded-full"
                    />
                    {t('skills.assign.willChange')}
                  </span>
                )}
                <span
                  className={`text-mono-sm inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                    TONE_CHIP[STATUS_TONE[p.status]]
                  }`}
                >
                  <Icon size={11} aria-hidden="true" className="shrink-0" />
                  {t(p.statusLabel)}
                </span>
                <button
                  type="button"
                  onClick={() => toggle(a)}
                  disabled={applying || readOnlyHost !== null}
                  aria-label={t('skills.assign.toggleAria', { action: t(label), agent: identityPresentation.label(a.agent) })}
                  className="border-border-default bg-surface-subtle text-text-primary hover:bg-surface-sunken text-body-sm inline-flex items-center rounded-md border px-2.5 py-1 font-semibold disabled:opacity-60"
                >
                  {t(label)}
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {/* ── Applied confirmation ─────────────────────────────────── */}
      {applied && pendingCount === 0 && (
        <p
          className="text-status-open border-accent-soft bg-tier-green-tint text-body-sm mt-4 flex items-start gap-2.5 rounded-md border p-3"
          role="status"
        >
          <BadgeCheck size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
          {t('skills.assign.applied')}
        </p>
      )}

      {/* ── Config-review: review-before-commit summary ──────────── */}
      {pendingCount > 0 && (
        <div className="border-border-default bg-surface-sunken mt-4 rounded-md border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-text-primary text-body-sm inline-flex items-center gap-2 font-semibold">
              <Users size={15} aria-hidden="true" className="text-text-muted" />
              {t('skills.assign.toReview', { count: pendingCount })}
            </span>
            {!reviewOpen && (
              <button
                type="button"
                onClick={() => setReviewOpen(true)}
                className="bg-accent-default text-text-inverse hover:bg-accent-hover text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold"
              >
                <ClipboardCheck size={14} aria-hidden="true" />
                {t('skills.assign.reviewApply')}
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
                    <span className="text-text-primary text-body-sm font-semibold">
                      {t(c.label)}
                    </span>
                    <span className="text-text-secondary text-body-sm min-w-0 break-words">
                      {t(c.summary, { agent: identityPresentation.label(c.agent) })}
                    </span>
                  </li>
                ))}
              </ul>

              <p className="text-text-secondary border-border-subtle bg-surface-subtle text-body-sm mt-3 flex items-start gap-2.5 rounded-md border p-3">
                <Info
                  size={15}
                  aria-hidden="true"
                  className="text-text-muted mt-0.5 shrink-0"
                />
                {t(CONFIG_REVIEW_NOTE)}
              </p>

              <OwnerSaveNotice owner={owner} className="mt-2" />
              {applyError && (
                <p className="text-attention-text text-body-sm mt-2" role="alert">
                  {renderSkillErrorInContext(applyError, 'skills.assign.applyError', t)}
                </p>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={apply}
                  disabled={applying || readOnlyHost !== null}
                  className="bg-accent-default text-text-inverse hover:bg-accent-hover text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold disabled:opacity-60"
                >
                  {applying
                    ? t('skills.assign.applying')
                    : t('skills.assign.apply', { count: pendingCount })}
                </button>
                <button
                  type="button"
                  onClick={() => setReviewOpen(false)}
                  disabled={applying}
                  className="text-text-secondary hover:text-text-primary text-body-sm inline-flex items-center rounded-md px-2 py-1.5 font-semibold disabled:opacity-60"
                >
                  {t('skills.assign.keepEditing')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>,
  );
}
