/**
 * ProposalDetailPage — THR-055 Founder Proposal Detail + THR-136 review actions.
 *
 * Reached via the static route /orgs/:slug/skills/proposals/:versionId.
 * Displays immutable proposal/version facts and exposes the minimal Founder
 * review-action set bounded by THR-136: claim, validate, submit-for-review,
 * approve, and reject. Publish, assign, and rollback remain out of scope here.
 *
 * Sections (top-to-bottom):
 *   1. Shell breadcrumb + identity (mono skill_id, version, full copyable hash)
 *   2. Lifecycle status + Founder review-action affordances (THR-136)
 *   3. Readiness strip (not-in-catalog, not-assigned, etc.)
 *   4. SKILL.md primary pane (read-only, wrapped, copy control, null warning)
 *   5. Evidence rail (purpose, policy class, advisory target, validation)
 *   6. Provenance + audit timeline (immutable proposer, claimant, events)
 *   7. Assignment & materialization projection (separate from package status)
 *   8. Guidance-only footer
 *
 * State handling:
 *   - 403 → Founder-access state with NO bytes/hash/proposer/audit/assignment
 *   - 404 → distinct not-found
 *   - Error → generic error with Retry (refetches authoritative state)
 *   - skill_md: null → visible warning, no fabrication
 *   - Loading → structural skeleton
 *   - 409 stale_concurrency → refetch authoritative detail, clear confirmation,
 *     show explicit conflict explanation
 *
 * Copy discipline: renders ONLY response facts — never synthesizes SKILL.md,
 * hash, validation pass, claimant, audit row, assignment, materialization,
 * or permitted action. Rejected is terminal/view-only. Published/rejected
 * are visibly distinct. Assignment/materialization are separate from
 * package decision status.
 */
import { type ReactNode, useCallback, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  ClipboardCheck,
  Copy,
  Info,
  Lock,
  Package,
  RefreshCw,
  Shield,
  Sparkles,
  XCircle,
} from 'lucide-react';
import {
  useClaimProposal,
  useProposalDetail,
  useReviewProposal,
  useSubmitReviewProposal,
  useValidateProposal,
  ApiError,
} from '@/hooks/skills';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import {
  assignmentProjection,
  availableReviewAction,
  hasAssignmentProjection,
  hashDisplay,
  isPublished,
  isRejected,
  isTerminal,
  materializationProjection,
  readinessFacts,
  statusLabel,
  statusTone,
  timelineEvents,
  TONE_CHIP,
  validatorFacts,
  type MaterializationAttempt,
  type ProposalReviewAction,
  type ReadinessFact,
  type TimelineEvent,
} from './proposal-detail';

// ── Sub-components ───────────────────────────────────────────────────────

/** Uppercase section eyebrow — matches catalog styling. */
function Eyebrow({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="text-fg-subtle text-overline mb-2 tracking-wider uppercase">
      {children}
    </div>
  );
}

/** Semantic status chip with tone-aware icon + label. */
function StatusChip({
  status,
  className,
}: {
  status: string;
  className?: string;
}): JSX.Element {
  const tone = statusTone(status);
  const Icon =
    tone === 'done'
      ? CheckCircle2
      : tone === 'failed'
        ? XCircle
        : tone === 'active'
          ? Sparkles
          : CircleDashed;
  return (
    <span
      className={`text-mono-sm inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${TONE_CHIP[tone]} ${className ?? ''}`}
    >
      <Icon size={11} aria-hidden="true" className="shrink-0" />
      {statusLabel(status)}
    </span>
  );
}

/** Copy-to-clipboard button with confirmation and failure state. Accessible via keyboard, aria-live region for status feedback. */
function CopyButton({
  label,
  value,
  ariaLabel,
  variant = 'default',
}: {
  label: string;
  value: string;
  ariaLabel: string;
  variant?: 'default' | 'mono';
}): JSX.Element {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  const copy = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopyState('copied');
        timerRef.current = setTimeout(() => setCopyState('idle'), 2000);
      })
      .catch(() => {
        setCopyState('failed');
        timerRef.current = setTimeout(() => setCopyState('idle'), 3000);
      });
  }, [value]);

  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        onClick={copy}
        aria-label={ariaLabel}
        className={`focus-visible:ring-accent inline-flex items-center gap-1.5 rounded text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 ${
          variant === 'mono'
            ? 'text-fg-subtle hover:text-fg font-mono'
            : 'text-fg-muted hover:text-fg'
        }`}
      >
        {copyState === 'copied' ? (
          <>
            <ClipboardCheck size={12} aria-hidden="true" className="text-status-open" />
            <span className="text-status-open">Copied</span>
          </>
        ) : copyState === 'failed' ? (
          <>
            <XCircle size={12} aria-hidden="true" className="text-attention-text" />
            <span className="text-attention-text">Failed</span>
          </>
        ) : (
          <>
            <Copy size={12} aria-hidden="true" />
            {label}
          </>
        )}
      </button>
      <span aria-live="polite" role="status" className="sr-only">
        {copyState === 'copied' ? 'Copied to clipboard' : copyState === 'failed' ? 'Clipboard copy failed' : ''}
      </span>
    </span>
  );
}

/** Horizontal label-value pair for evidence rail. */
function EvidenceRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className="border-border-subtle border-b py-2.5 last:border-b-0">
      <dt className="text-fg-subtle text-2xs mb-0.5 font-bold tracking-wide uppercase">
        {label}
      </dt>
      <dd className="text-fg text-body-sm">{children}</dd>
    </div>
  );
}

/** Readiness fact row in the readiness strip. */
function ReadinessRow({ fact }: { fact: ReadinessFact }): JSX.Element {
  const Icon =
    fact.status === 'ok'
      ? CheckCircle2
      : fact.status === 'warning'
        ? AlertTriangle
        : CircleDashed;
  const tint =
    fact.status === 'ok'
      ? 'text-status-open'
      : fact.status === 'warning'
        ? 'text-attention-text'
        : 'text-fg-muted';
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${tint}`}>
      <Icon size={11} aria-hidden="true" className="shrink-0" />
      {fact.label}
    </span>
  );
}

// ── Materialization list ─────────────────────────────────────────────────

function MaterializationList({
  items,
}: {
  items: MaterializationAttempt[];
}): JSX.Element {
  return (
    <div className="mt-3">
      <h4 className="text-fg-subtle text-2xs mb-1.5 font-bold tracking-wide uppercase">
        Materializations
      </h4>
      <ul className="flex flex-col gap-1.5">
        {items.map((m, i) => (
          <li key={i} className="text-fg-muted text-body-sm">
            {m.agentName ? (
              <code className="text-mono-sm">{m.agentName}</code>
            ) : null}
            {m.agentName && m.createdAt ? ' · ' : null}
            {m.createdAt ? (
              <time dateTime={m.createdAt} className="text-xs">
                {m.createdAt}
              </time>
            ) : null}
            {m.success === true ? (
              <span className="text-status-open ml-1.5 text-xs font-semibold">
                succeeded
              </span>
            ) : m.success === false ? (
              <span className="text-attention-text ml-1.5 text-xs font-semibold">
                failed
              </span>
            ) : null}
            {m.errorMessage ? (
              <span className="text-attention-text ml-1.5 text-xs break-all">
                {m.errorMessage}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Timeline ─────────────────────────────────────────────────────────────

function TimelineSection({ events }: { events: TimelineEvent[] }): JSX.Element {
  if (events.length === 0) {
    return (
      <section
        className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6"
        aria-label="Timeline"
      >
        <Eyebrow>Timeline</Eyebrow>
        <p className="text-fg-muted text-body-sm">No events recorded yet.</p>
      </section>
    );
  }

  return (
    <section
      className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6"
      aria-label="Timeline"
    >
      <div className="flex items-center justify-between">
        <Eyebrow>Timeline</Eyebrow>
        <span className="text-fg-subtle text-2xs">append-only</span>
      </div>
      <ol className="mt-1 flex flex-col gap-2">
        {events.map((ev, i) => {
          const isFirst = i === 0;
          return (
            <li
              key={`${ev.eventType}-${ev.time}-${i}`}
              className={`flex items-start gap-3 ${isFirst ? '' : 'border-border-subtle border-t pt-2'}`}
            >
              <StatusChip status={ev.newStatus ?? ev.eventType} className="shrink-0" />
              <div className="min-w-0">
                <div className="text-fg text-body-sm font-semibold">{ev.label}</div>
                <div className="text-fg-muted text-body-sm mt-0.5">
                  {ev.actor}
                  {ev.actorRole ? ` (${ev.actorRole})` : ''}
                  {ev.time ? ` · ${ev.time}` : ''}
                </div>
                {/* Content hash per event, where supplied */}
                {ev.contentHash ? (
                  <code className="text-mono-sm text-fg-subtle mt-0.5 block break-all select-all">
                    {ev.contentHash}
                  </code>
                ) : null}
                {/* Safely rendered metadata facts */}
                {ev.metadataFacts.length > 0 ? (
                  <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
                    {ev.metadataFacts.map((mf) => (
                      <span
                        key={mf.key}
                        className="text-fg-subtle text-2xs inline-flex items-baseline gap-1"
                      >
                        <span className="font-bold uppercase">{mf.key}</span>
                        <span className="font-mono">{mf.value}</span>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// ── Skeleton ─────────────────────────────────────────────────────────────

function LoadingSkeleton(): JSX.Element {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
        <div className="bg-surface-subtle mb-4 h-5 w-48 animate-pulse rounded" aria-hidden="true" />
        <div className="border-border-subtle bg-surface-subtle mb-4 h-20 animate-pulse rounded-md border" aria-hidden="true" />
        <div className="flex flex-col gap-4 lg:flex-row">
          <div className="flex-1">
            <div className="border-border-subtle bg-surface-subtle h-64 animate-pulse rounded-md border" aria-hidden="true" />
          </div>
          <div className="lg:w-64">
            <div className="border-border-subtle bg-surface-subtle h-40 animate-pulse rounded-md border" aria-hidden="true" />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Founder review actions (THR-136) ─────────────────────────────────────

const ACTION_CONFIG: Record<
  ProposalReviewAction,
  { label: string; confirm: string; variant: 'default' | 'destructive' | 'secondary' }
> = {
  claim: { label: 'Claim for review', confirm: 'Claim', variant: 'default' },
  validate: { label: 'Validate', confirm: 'Validate', variant: 'default' },
  'submit-review': { label: 'Submit for review', confirm: 'Submit', variant: 'default' },
  approve: { label: 'Approve', confirm: 'Approve', variant: 'default' },
  reject: { label: 'Reject', confirm: 'Reject', variant: 'destructive' },
};

interface ActionConfirmationPanelProps {
  action: ProposalReviewAction;
  onCancel: () => void;
  onConfirm: (payload: { validatorVersion?: string; rationale?: string }) => void;
  isPending: boolean;
}

function ActionConfirmationPanel({
  action,
  onCancel,
  onConfirm,
  isPending,
}: ActionConfirmationPanelProps): JSX.Element {
  const [validatorVersion, setValidatorVersion] = useState('');
  const [rationale, setRationale] = useState('');

  const needsValidatorVersion = action === 'validate';
  const needsRationale = action === 'approve' || action === 'reject';
  const rationaleRequired = action === 'reject';
  const canSubmit =
    !isPending &&
    (!needsValidatorVersion || validatorVersion.trim().length > 0) &&
    (!rationaleRequired || rationale.trim().length > 0);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    onConfirm({
      validatorVersion: needsValidatorVersion ? validatorVersion.trim() : undefined,
      rationale: needsRationale ? rationale.trim() : undefined,
    });
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-border-default bg-surface-raised mt-4 rounded-md border p-4"
      aria-label={`Confirm ${ACTION_CONFIG[action].label}`}
    >
      <div className="text-fg text-body-sm mb-3 font-semibold">
        Confirm {ACTION_CONFIG[action].label.toLowerCase()}
      </div>

      {needsValidatorVersion && (
        <div className="mb-3">
          <Label htmlFor="validator-version">
            Validator version <span className="text-attention-text">*</span>
          </Label>
          <Input
            id="validator-version"
            value={validatorVersion}
            onChange={(e) => setValidatorVersion(e.target.value)}
            placeholder="e.g. THR-055/1.0.0"
            disabled={isPending}
            className="mt-1.5"
          />
        </div>
      )}

      {needsRationale && (
        <div className="mb-3">
          <Label htmlFor="action-rationale">
            {action === 'reject' ? (
              <>
                Rationale <span className="text-attention-text">*</span>
              </>
            ) : (
              'Rationale (optional)'
            )}
          </Label>
          <Textarea
            id="action-rationale"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            placeholder={
              action === 'reject'
                ? 'Explain why this proposal is rejected…'
                : 'Optional approval notes…'
            }
            disabled={isPending}
            className="mt-1.5"
          />
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onCancel}
          disabled={isPending}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          variant={ACTION_CONFIG[action].variant}
          size="sm"
          disabled={!canSubmit}
          aria-label={`Confirm ${ACTION_CONFIG[action].label.toLowerCase()}`}
        >
          {isPending ? 'Working…' : 'Confirm'}
        </Button>
      </div>
    </form>
  );
}

// ── Main page ────────────────────────────────────────────────────────────

export function ProposalDetailPage(): JSX.Element {
  const { slug, versionId: versionIdParam } = useParams<{
    slug: string;
    versionId: string;
  }>();
  const versionId = versionIdParam ? Number(versionIdParam) : undefined;

  const query = useProposalDetail(slug, versionId);
  const queryClient = useQueryClient();

  const claim = useClaimProposal();
  const validate = useValidateProposal();
  const submitReview = useSubmitReviewProposal();
  const review = useReviewProposal();

  const [confirmingAction, setConfirmingAction] = useState<ProposalReviewAction | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  const anyPending = claim.isPending || validate.isPending || submitReview.isPending || review.isPending;

  const refreshDetail = useCallback(() => {
    if (slug && versionId !== undefined) {
      void queryClient.invalidateQueries({
        queryKey: ['proposal-detail', slug, versionId],
        refetchType: 'active',
      });
    }
  }, [slug, versionId, queryClient]);

  const handleStale = useCallback(() => {
    setConfirmingAction(null);
    setConflictMessage(
      'Another action changed this proposal. The page now shows the refreshed authoritative state.',
    );
    refreshDetail();
  }, [refreshDetail]);

  const runMutation = useCallback(
    async (
      action: ProposalReviewAction,
      payload: { validatorVersion?: string; rationale?: string },
    ) => {
      if (!slug || versionId === undefined || query.data?.last_event_id == null) return;
      const expectedEventId = query.data.last_event_id;
      try {
        switch (action) {
          case 'claim':
            await claim.mutateAsync({ slug, versionId, expectedEventId });
            break;
          case 'validate':
            await validate.mutateAsync({
              slug,
              versionId,
              body: {
                validator_version: payload.validatorVersion!,
                expected_event_id: expectedEventId,
              },
            });
            break;
          case 'submit-review':
            await submitReview.mutateAsync({
              slug,
              versionId,
              body: { expected_event_id: expectedEventId },
            });
            break;
          case 'approve':
            await review.mutateAsync({
              slug,
              versionId,
              body: {
                decision: 'approved',
                rationale: payload.rationale,
                expected_event_id: expectedEventId,
              },
            });
            break;
          case 'reject':
            await review.mutateAsync({
              slug,
              versionId,
              body: {
                decision: 'rejected',
                rationale: payload.rationale!,
                expected_event_id: expectedEventId,
              },
            });
            break;
        }
        setConfirmingAction(null);
        setConflictMessage(null);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409 && err.code === 'stale_concurrency') {
          handleStale();
          return;
        }
        // For any other rejected/invalid response, refetch authoritative state
        // and surface a generic explanation without leaking server data.
        refreshDetail();
        setConfirmingAction(null);
        setConflictMessage('The action could not be applied. The page shows the current authoritative state.');
      }
    },
    [slug, versionId, query, claim, validate, submitReview, review, handleStale],
  );

  const backLink = (
    <Link
      to={`/orgs/${slug ?? ''}/skills`}
      className="text-fg-muted hover:text-fg text-body-sm mb-4 inline-flex items-center gap-1.5"
    >
      <ArrowLeft size={15} aria-hidden="true" />
      Back to skills
    </Link>
  );

  // ── 403: Founder access only (leak NO proposal data) ──────────────────
  if (
    query.isError &&
    query.error instanceof ApiError &&
    query.error.status === 403
  ) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
          {backLink}
          <EmptyState
            icon={<Shield size={28} />}
            title="Founder access required"
            body="Proposal review is restricted to the founder. Agent sessions cannot view or act on proposals."
          />
        </div>
      </div>
    );
  }

  // ── 404 ────────────────────────────────────────────────────────────────
  if (
    query.isError &&
    query.error instanceof ApiError &&
    query.error.status === 404
  ) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
          {backLink}
          <EmptyState
            icon={<AlertTriangle size={28} />}
            title="Proposal not found"
            body="This proposal version is not available — it may have been deleted, or the link is out of date."
          />
        </div>
      </div>
    );
  }

  // ── Generic error with Retry ──────────────────────────────────────────
  if (query.isError) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-5 md:px-7 md:py-6">
          {backLink}
          <EmptyState
            icon={<AlertTriangle size={28} />}
            title="Could not load this proposal"
            body="This proposal is unavailable right now."
          />
          <div className="mt-4 text-center">
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="text-fg-muted hover:text-fg text-body-sm inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold underline underline-offset-2"
            >
              <RefreshCw size={14} aria-hidden="true" />
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Loading ────────────────────────────────────────────────────────────
  if (query.isLoading || !query.data) {
    return <LoadingSkeleton />;
  }

  const detail = query.data;
  const events = timelineEvents(detail.events ?? []);
  const assignments = assignmentProjection(detail.assignments ?? []);
  const mats = materializationProjection(detail.materializations ?? []);
  const validator = validatorFacts(detail.events ?? []);
  const hash = hashDisplay(detail.content_hash);
  const facts = readinessFacts(detail);
  const rejected = isRejected(detail.status);
  const published = isPublished(detail.status);
  const terminal = isTerminal(detail.status);
  const skillMdAvailable = detail.skill_md != null && detail.skill_md.length > 0;

  return (
    // break-words protects long mono identifiers from horizontal overflow.
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 break-words md:px-7 md:py-6">
        {backLink}

        {/* ── Header / identity bar ─────────────────────────────────── */}
        <header className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6">
          {/* Breadcrumb label */}
          <div className="text-fg-subtle text-overline mb-3 flex flex-wrap items-center gap-1.5 tracking-wider uppercase">
            <Package size={12} aria-hidden="true" />
            <span>Proposal</span>
            <span aria-hidden="true">·</span>
            <span className="text-fg-muted font-mono normal-case">
              {detail.skill_id}
            </span>
          </div>

          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-2xs bg-info-soft text-info inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-bold tracking-wide uppercase">
                  <Lock size={10} aria-hidden="true" />
                  founder
                </span>
                <StatusChip status={detail.status} />
                <span className="text-mono-sm text-fg-subtle">
                  v{detail.version}
                </span>
              </div>

              {/* Mono identity + version */}
              <h1 className="text-fg font-mono text-lg leading-snug font-semibold break-all">
                {detail.name ?? detail.skill_id}
              </h1>

              {/* Full copyable hash */}
              <div className="mt-2 flex items-center gap-2">
                <code className="text-mono-sm text-fg-muted max-w-full break-all select-all">
                  {hash.full}
                </code>
                <CopyButton
                  label=""
                  value={hash.full}
                  ariaLabel="Copy full content hash to clipboard"
                />
              </div>

              {/* Readiness strip */}
              {facts.length > 0 && (
                <div className="border-border-subtle mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t pt-3">
                  {facts.map((f) => (
                    <ReadinessRow key={f.label} fact={f} />
                  ))}
                </div>
              )}
            </div>

            {/* Right-side context: lifecycle status + Founder review actions */}
            {detail.status && (
              <div className="shrink-0 text-right">
                <div className="text-fg-subtle text-2xs mb-1 font-bold tracking-wide uppercase">
                  Lifecycle
                </div>
                <StatusChip status={detail.status} />
                {rejected && (
                  <p className="text-attention-text mt-1.5 text-xs font-semibold">
                    Terminal — view only
                  </p>
                )}

                {/* THR-136: minimal Founder review-action affordances */}
                {!terminal && (
                  <div className="mt-2 flex flex-col items-end gap-2">
                    {(() => {
                      const primary = availableReviewAction(detail.status);
                      const showReject = detail.status === 'in_review';
                      if (!primary && !showReject) return null;
                      return (
                        <>
                          {primary && (
                            <Button
                              type="button"
                              size="sm"
                              variant="default"
                              disabled={anyPending || confirmingAction != null}
                              onClick={() => {
                                setConfirmingAction(primary);
                                setConflictMessage(null);
                              }}
                              aria-label={ACTION_CONFIG[primary].label}
                            >
                              {ACTION_CONFIG[primary].label}
                            </Button>
                          )}
                          {showReject && (
                            <Button
                              type="button"
                              size="sm"
                              variant="destructive"
                              disabled={anyPending || confirmingAction != null}
                              onClick={() => {
                                setConfirmingAction('reject');
                                setConflictMessage(null);
                              }}
                              aria-label="Reject"
                            >
                              Reject
                            </Button>
                          )}
                        </>
                      );
                    })()}
                  </div>
                )}
              </div>
            )}
          </div>
        </header>

        {/* ── Conflict / stale-concurrency explanation ──────────────── */}
        {conflictMessage && (
          <section
            className="border-attention/40 bg-attention-soft mt-4 rounded-md border p-4"
            aria-label="Conflict"
            role="status"
          >
            <div className="text-attention-text flex items-center gap-2 text-sm font-semibold">
              <AlertTriangle size={15} aria-hidden="true" />
              Conflict
            </div>
            <p className="text-fg-muted text-body-sm mt-1.5">{conflictMessage}</p>
          </section>
        )}

        {/* ── Founder action confirmation panel ─────────────────────── */}
        {confirmingAction && (
          <ActionConfirmationPanel
            action={confirmingAction}
            isPending={anyPending}
            onCancel={() => setConfirmingAction(null)}
            onConfirm={(payload) => void runMutation(confirmingAction, payload)}
          />
        )}

        {/* ── Rejected terminal banner ──────────────────────────────── */}
        {rejected && (
          <section
            className="border-attention/40 bg-attention-soft mt-4 rounded-md border p-4"
            aria-label="Rejected — terminal"
          >
            <div className="text-attention-text flex items-center gap-2 text-sm font-semibold">
              <XCircle size={15} aria-hidden="true" />
              Rejected — terminal
            </div>
            <p className="text-fg-muted text-body-sm mt-1.5">
              This proposal was rejected by a reviewer. It cannot be reopened,
              re-validated, published, assigned, or rolled back. A future change
              requires a new proposal and version.
            </p>
          </section>
        )}

        {/* ── Published distinct banner ─────────────────────────────── */}
        {published && (
          <section
            className="border-status-open/40 bg-tier-green-tint mt-4 rounded-md border p-4"
            aria-label="Published"
          >
            <div className="text-status-open flex items-center gap-2 text-sm font-semibold">
              <CheckCircle2 size={15} aria-hidden="true" />
              Published
            </div>
            <p className="text-fg-muted text-body-sm mt-1.5">
              Package lifecycle ends here — assignment and effectiveness are a
              separate projection.
            </p>
          </section>
        )}

        {/* ── Main content: SKILL.md pane + evidence rail ───────────── */}
        <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start">
          {/* SKILL.md primary pane */}
          <section
            className="border-border-default bg-surface-raised flex-1 rounded-md border p-5 md:p-6"
            aria-label="Package content"
          >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <Eyebrow>Package content · SKILL.md</Eyebrow>
                <span className="text-fg-subtle text-2xs">
                  read-only at every state
                </span>
              </div>
              {skillMdAvailable && (
                <CopyButton
                  label="Copy SKILL.md"
                  value={detail.skill_md!}
                  ariaLabel="Copy full SKILL.md content to clipboard"
                />
              )}
            </div>

            {skillMdAvailable ? (
              <pre className="text-mono-sm text-fg-muted bg-surface-subtle border-border-subtle max-h-96 overflow-auto rounded-md border p-4 text-sm leading-relaxed whitespace-pre-wrap">
                {detail.skill_md}
              </pre>
            ) : (
              <div
                className="border-attention/30 bg-attention-soft flex items-start gap-2.5 rounded-md border p-3"
                aria-label="SKILL.md unavailable"
              >
                <AlertTriangle
                  size={15}
                  aria-hidden="true"
                  className="text-attention-text mt-0.5 shrink-0"
                />
                <div>
                  <p className="text-attention-text text-body-sm font-semibold">
                    Canonical bytes unavailable
                  </p>
                  <p className="text-fg-muted text-body-sm mt-1">
                    The immutable SKILL.md content could not be loaded from the
                    artifact store. This may be a legacy proposal or the artifact
                    may be missing.
                  </p>
                </div>
              </div>
            )}
          </section>

          {/* Evidence rail (right on desktop, below on mobile) */}
          <aside
            className="border-border-default bg-surface-raised rounded-md border p-5 md:p-6 lg:w-72 lg:shrink-0"
            aria-label="Evidence"
          >
            <Eyebrow>Evidence</Eyebrow>
            <dl>
              {detail.purpose && (
                <EvidenceRow label="Purpose">{detail.purpose}</EvidenceRow>
              )}
              <EvidenceRow label="Policy class">
                <code className="text-mono-sm">
                  {detail.policy_class ?? 'standard_operational'}
                </code>
              </EvidenceRow>
              {detail.target_agent_suggestion && (
                <EvidenceRow label="Suggested target">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="font-mono text-sm">
                      {detail.target_agent_suggestion}
                    </span>
                    <span className="text-2xs text-fg-subtle">(advisory)</span>
                  </span>
                </EvidenceRow>
              )}
              <EvidenceRow label="Proposal ID">
                <code className="text-mono-sm">{detail.skill_id}</code>
              </EvidenceRow>
              <EvidenceRow label="Version">
                v{detail.version}
              </EvidenceRow>
              <EvidenceRow label="Hash">
                <code className="text-mono-sm text-fg-subtle">
                  {hash.short}
                </code>
              </EvidenceRow>
              <EvidenceRow label="Validation">
                {validator.hasValidation ? (
                  <span
                    className={
                      validator.result === 'Passed'
                        ? 'text-status-open font-semibold'
                        : 'text-attention-text font-semibold'
                    }
                  >
                    {validator.result}
                    {validator.version ? ` · ${validator.version}` : ''}
                    {validator.key ? ` · ${validator.key}` : ''}
                  </span>
                ) : (
                  <span className="text-fg-muted">not run</span>
                )}
              </EvidenceRow>
              <EvidenceRow label="Created">
                <time className="text-fg-muted text-xs">
                  {detail.created_at}
                </time>
              </EvidenceRow>
            </dl>
          </aside>
        </div>

        {/* ── Suggested target agent (advisory) ─────────────────────── */}
        {detail.target_agent_suggestion && (
          <section
            className="border-border-default bg-bg-subtle mt-4 flex items-start gap-2.5 rounded-md border p-3"
            aria-label="Suggested target agent"
          >
            <Info size={15} aria-hidden="true" className="text-fg-subtle mt-0.5 shrink-0" />
            <div>
              <p className="text-fg text-body-sm font-semibold">
                Suggested target agent — advisory
              </p>
              <p className="text-fg-muted text-body-sm mt-0.5">
                A hint from the submitting task, not an assignment. The Founder
                chooses the agent explicitly when assigning.
              </p>
            </div>
          </section>
        )}

        {/* ── Provenance ────────────────────────────────────────────── */}
        <section
          className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6"
          aria-label="Provenance"
        >
          <Eyebrow>Provenance</Eyebrow>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {/* Immutable author */}
            <div>
              <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                Proposer (author)
              </dt>
              <dd className="text-fg font-mono text-sm font-semibold">
                {detail.proposer_agent ? (
                  detail.proposer_agent
                ) : (
                  <span className="text-fg-muted font-normal">unknown</span>
                )}
              </dd>
              <span className="text-fg-subtle text-2xs">immutable</span>
            </div>

            {/* Separate claimant */}
            <div>
              <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                Claimed by
              </dt>
              <dd className="text-fg font-mono text-sm font-semibold">
                {detail.claimed_by ? (
                  <>
                    {detail.claimed_by}
                    {detail.claimed_at ? (
                      <span className="text-fg-muted ml-1.5 text-xs font-normal">
                        {detail.claimed_at}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-fg-muted font-normal">not claimed</span>
                )}
              </dd>
            </div>

            {/* Source task */}
            <div>
              <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                Source task
              </dt>
              <dd className="text-fg font-mono text-sm">
                {detail.proposal_task_id ? (
                  <Link
                    to={`/orgs/${slug}/tasks/${detail.proposal_task_id}`}
                    className="hover:text-accent-text focus-visible:ring-accent rounded focus:outline-none focus-visible:ring-2"
                  >
                    {detail.proposal_task_id}
                  </Link>
                ) : (
                  <span className="text-fg-muted">unknown</span>
                )}
              </dd>
            </div>

            {/* Source session */}
            <div>
              <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                Source session
              </dt>
              <dd className="text-fg font-mono text-sm">
                {detail.proposal_session_id ? (
                  <code className="text-mono-sm">
                    {detail.proposal_session_id}
                  </code>
                ) : (
                  <span className="text-fg-muted">unknown</span>
                )}
              </dd>
            </div>

            {/* Reviewer */}
            {detail.reviewer && (
              <div>
                <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                  Reviewer
                </dt>
                <dd className="text-fg font-mono text-sm font-semibold">
                  {detail.reviewer}
                  {detail.reviewed_at ? (
                    <span className="text-fg-muted ml-1.5 text-xs font-normal">
                      {detail.reviewed_at}
                    </span>
                  ) : null}
                </dd>
                {detail.review_decision && (
                  <dd
                    className={`mt-0.5 text-xs font-semibold ${
                      detail.review_decision === 'approved'
                        ? 'text-status-open'
                        : 'text-attention-text'
                    }`}
                  >
                    {detail.review_decision === 'approved'
                      ? 'Approved'
                      : 'Rejected'}
                  </dd>
                )}
              </div>
            )}

            {/* Publisher */}
            {detail.publisher && (
              <div>
                <dt className="text-fg-subtle text-2xs font-bold tracking-wide uppercase">
                  Publisher
                </dt>
                <dd className="text-fg font-mono text-sm font-semibold">
                  {detail.publisher}
                  {detail.published_at ? (
                    <span className="text-fg-muted ml-1.5 text-xs font-normal">
                      {detail.published_at}
                    </span>
                  ) : null}
                </dd>
              </div>
            )}
          </dl>
        </section>

        {/* ── Timeline ──────────────────────────────────────────────── */}
        <TimelineSection events={events} />

        {/* ── Assignment & materialization projection ───────────────── */}
        <section
          className="border-border-default bg-surface-raised mt-4 rounded-md border p-5 md:p-6"
          aria-label="Assignment and materialization"
        >
          <div className="flex items-center justify-between">
            <Eyebrow>Assignment &amp; materialization</Eyebrow>
            <span className="text-fg-subtle text-2xs">
              projection, not a package state
            </span>
          </div>

          {!terminal ? (
            <p className="text-fg-muted text-body-sm">
              Not applicable — the package decision lifecycle is not yet
              complete. Assignment and materialization happen after publication.
            </p>
          ) : hasAssignmentProjection(detail) ? (
            <>
              <p className="text-fg-muted text-body-sm mb-3">
                Derived from version-pinned assignment records and runtime
                materialization — the published package itself never changes.
              </p>

              {assignments.length > 0 && (
                <div className="mt-1">
                  <h4 className="text-fg-subtle text-2xs mb-1.5 font-bold tracking-wide uppercase">
                    Assignments
                  </h4>
                  <ul className="flex flex-col gap-2">
                    {assignments.map((a) => (
                      <li
                        key={a.agentName}
                        className="border-border-subtle bg-surface-subtle flex items-center justify-between gap-3 rounded-md border p-2.5"
                      >
                        <span className="text-fg font-mono text-sm font-semibold">
                          {a.agentName}
                        </span>
                        <span className="text-fg-muted text-xs">
                          {a.version ? `v${a.version}` : '—'}
                          {a.assignedBy ? ` by ${a.assignedBy}` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {mats.length > 0 && (
                <MaterializationList items={mats} />
              )}
            </>
          ) : (
            <p className="text-fg-muted text-body-sm">
              No assignments or materializations recorded.
            </p>
          )}
        </section>

        {/* ── Guidance-only footer ──────────────────────────────────── */}
        <div className="border-border-default bg-bg-subtle text-fg-muted text-body-sm mt-4 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
          <Info size={15} aria-hidden="true" className="text-fg-subtle shrink-0" />
          <span>
            <b className="text-fg font-semibold">Guidance visibility only.</b>{' '}
            Assignment and materialization are separate version-pinned
            projections — they never change what tools or commands an agent can
            use.
          </span>
        </div>
      </div>
    </div>
  );
}
