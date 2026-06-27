/**
 * S4 — Eligibility editor. The single org-level gate (`agents` selector).
 *
 * mode all | whitelist; include/exclude multi-select pickers from the LIVE
 * roster (no free text). Live "resulting eligible set: N agents" preview.
 * Save is impact-heavy → confirm with the resulting eligible-set.
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
import type { WorkingHoursSettings } from '@/lib/api/types';
import { ErrorPanel } from './components';
import { extractServerErrors } from './errors';
import { eligibleSet } from './merge';

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
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit eligibility</DialogTitle>
          <DialogDescription>
            The single org-level gate controlling which agents are eligible for
            work hours. Pickers are populated from the live roster.
          </DialogDescription>
        </DialogHeader>

        {errors.length > 0 && <ErrorPanel errors={errors} />}

        {confirming ? (
          <div className="text-sm">
            <p className="text-text-primary">
              Resulting eligible set:{' '}
              <span className="font-semibold tabular-nums">{resulting.length}</span>{' '}
              agent{resulting.length !== 1 ? 's' : ''}.
            </p>
            <p className="text-text-muted mt-1 break-words">
              {resulting.length > 0 ? resulting.join(', ') : '(none)'}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <span className="text-text-primary text-sm font-medium">mode</span>
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
                title="include"
                roster={allAgents}
                selected={include}
                onToggle={(name) => toggle(include, setInclude, name)}
              />
            )}

            <AgentPicker
              title="exclude"
              roster={allAgents}
              selected={exclude}
              onToggle={(name) => toggle(exclude, setExclude, name)}
            />

            <div
              role="status"
              className="bg-surface-sunken rounded p-2 text-sm"
            >
              Resulting eligible set:{' '}
              <span className="font-semibold tabular-nums">{resulting.length}</span>{' '}
              agent{resulting.length !== 1 ? 's' : ''}
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
                Back
              </Button>
              <Button onClick={() => void doSave()} disabled={mutation.isPending}>
                {mutation.isPending ? 'Saving…' : 'Confirm & save'}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={() => setConfirming(true)} disabled={mutation.isPending}>
                Review impact…
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
  return (
    <div>
      <p className="text-text-muted mb-1 text-xs font-medium tracking-wide uppercase">
        {title}
      </p>
      <div className="flex flex-wrap gap-1">
        {roster.length === 0 && (
          <span className="text-text-muted text-xs">No agents in roster.</span>
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
              {name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
