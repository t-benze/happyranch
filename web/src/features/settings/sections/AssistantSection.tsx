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
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';
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

const STATE_LABEL: Record<AssistantState, MessageKey> = {
  uninitialized: 'settings.assistant.state.uninitialized',
  configured: 'settings.assistant.state.configured',
  stale_or_broken: 'settings.assistant.state.staleOrBroken',
};

const STATE_BADGE: Record<AssistantState, string> = {
  uninitialized: 'bg-bg-raised text-fg-muted',
  configured: 'bg-feedback-success/15 text-feedback-success',
  stale_or_broken: 'bg-feedback-danger/15 text-feedback-danger',
};

/** Built-in executors the picker offers; "other" reveals a free-text field. */
const EXECUTOR_OPTIONS = ['claude', 'codex', 'opencode', 'pi', 'other'] as const;

/**
 * A register-form error: either product-owned copy (a catalog key, translated
 * at render so it follows a locale switch) or a raw daemon diagnostic shown
 * verbatim in every locale.
 */
type RegisterError = { key: MessageKey; params?: MessageParams } | { raw: string };

/** Surface daemon structural errors verbatim; fall back to a readable string. */
function describeRegisterError(err: unknown): RegisterError {
  if (err instanceof ApiError) {
    if (err.code) {
      // assistant_registration_invalid / assistant_executable_not_found are
      // shown verbatim so the founder sees exactly what the daemon reported.
      const detail = err.detail as { message?: string; executable?: string } | null;
      if (err.code === 'assistant_executable_not_found' && detail?.executable) {
        return { raw: `${err.code}: ${detail.executable}` };
      }
      if (detail?.message) return { raw: `${err.code}: ${detail.message}` };
      return { raw: err.code };
    }
    return { key: 'settings.assistant.register.errorHttp', params: { status: err.status } };
  }
  return { raw: String(err) };
}

export function AssistantSection(): JSX.Element {
  const { t } = useTranslation();
  const statusQuery = useAssistantStatus(true);
  const status = statusQuery.data;

  return (
    <section>
      {statusQuery.isLoading ? (
        <p className="text-text-secondary text-sm">{t('settings.assistant.loading')}</p>
      ) : statusQuery.isError || !status ? (
        <p className="text-feedback-danger text-sm">{t('settings.assistant.loadError')}</p>
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
  const { t } = useTranslation();
  return (
    <section
      aria-label={t('settings.assistant.status.aria')}
      className="border-border-default bg-surface-raised shadow-pasture-sm flex flex-col gap-2 rounded-lg border p-4"
    >
      <div className="flex items-center gap-2">
        <span className="text-text-secondary text-sm">{t('settings.assistant.status.state')}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[status.state]}`}
        >
          {t(STATE_LABEL[status.state])}
        </span>
      </div>
      <dl className="flex flex-col gap-1 text-sm">
        <div className="flex gap-4">
          <dt className="text-text-secondary w-24 shrink-0">{t('settings.assistant.executor')}</dt>
          <dd className="text-text-primary font-mono break-all tabular-nums">{status.selected_executor ?? '—'}</dd>
        </div>
        <div className="flex gap-4">
          <dt className="text-text-secondary w-24 shrink-0">
            {t('settings.assistant.status.workspace')}
          </dt>
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
  const { t, render } = useTranslation();
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
      aria-label={t('settings.assistant.setup.aria')}
      className="border-border bg-bg-subtle flex flex-col gap-3 rounded-md border p-4"
    >
      <h2 className="text-fg text-sm font-semibold">{t('settings.assistant.setup.title')}</h2>

      {status.state === 'uninitialized' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">
            {t('settings.assistant.setup.uninitializedBody')}
          </p>
          <div>
            <Button onClick={initialize} disabled={initMutation.isPending}>
              {initMutation.isPending
                ? t('settings.assistant.setup.initializing')
                : t('settings.assistant.setup.initialize')}
            </Button>
          </div>
          {showInstructions && (
            <div className="border-border-default bg-surface-sunken rounded-lg border p-3 text-sm">
              <p className="text-text-primary font-medium">
                {t('settings.assistant.setup.selfRegistration.title')}
              </p>
              <ol className="text-text-secondary mt-1 list-decimal pl-5">
                <li>{t('settings.assistant.setup.selfRegistration.step1')}</li>
                <li>
                  {render('settings.assistant.setup.selfRegistration.step2', {
                    command: (
                      <code className="text-text-primary font-mono">happyranch assistant register</code>
                    ),
                  })}
                </li>
              </ol>
            </div>
          )}
        </div>
      )}

      {status.state === 'stale_or_broken' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">{t('settings.assistant.setup.staleBody')}</p>
          <div>
            <Button onClick={() => repairMutation.mutateAsync()} disabled={repairMutation.isPending}>
              {repairMutation.isPending
                ? t('settings.assistant.setup.repairing')
                : t('settings.assistant.setup.repair')}
            </Button>
          </div>
        </div>
      )}

      {status.state === 'configured' && (
        <div className="flex flex-col gap-2">
          <p className="text-text-secondary text-sm">
            {t('settings.assistant.setup.configuredBody')}
          </p>
          <div>
            <Button variant="destructive" onClick={() => setReconfigureOpen(true)}>
              {t('settings.assistant.setup.reconfigure')}
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
  const { t } = useTranslation();
  const initMutation = useInitAssistant();

  const confirm = async (): Promise<void> => {
    await initMutation.mutateAsync({ reconfigure: true });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('settings.assistant.reconfigure.title')}</DialogTitle>
        </DialogHeader>
        <p className="text-text-secondary text-sm">{t('settings.assistant.reconfigure.body')}</p>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={confirm}
            disabled={initMutation.isPending}
          >
            {initMutation.isPending
              ? t('settings.assistant.reconfigure.confirming')
              : t('settings.assistant.reconfigure.confirm')}
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
  const { t } = useTranslation();
  const registerMutation = useRegisterAssistant();
  const [executorChoice, setExecutorChoice] = useState<string>(
    EXECUTOR_OPTIONS[0],
  );
  const [customExecutor, setCustomExecutor] = useState('');
  const [command, setCommand] = useState('');
  const [argv, setArgv] = useState('');
  const [error, setError] = useState<RegisterError | null>(null);

  const executor = executorChoice === 'other' ? customExecutor.trim() : executorChoice;

  const submit = async (): Promise<void> => {
    setError(null);
    if (!executor) {
      setError({ key: 'settings.assistant.register.errorNoExecutor' });
      return;
    }
    if (!command.trim()) {
      setError({ key: 'settings.assistant.register.errorNoCommand' });
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
      aria-label={t('settings.assistant.register.title')}
      className="border-border-default bg-surface-raised shadow-pasture-sm flex flex-col gap-3 rounded-lg border p-4"
    >
      <h2 className="text-text-primary font-display text-sm">
        {currentExecutor
          ? t('settings.assistant.register.switchTitle')
          : t('settings.assistant.register.title')}
      </h2>
      <p className="text-text-secondary text-sm">{t('settings.assistant.register.preserveNote')}</p>
      <p className="text-text-secondary text-sm">{t('settings.assistant.register.noRestart')}</p>

      <div className="flex flex-col gap-1">
        <Label htmlFor="assistant-executor">{t('settings.assistant.executor')}</Label>
        <Select value={executorChoice} onValueChange={setExecutorChoice}>
          <SelectTrigger id="assistant-executor" aria-label={t('settings.assistant.executor')} className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EXECUTOR_OPTIONS.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt === 'other' ? t('settings.assistant.register.other') : opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {executorChoice === 'other' && (
        <div className="flex flex-col gap-1">
          <Label htmlFor="assistant-executor-name">
            {t('settings.assistant.register.executorName')}
          </Label>
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
        <Label htmlFor="assistant-command">{t('settings.assistant.register.command')}</Label>
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
        <Label htmlFor="assistant-argv">{t('settings.assistant.register.argv')}</Label>
        <Input
          id="assistant-argv"
          value={argv}
          placeholder="claude --dangerously-skip-permissions"
          onChange={(e) => {
            setArgv(e.target.value);
            setError(null);
          }}
        />
        <p className="text-text-muted text-xs">{t('settings.assistant.register.argvHint')}</p>
      </div>

      {error && (
        <p role="alert" className="text-feedback-danger text-sm break-all">
          {'raw' in error ? error.raw : t(error.key, error.params)}
        </p>
      )}

      <div>
        <Button onClick={submit} disabled={registerMutation.isPending}>
          {registerMutation.isPending
            ? t('settings.assistant.register.registering')
            : t('settings.assistant.register.submit')}
        </Button>
      </div>
    </section>
  );
}
