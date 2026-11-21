import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * S4 — Eligibility editor. The single org-level gate (`agents` selector).
 *
 * mode all | whitelist; include/exclude multi-select pickers from the LIVE
 * roster (no free text). Live "resulting eligible set: N agents" preview.
 * Save is impact-heavy → confirm with the resulting eligible-set.
 *
 * Lives in `src/shared/work-hours/` because both Work Hours and
 * Settings ▸ Organization operating controls mount it; it is the single
 * owner of the existing `working_hours.agents` patch mutation.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { OwnerSaveNotice, useConfigOwner, useOwnerGate } from '@/shared/owners';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import { useUpdateOrgSettings } from '@/hooks/settings';
import { useTranslation } from '@/hooks/i18n';
import type { WorkingHoursSettings } from '@/lib/api/types';
import { ErrorPanel } from './ErrorPanel';
import { extractServerErrors } from './extractServerErrors';
import { eligibleSet } from './eligibleSet';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wh: WorkingHoursSettings;
  allAgents: string[];
  onSaved: () => void;
}

export function EligibilityEditorDialog({
  open,
  onOpenChange,
  wh,
  allAgents,
  onSaved,
}: Props): JSX.Element {
  const { t, render } = useTranslation();
  const mutation = useUpdateOrgSettings();
  const [mode, setMode] = useState<string>(wh.agents.mode);
  const [include, setInclude] = useState<string[]>(wh.agents.include);
  const [exclude, setExclude] = useState<string[]>(wh.agents.exclude);
  const [errors, setErrors] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);

  // THR-387 S6-W2a: a Work Hours config owner while the editor holds edits.
  const { slug } = useParams<{ slug: string }>();
  // The draft belongs to the org it was opened for: Discard consent closes the
  // editor, and any org change closes it before it can render or save there.
  const openedOrg = useRef(slug);
  if (!open) openedOrg.current = slug; // while closed, follow the route
  const boundOrg = openedOrg.current;
  const orgChanged = open && slug !== boundOrg;
  useEffect(() => {
    if (orgChanged) onOpenChange(false);
  }, [orgChanged, onOpenChange]);
  const draft = { mode, include, exclude };
  const owner = useConfigOwner({
    page: 'settings.workHours',
    org: boundOrg ?? null,
    onDiscard: () => onOpenChange(false),
    dirty: open && !orgChanged && JSON.stringify(draft) !== JSON.stringify({ mode: wh.agents.mode, include: wh.agents.include, exclude: wh.agents.exclude }),
    draft,
  });
  // Direct close paths (close button, Escape, Cancel) ask the owner gate
  // first: unsent edits get Keep editing / Discard, a sent save holds.
  const { requestOwnerTransition } = useOwnerGate();
  const requestOpenChange = (next: boolean): void => {
    if (next) {
      onOpenChange(true);
      return;
    }
    requestOwnerTransition({ effect: 'close', affects: (record) => record.id === owner.id, proceed: () => onOpenChange(false) });
  };

  const resulting = useMemo(
    () => eligibleSet(allAgents, { mode, include, exclude }),
    [allAgents, mode, include, exclude],
  );

  function toggle(list: string[], setList: (v: string[]) => void, name: string) {
    setList(list.includes(name) ? list.filter((n) => n !== name) : [...list, name]);
  }

  // Receipt admission counts as saving, like the request itself.
  const saving = mutation.isPending || owner.busy;

  async function doSave() {
    setErrors([]);
    const result = await owner.track(() =>
      mutation.mutateAsync({
        working_hours: { agents: { mode, include, exclude } },
      }),
    );
    setConfirming(false);
    if (result.ok) {
      onSaved();
      // Close only when the acknowledged draft is still the current one. A
      // newer draft typed while the save was pending (via Back) stays open and
      // dirty against the saved value, which is now the baseline.
      if (result.current) onOpenChange(false);
    } else if (result.outcome === 'failed') {
      setErrors(extractServerErrors(result.error));
    }
  }

  return (
    <Dialog open={open && !orgChanged} onOpenChange={requestOpenChange}>
      <DialogContent className="max-w-lg" closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('workHours.eligibilityEditor.title')}</DialogTitle>
          <DialogDescription>{t('workHours.eligibilityEditor.description')}</DialogDescription>
        </DialogHeader>

        <OwnerSaveNotice owner={owner} className="" />
        {errors.length > 0 && <ErrorPanel errors={errors} />}

        {confirming ? (
          <div className="text-sm">
            <p className="text-text-primary">
              {render('workHours.eligibilityEditor.confirmResult', {
                count: resulting.length,
                n: <span className="font-semibold tabular-nums">{resulting.length}</span>,
              })}
            </p>
            <p className="text-text-muted mt-1 break-words">
              {resulting.length > 0 ? resulting.join(', ') : t('workHours.dialog.none')}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <span className="text-text-primary text-sm font-medium">
                {t('workHours.eligibilityEditor.mode')}
              </span>
              <Select value={mode} onValueChange={setMode}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">all</SelectItem>
                  <SelectItem value="whitelist">whitelist</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {mode === 'whitelist' && (
              <AgentPicker
                title={t('workHours.eligibilityEditor.include')}
                roster={allAgents}
                selected={include}
                onToggle={(name) => toggle(include, setInclude, name)}
              />
            )}

            <AgentPicker
              title={t('workHours.eligibilityEditor.exclude')}
              roster={allAgents}
              selected={exclude}
              onToggle={(name) => toggle(exclude, setExclude, name)}
            />

            <div
              role="status"
              className="bg-surface-sunken rounded-sm p-2 text-sm"
            >
              {render('workHours.eligibilityEditor.liveResult', {
                count: resulting.length,
                n: <span className="font-semibold tabular-nums">{resulting.length}</span>,
              })}
              {resulting.length > 0 && (
                <span className="text-text-muted">
                  {' '}
                  — {resulting.join(', ')}
                </span>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          {confirming ? (
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                {t('workHours.dialog.back')}
              </Button>
              <Button onClick={() => void doSave()} disabled={saving}>
                {saving ? t('workHours.dialog.saving') : t('workHours.dialog.confirmSave')}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => requestOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button onClick={() => setConfirming(true)} disabled={saving}>
                {t('workHours.dialog.reviewImpact')}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AgentPicker({
  title,
  roster,
  selected,
  onToggle,
}: {
  title: string;
  roster: string[];
  selected: string[];
  onToggle: (name: string) => void;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <div>
      <p className="text-text-muted mb-1 text-xs font-medium tracking-wide uppercase">
        {title}
      </p>
      <div className="flex flex-wrap gap-1">
        {roster.length === 0 && (
          <span className="text-text-muted text-xs">
            {t('workHours.eligibilityEditor.emptyRoster')}
          </span>
        )}
        {roster.map((name) => {
          const on = selected.includes(name);
          return (
            <button
              key={name}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(name)}
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                on
                  ? 'bg-accent-default text-text-inverse'
                  : 'bg-surface-raised text-text-secondary border-border-default border'
              }`}
            >
              <IdentityName canonicalId={name} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
