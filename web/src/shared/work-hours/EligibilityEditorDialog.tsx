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
import { useMemo, useState } from 'react';
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

  const resulting = useMemo(
    () => eligibleSet(allAgents, { mode, include, exclude }),
    [allAgents, mode, include, exclude],
  );

  function toggle(list: string[], setList: (v: string[]) => void, name: string) {
    setList(list.includes(name) ? list.filter((n) => n !== name) : [...list, name]);
  }

  async function doSave() {
    setErrors([]);
    try {
      await mutation.mutateAsync({
        working_hours: { agents: { mode, include, exclude } },
      });
      setConfirming(false);
      onSaved();
      onOpenChange(false);
    } catch (err: unknown) {
      setConfirming(false);
      setErrors(extractServerErrors(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('workHours.eligibilityEditor.title')}</DialogTitle>
          <DialogDescription>{t('workHours.eligibilityEditor.description')}</DialogDescription>
        </DialogHeader>

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
              className="bg-surface-sunken rounded p-2 text-sm"
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
              <Button onClick={() => void doSave()} disabled={mutation.isPending}>
                {mutation.isPending ? t('workHours.dialog.saving') : t('workHours.dialog.confirmSave')}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button onClick={() => setConfirming(true)} disabled={mutation.isPending}>
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
                  ? 'bg-accent text-accent-fg'
                  : 'bg-bg-raised text-fg-muted border-border border'
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
