import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * EscalationInboxRow — inline continue-and-resolve expander for the
 * "Waiting on you" panel.
 *
 * Click the card (or focus its toggle button and press Enter/Space) to
 * expand → rationale textarea autofocuses → the submit button or the
 * platform submit shortcut (⌘↵ on macOS, Ctrl+↵ elsewhere) sends it; Esc
 * collapses. Sends { decision: 'continue', rationale } to the existing
 * /tasks/{id}/resolve-escalation route. Cancel-with-rationale lives on
 * the Tasks page's ResolveEscalationDialog; the dashboard is continue-only
 * because that's the common founder action.
 *
 * KB promotion is deferred — see spec §4.6.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { DashboardEscalationRow } from '@/lib/api/types';
import { useResolveEscalation } from '@/hooks/tasks';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { KbdChip } from '@/design-system/patterns/KbdChip';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { formatAge } from '../dashboardCopy';

/** macOS/iOS submit with ⌘↵; every other platform uses Ctrl+↵ (THR-376 I3). */
function isApplePlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent;
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** An escalation is "stale" once it has waited a full day (THR-061 slice 1).
 *  Purely client-derived from the real `age_seconds` field — no new metric. */
const STALE_THRESHOLD_SECONDS = 86_400;

/**
 * Per-flavor chip tint (THR-061 slice 1). Maps each DERIVED `flavor`
 * ("needs-decision" | "exhausted" | "over-budget") to an EXISTING Pasture
 * status tint so the founder can tell WHY a task is waiting at a glance.
 * Existing design-system tokens only — no new hex (hex gate). Unknown
 * flavors fall back to the neutral escalated tint the chip shipped with.
 */
function flavorChipClass(flavor: string): string {
  switch (flavor) {
    case 'needs-decision':
      return 'bg-attention-soft text-attention-text';
    case 'over-budget':
      return 'bg-tier-yellow-tint text-status-archiving';
    case 'exhausted':
      return 'bg-tier-red-tint text-status-abandoned';
    default:
      return 'bg-tier-red-tint text-status-escalated';
  }
}

interface EscalationInboxRowProps {
  row: DashboardEscalationRow;
  expanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  slug: string;
}

/**
 * Localized display label for a KNOWN derived flavor. The machine value stays
 * the chip-tint/logic key; an unknown flavor renders verbatim (API data).
 */
const FLAVOR_LABEL_KEY: Record<string, MessageKey> = {
  'needs-decision': 'dashboard.escalation.flavor.needsDecision',
  'over-budget': 'dashboard.escalation.flavor.overBudget',
  exhausted: 'dashboard.escalation.flavor.exhausted',
};

export function EscalationInboxRow({
  row,
  expanded,
  onExpand,
  onCollapse,
  slug,
}: EscalationInboxRowProps): JSX.Element {
  const { t, locale } = useTranslation();
  const [rationale, setRationale] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const formId = useId();
  const [isApple] = useState(isApplePlatform);
  const resolve = useResolveEscalation(row.task_id);
  const isStale = row.age_seconds >= STALE_THRESHOLD_SECONDS;

  useEffect(() => {
    if (expanded) {
      const t = setTimeout(() => taRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [expanded]);

  async function submit() {
    await resolve.mutateAsync({ decision: 'continue', rationale });
  }

  /** Collapse and hand focus back to the card toggle so keyboard users keep
   *  their place once the textarea unmounts. */
  function collapse() {
    onCollapse();
    toggleRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Escape') {
      collapse();
      e.preventDefault();
      return;
    }
    if (e.key === 'Enter' && (isApple ? e.metaKey : e.ctrlKey)) {
      e.preventDefault();
      if (!resolve.isPending) void submit();
    }
  }

  return (
    <div
      className={cn(
        'bg-surface-raised relative rounded-md border p-3 transition-colors',
        'has-[[data-esc-toggle]:focus-visible]:ring-ring has-[[data-esc-toggle]:focus-visible]:ring-2',
        expanded
          ? 'border-border-strong shadow-pasture-sm'
          : 'border-border-default hover:border-border-strong',
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-text-primary text-task-meta font-mono font-semibold">
          <IdentityName canonicalId={row.agent} />
        </span>
        <span className="text-text-muted text-task-meta font-mono">·</span>
        <span className="text-text-muted text-task-meta font-mono">
          {/* Raised above the collapsed card's stretched toggle so the task
              link stays independently clickable (no nested interactives). */}
          <span className="relative z-10">
            <IdBadge id={row.task_id} kind="task" to={`/orgs/${slug}/tasks/${row.task_id}`} />
          </span>
          {' · '}{row.team}{' · '}
          <span
            className={isStale ? 'text-status-escalated font-medium' : undefined}
          >
            {formatAge(locale, row.age_seconds)}
          </span>
        </span>
        {isStale && (
          <span
            className="border-tier-red text-status-escalated relative z-10 rounded-sm border px-1 text-xs font-medium uppercase"
            title={t('dashboard.escalation.staleTitle')}
          >
            {t('dashboard.escalation.stale')}
          </span>
        )}
        {row.flavor && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${flavorChipClass(
              row.flavor,
            )}`}
          >
            {Object.prototype.hasOwnProperty.call(FLAVOR_LABEL_KEY, row.flavor)
              ? t(FLAVOR_LABEL_KEY[row.flavor])
              : row.flavor}
          </span>
        )}
      </div>
      <button
        ref={toggleRef}
        type="button"
        data-esc-toggle=""
        aria-expanded={expanded}
        aria-controls={expanded ? formId : undefined}
        onClick={() => (expanded ? collapse() : onExpand())}
        className={cn(
          'text-text-primary mt-1 block w-full cursor-pointer text-left text-sm focus-visible:outline-none',
          // Collapsed: stretch the hit area over the whole card.
          !expanded && 'before:absolute before:inset-0 before:rounded-md',
        )}
      >
        {row.question}
      </button>
      {row.escalation_reason?.refusal_code && row.escalation_reason.secondary && (
        <p className="text-text-muted mt-1 text-xs">
          {row.escalation_reason.secondary}
        </p>
      )}

      {expanded && (
        <div id={formId} className="mt-3 space-y-2">
          <Textarea
            ref={taRef}
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('dashboard.escalation.placeholder', { agent: row.agent })}
            aria-keyshortcuts={isApple ? 'Meta+Enter' : 'Control+Enter'}
            className="text-body-sm min-h-16"
            rows={3}
          />
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="text-text-muted text-caption inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap"
              data-esc-send-hint=""
            >
              <KbdChip keys={isApple ? ['⌘', '↵'] : ['Ctrl', '↵']} />
              {t('dashboard.escalation.sendHint')}
            </span>
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" size="sm" onClick={collapse} type="button">
                {t('dashboard.escalation.cancel')}
              </Button>
              <Button
                size="sm"
                onClick={() => void submit()}
                disabled={resolve.isPending}
                type="button"
              >
                {resolve.isPending
                  ? t('dashboard.escalation.continuing')
                  : t('dashboard.escalation.continue')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
