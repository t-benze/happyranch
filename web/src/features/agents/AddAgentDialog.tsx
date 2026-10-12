/**
 * AddAgentDialog — founder creates a new agent.
 *
 * Two visible branches keyed off `role`:
 *
 *   - Worker: pick an existing team from `useTeamsList()`. If no teams,
 *     show an inline note and keep Create disabled.
 *   - Manager: type a new team name. Defaults to the agent name with
 *     the trailing `_<suffix>` stripped — auto-tracks until the user
 *     manually edits the team field.
 *
 * Submit sends exactly ONE of `team` / `new_team` based on role, so the
 * backend's role_team_mismatch guard never fires for legitimate clicks.
 *
 * Executor list is derived at runtime from the daemon via the shared
 * `useExecutorOptions` hook (consumed by both create and edit so they
 * cannot drift): registered built-ins (health/prereqs present=true) plus
 * all custom runtime profiles. Unregistered built-ins and unavailable
 * custom profiles are shown as unavailable but are not selectable.
 * On API error, Create is disabled until the registered list is known
 * (no invented fallback).
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useCreateAgent } from '@/hooks/agents';
import { useTeamsList } from '@/hooks/teams';
import { useExecutorOptions } from './useExecutorOptions';
import { useTranslation } from '@/hooks/i18n';
import { classifyAgentError, renderAgentError, type AgentErrorView } from './strings';

const NAME_RE = /^[a-z][a-z0-9_]*$/;

function defaultTeamForName(name: string): string {
  if (!name) return '';
  const i = name.lastIndexOf('_');
  if (i <= 0) return name;
  return name.slice(0, i);
}

type Role = 'worker' | 'manager';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddAgentDialog({ open, onOpenChange }: Props): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const { t, render } = useTranslation();
  const teamsQuery = useTeamsList();
  const teams = teamsQuery.data?.teams ?? [];

  const [name, setName] = useState('');
  const [role, setRole] = useState<Role>('worker');
  const [team, setTeam] = useState('');
  const [newTeam, setNewTeam] = useState('');
  const [linkedToName, setLinkedToName] = useState(true);
  const [executor, setExecutor] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  // Locale-neutral error view + the submitted values its template names, so a
  // mapped message re-translates in place on a locale switch.
  const [serverError, setServerError] = useState<
    { view: AgentErrorView; params: { name: string; team: string; newTeam: string } } | null
  >(null);

  const create = useCreateAgent();
  const executorOptions = useExecutorOptions();

  // When the selectable-name set changes (reload / refetch), keep a
  // still-valid user selection, but clear or reset when a former value
  // has disappeared from the live options.
  const selectableNames = useMemo(
    () => new Set(executorOptions.selectable.map((o) => o.name)),
    [executorOptions.selectable],
  );
  useEffect(() => {
    if (executorOptions.state !== 'ready') return;
    if (selectableNames.has(executor)) return; // still valid
    if (executorOptions.selectable.length > 0) {
      setExecutor(executorOptions.selectable[0].name);
    } else {
      setExecutor('');
    }
    // Run only when the selectable set changes; the eslint-disable is
    // intentional — adding executor to the deps would re-init on every
    // selection change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectableNames, executorOptions.state, executorOptions.selectable]);

  const onNameChange = (next: string) => {
    setName(next);
    setServerError(null);
    if (role === 'manager' && linkedToName) {
      setNewTeam(defaultTeamForName(next));
    }
  };

  const onNewTeamChange = (next: string) => {
    setNewTeam(next);
    setLinkedToName(false);
  };

  const onRoleChange = (next: Role) => {
    setRole(next);
    if (next === 'manager') {
      setLinkedToName(true);
      setNewTeam(defaultTeamForName(name));
    }
  };

  const nameOk = NAME_RE.test(name);
  const executorOk =
    executorOptions.state === 'ready' &&
    !!executor &&
    executorOptions.selectable.some((o) => o.name === executor);
  const fieldsOk =
    nameOk &&
    executorOk &&
    description.trim().length > 0 &&
    systemPrompt.trim().length > 0 &&
    (role === 'worker'
      ? !teamsQuery.isLoading && !teamsQuery.isError && teams.some((entry) => entry.name === team)
      : !!newTeam);
  const canSubmit = fieldsOk && !create.isPending;

  const onSubmit = async () => {
    if (!canSubmit) return;
    const body =
      role === 'worker'
        ? {
            name,
            role,
            team,
            executor,
            description,
            system_prompt: systemPrompt,
          }
        : {
            name,
            role,
            new_team: newTeam,
            executor,
            description,
            system_prompt: systemPrompt,
          };
    try {
      await create.mutateAsync(body);
      onOpenChange(false);
      if (slug) navigate(`/orgs/${slug}/agents/${name}`);
    } catch (err: unknown) {
      setServerError({
        view: classifyAgentError(err, 'agents.error.createFailed'),
        params: { name, team, newTeam },
      });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('agents.add.title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="agent-name">{t('agents.add.name')}</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder={t('agents.add.namePlaceholder')}
              autoFocus
            />
            <p className="text-fg-muted text-xs">{t('agents.add.nameHint')}</p>
          </div>

          <fieldset>
            <legend className="text-sm font-medium">{t('agents.add.role')}</legend>
            <label className="mr-4 inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="worker"
                checked={role === 'worker'}
                onChange={() => onRoleChange('worker')}
              />
              {t('agents.role.worker')}
            </label>
            <label className="inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="manager"
                checked={role === 'manager'}
                onChange={() => onRoleChange('manager')}
              />
              {t('agents.role.manager')}
            </label>
          </fieldset>

          {role === 'worker' ? (
            teamsQuery.isLoading ? (
              <p className="text-fg-muted text-sm" role="status">
                {t('agents.add.teamsLoading')}
              </p>
            ) : teamsQuery.isError ? (
              <div className="space-y-2" role="alert">
                <p className="text-fg-muted text-sm">{t('agents.add.teamsError')}</p>
                <Button variant="outline" size="sm" onClick={() => teamsQuery.refetch()}>
                  {t('common.retry')}
                </Button>
              </div>
            ) : teams.length === 0 ? (
              <p className="text-fg-muted text-sm">
                {t('agents.add.noTeams')}
              </p>
            ) : (
              <div>
                <Label htmlFor="agent-team">{t('agents.add.team')}</Label>
                <select
                  id="agent-team"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                  className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
                >
                  <option value="">{t('agents.add.selectTeam')}</option>
                  {teams.map((entry) => (
                    <option key={entry.name} value={entry.name}>
                      {entry.name}{entry.manager_kind === 'human' ? ` · ${t('agents.team.founderManaged')}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )
          ) : (
            <div>
              <Label htmlFor="agent-new-team">{t('agents.add.newTeam')}</Label>
              <Input
                id="agent-new-team"
                value={newTeam}
                onChange={(e) => onNewTeamChange(e.target.value)}
                placeholder={t('agents.add.newTeamPlaceholder')}
              />
            </div>
          )}

          <div>
            <Label htmlFor="agent-executor">{t('agents.executor.label')}</Label>
            {executorOptions.state === 'loading' ? (
              <p className="text-fg-muted text-sm">{t('agents.executor.loading')}</p>
            ) : executorOptions.state === 'error' ? (
              <p className="text-tier-red text-sm">
                {t('agents.add.executorError')}
              </p>
            ) : executorOptions.state === 'empty' ? (
              <div>
                <p className="text-fg-muted text-sm">
                  {t('agents.add.noExecutors')}
                  {executorOptions.unavailable.length > 0 && (
                    <>
                      {' '}
                      {t('agents.add.notLaunchable', {
                        names: executorOptions.unavailable.map((e) => e.name).join(', '),
                      })}
                    </>
                  )}
                </p>
                {slug && (
                  <p className="text-fg-muted mt-1 text-xs">
                    {render('agents.add.registerVia', {
                      link: (
                        <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
                          {t('agents.executor.settingsLink')}
                        </Link>
                      ),
                    })}
                  </p>
                )}
              </div>
            ) : (
              <select
                id="agent-executor"
                value={executor}
                onChange={(e) => setExecutor(e.target.value)}
                className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
              >
                {executorOptions.selectable.map((opt) => (
                  <option key={opt.name} value={opt.name}>
                    {opt.kind === 'custom' ? t('agents.executor.customOption', { name: opt.name }) : opt.name}
                  </option>
                ))}
                {executorOptions.unavailable.length > 0 && (
                  <>
                    <option disabled>{t('agents.executor.unavailableHeader')}</option>
                    {executorOptions.unavailable.map((opt) => (
                      <option key={opt.name} value={opt.name} disabled>
                        {opt.kind === 'custom'
                          ? t('agents.add.customUnavailableOption', { name: opt.name })
                          : t('agents.add.notRegisteredOption', { name: opt.name })}
                      </option>
                    ))}
                  </>
                )}
              </select>
            )}
            {/* Settings → Executors navigation link for unavailable executors */}
            {slug && executorOptions.state === 'ready' && executorOptions.unavailable.length > 0 && (
              <p className="text-fg-muted mt-1 text-xs">
                {t('agents.executor.needRegister')}{' '}
                <Link to={`/orgs/${slug}/settings/executors`} className="text-accent-text underline">
                  {t('agents.executor.settingsLink')}
                </Link>
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="agent-description">{t('agents.field.description')}</Label>
            <Input
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="agent-system-prompt">{t('agents.field.systemPrompt')}</Label>
            <Textarea
              id="agent-system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
            />
          </div>

          {serverError && (
            <p className="text-tier-red text-sm">
              {renderAgentError(serverError.view, t, serverError.params)}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!canSubmit} onClick={onSubmit}>
            {create.isPending ? t('agents.add.creating') : t('agents.add.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
