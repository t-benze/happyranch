/**
 * AssistantSection — the single place to configure the System Assistant.
 *
 * Owns the full setup/config flow lifted out of SystemAssistantPage: status
 * badge, init / repair / reconfigure, and the register-executor form (executor
 * picker incl. "other" free-text, command + argv). The Full Session terminal
 * surface has been removed; the A-mode dock (AssistantDockHost) is a global
 * toggle — no route-based link is available.
 */
import { useState } from 'react';
import { ApiError } from '@/lib/api';
import {
  useAssistantStatus,
  useInitAssistant,
  useRegisterAssistant,
  useRepairAssistant,
} from '@/hooks/assistant';
import type { AssistantState, AssistantStatus } from '@/lib/api/types';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';

const STATE_LABEL: Record<AssistantState, string> = {
  uninitialized: 'Uninitialized',
  configured: 'Configured',
  stale_or_broken: 'Stale or broken',
};

const STATE_BADGE: Record<AssistantState, string> = {
  uninitialized: 'bg-bg-raised text-fg-muted',
  configured: 'bg-feedback-success/15 text-feedback-success',
  stale_or_broken: 'bg-feedback-danger/15 text-feedback-danger',
};

/** Built-in executors the picker offers; "other" reveals a free-text field. */
const EXECUTOR_OPTIONS = ['claude', 'codex', 'opencode', 'pi', 'other'] as const;

/** Surface daemon structural errors verbatim; fall back to a readable string. */
function describeRegisterError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code) {
      // assistant_registration_invalid / assistant_executable_not_found are
      // shown verbatim so the founder sees exactly what the daemon reported.
      const detail = err.detail as { message?: string; executable?: string } | null;
      if (err.code === 'assistant_executable_not_found' && detail?.executable) {
        return `${err.code}: ${detail.executable}`;
      }
      if (detail?.message) return `${err.code}: ${detail.message}`;
      return err.code;
    }
    return `Registration failed (HTTP ${err.status}).`;
  }
  return String(err);
}

export function AssistantSection(): JSX.Element {
  const statusQuery = useAssistantStatus();
  const status = statusQuery.data;

  return (
    <section>
      {statusQuery.isLoading ? (
        <p className="text-text-secondary text-sm">Loading…</p>
      ) : statusQuery.isError || !status ? (
        <p className="text-feedback-danger text-sm">Could not load assistant status.</p>
      ) : (
        <div className="flex flex-col gap-6">
          <StatusCard status={status} />
          <SetupActions status={status} />
          <RegisterExecutorSection currentExecutor={status.selected_executor} />
        </div>
      )}
    </section>
  );
}

function StatusCard({ status }: { status: AssistantStatus }): JSX.Element {
  return (
    <section
      aria-label="Assistant status"
      className="border-border-default bg-surface-raised shadow-pasture-sm flex flex-col gap-2 rounded-lg border p-4"
    >
      <div className="flex items-center gap-2">
        <span className="text-text-secondary text-sm">State</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[status.state]}`}
        >
          {STATE_LABEL[status.state]}
        </span>
      </div>
      <dl className="flex flex-col gap-1 text-sm">
        <div className="flex gap-4">
          <dt className="text-text-secondary w-24 shrink-0">Executor</dt>
          <dd className="text-text-primary font-mono break-all tabular-nums">{status.selected_executor ?? '—'}</dd>
        </div>
        <div className="flex gap-4">
          <dt className="text-text-secondary w-24 shrink-0">Workspace</dt>
          <dd className="text-text-primary break-all">{status.workspace_path ?? '—'}</dd>
        </div>
      </dl>
      {status.state === 'stale_or_broken' && status.detail && (
        <p role="alert" className="text-feedback-danger text-sm">
          {status.detail}
        </p>
      )}
    </section>
  );
}

function SetupActions({ status }: { status: AssistantStatus }): JSX.Element {
  const initMutation = useInitAssistant();
  const repairMutation = useRepairAssistant();
  const [reconfigureOpen, setReconfigureOpen] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);

  const initialize = async (): Promise<void> => {
    await initMutation.mutateAsync({ reconfigure: false });
    setShowInstructions(true);
  };

  return (
    <section
      aria-label="Setup actions"
      className="border-border bg-bg-subtle flex flex-col gap-3 rounded-md border p-4"
    >
      <h2 className="text-fg text-sm font-semibold">Setup</h2>

      {status.state === 'uninitialized' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">
            Prepare the registration workspace, then either register an executor
            below or launch your CLI in the workspace and let it self-register.
          </p>
          <div>
            <Button onClick={initialize} disabled={initMutation.isPending}>
              {initMutation.isPending ? 'Initializing…' : 'Initialize workspace'}
            </Button>
          </div>
          {showInstructions && (
            <div className="border-border-default bg-surface-sunken rounded-lg border p-3 text-sm">
              <p className="text-text-primary font-medium">Self-registration</p>
              <ol className="text-text-secondary mt-1 list-decimal pl-5">
                <li>
                  Open your agentic CLI (claude, codex, opencode, pi, …) in the
                  workspace shown above.
                </li>
                <li>
                  Ask it to register itself; it runs{' '}
                  <code className="text-text-primary font-mono">happyranch assistant register</code>.
                </li>
              </ol>
            </div>
          )}
        </div>
      )}

      {status.state === 'stale_or_broken' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">
            The workspace drifted from the saved config. Repair rebuilds it from
            the recorded executor without clearing your registration.
          </p>
          <div>
            <Button onClick={() => repairMutation.mutateAsync()} disabled={repairMutation.isPending}>
              {repairMutation.isPending ? 'Repairing…' : 'Repair'}
            </Button>
          </div>
        </div>
      )}

      {status.state === 'configured' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">
            Reconfiguring closes any open sessions and clears the saved config so
            you can register a different executor from scratch.
          </p>
          <div>
            <Button variant="destructive" onClick={() => setReconfigureOpen(true)}>
              Reconfigure…
            </Button>
          </div>
        </div>
      )}

      <ReconfigureDialog open={reconfigureOpen} onOpenChange={setReconfigureOpen} />
    </section>
  );
}

function ReconfigureDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): JSX.Element {
  const initMutation = useInitAssistant();

  const confirm = async (): Promise<void> => {
    await initMutation.mutateAsync({ reconfigure: true });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reconfigure the assistant?</DialogTitle>
        </DialogHeader>
        <p className="text-text-secondary text-sm">
          This closes all open assistant sessions and clears the saved
          configuration. You will need to register an executor again.
        </p>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={confirm}
            disabled={initMutation.isPending}
          >
            {initMutation.isPending ? 'Reconfiguring…' : 'Reconfigure'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RegisterExecutorSection({
  currentExecutor,
}: {
  currentExecutor: string | null;
}): JSX.Element {
  const registerMutation = useRegisterAssistant();
  const [executorChoice, setExecutorChoice] = useState<string>(
    EXECUTOR_OPTIONS[0],
  );
  const [customExecutor, setCustomExecutor] = useState('');
  const [command, setCommand] = useState('');
  const [argv, setArgv] = useState('');
  const [error, setError] = useState<string | null>(null);

  const executor = executorChoice === 'other' ? customExecutor.trim() : executorChoice;

  const submit = async (): Promise<void> => {
    setError(null);
    if (!executor) {
      setError('Choose or name an executor.');
      return;
    }
    if (!command.trim()) {
      setError('Enter the command to launch.');
      return;
    }
    // Empty argv lets the server default to [command]; otherwise split on
    // whitespace into a list, exactly like the CLI's JSON argv array.
    const argvList = argv.trim() ? argv.trim().split(/\s+/) : [command.trim()];
    try {
      await registerMutation.mutateAsync({
        executor,
        command: command.trim(),
        argv: argvList,
      });
    } catch (err) {
      setError(describeRegisterError(err));
    }
  };

  return (
    <section
      aria-label="Register executor"
      className="border-border-default bg-surface-raised shadow-pasture-sm flex flex-col gap-3 rounded-lg border p-4"
    >
      <h2 className="text-text-primary font-display text-sm">
        {currentExecutor ? 'Switch executor' : 'Register executor'}
      </h2>
      <p className="text-text-secondary text-sm">
        Re-registering preserves the workspace — the server derives it from the
        runtime root, not from any input here — and only one executor is active
        at a time, so registering replaces the current one.
      </p>

      <div className="flex flex-col gap-1">
        <Label htmlFor="assistant-executor">Executor</Label>
        <Select value={executorChoice} onValueChange={setExecutorChoice}>
          <SelectTrigger id="assistant-executor" aria-label="Executor" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EXECUTOR_OPTIONS.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt === 'other' ? 'Other…' : opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {executorChoice === 'other' && (
        <div className="flex flex-col gap-1">
          <Label htmlFor="assistant-executor-name">Executor name</Label>
          <Input
            id="assistant-executor-name"
            value={customExecutor}
            placeholder="my-cli"
            onChange={(e) => {
              setCustomExecutor(e.target.value);
              setError(null);
            }}
          />
        </div>
      )}

      <div className="flex flex-col gap-1">
        <Label htmlFor="assistant-command">Command</Label>
        <Input
          id="assistant-command"
          value={command}
          placeholder="claude"
          onChange={(e) => {
            setCommand(e.target.value);
            setError(null);
          }}
        />
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor="assistant-argv">Argv (optional — defaults to the command)</Label>
        <Input
          id="assistant-argv"
          value={argv}
          placeholder="claude --dangerously-skip-permissions"
          onChange={(e) => {
            setArgv(e.target.value);
            setError(null);
          }}
        />
        <p className="text-text-muted text-xs">
          Space-separated. Leave blank to launch the command with no extra args.
        </p>
      </div>

      {error && (
        <p role="alert" className="text-feedback-danger text-sm break-all">
          {error}
        </p>
      )}

      <div>
        <Button onClick={submit} disabled={registerMutation.isPending}>
          {registerMutation.isPending ? 'Registering…' : 'Register'}
        </Button>
      </div>
    </section>
  );
}
