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
 * Executor list is derived at runtime from the daemon, never hard-coded:
 * registered built-ins (health/prereqs present=true) plus all custom
 * runtime profiles. Unregistered built-ins are shown as unavailable but
 * are not selectable. On API error, Create is disabled until the
 * registered list is known (no invented fallback).
 */
import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
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
import { usePrereqs } from '@/hooks/health';
import { useRuntimeProfiles } from '@/hooks/runtime-executors';
import { useTeamsList } from '@/hooks/teams';

const NAME_RE = /^[a-z][a-z0-9_]*$/;

/** The four built-in executor profile names. */
const BUILTIN_NAMES = new Set(['claude', 'codex', 'opencode', 'pi']);

function defaultTeamForName(name: string): string {
  if (!name) return '';
  const i = name.lastIndexOf('_');
  if (i <= 0) return name;
  return name.slice(0, i);
}

type Role = 'worker' | 'manager';

/** One selectable executor option rendered in the dropdown. */
interface ExecutorOption {
  name: string;
  present: boolean;
  /** The kind — builtin, custom, or unregistered_builtin (unavailable). */
  kind: 'builtin' | 'custom' | 'unregistered_builtin';
  hint: string | null;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddAgentDialog({ open, onOpenChange }: Props): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
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
  const [serverError, setServerError] = useState<string | null>(null);

  const create = useCreateAgent();
  const prereqsQuery = usePrereqs();
  const profilesQuery = useRuntimeProfiles();

  // Derive selectable executor options from live daemon data.
  const executorOptions = useMemo<{
    selectable: ExecutorOption[];
    unavailable: ExecutorOption[];
    state: 'loading' | 'error' | 'empty' | 'ready';
  }>(() => {
    if (prereqsQuery.isLoading || profilesQuery.isLoading) {
      return { selectable: [], unavailable: [], state: 'loading' };
    }
    if (prereqsQuery.isError || profilesQuery.isError) {
      return { selectable: [], unavailable: [], state: 'error' };
    }

    const prereqs = prereqsQuery.data?.prereqs ?? [];
    const customProfiles = profilesQuery.data?.profiles ?? [];

    // Built-ins from health/prereqs: present=true → selectable, false → unavail.
    const builtins = prereqs
      .filter((p) => BUILTIN_NAMES.has(p.tool))
      .map((p) => ({
        name: p.tool,
        present: p.present,
        kind: p.present ? 'builtin' as const : 'unregistered_builtin' as const,
        hint: p.hint,
      }));

    // Custom profiles from runtime/profiles are all registered by definition.
    const customs: ExecutorOption[] = customProfiles.map((p) => ({
      name: p.name,
      present: p.present,
      kind: 'custom' as const,
      hint: null,
    }));

    const selectable = [
      ...builtins.filter((b) => b.kind === 'builtin'),
      ...customs,
    ];
    const unavailable = builtins.filter((b) => b.kind === 'unregistered_builtin');

    if (selectable.length === 0) {
      return { selectable: [], unavailable, state: 'empty' };
    }
    return { selectable, unavailable, state: 'ready' };
  }, [prereqsQuery, profilesQuery]);

  // Auto-select the first selectable executor when options first load.
  const executorInitRef = useState(false);
  if (executorOptions.state === 'ready' && !executorInitRef[0] && executorOptions.selectable.length > 0) {
    setExecutor(executorOptions.selectable[0].name);
    executorInitRef[1](true);
  }

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
  const executorOk = executorOptions.state === 'ready' && !!executor;
  const fieldsOk =
    nameOk &&
    executorOk &&
    description.trim().length > 0 &&
    systemPrompt.trim().length > 0 &&
    (role === 'worker' ? !!team && teams.length > 0 : !!newTeam);
  const canSubmit = fieldsOk && !create.isPending;

  const onSubmit = async () => {
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
      const e = err as { code?: string; message?: string };
      if (e.code === 'agent_exists') {
        setServerError(`An agent named "${name}" already exists.`);
      } else if (e.code === 'team_exists') {
        setServerError(`Team "${newTeam}" already exists.`);
      } else if (e.code === 'unknown_team') {
        setServerError(`Team "${team}" doesn't exist (was it removed?).`);
      } else {
        setServerError(e.message ?? 'Could not create agent.');
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New agent</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="agent-name">Name</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="e.g. alpha_worker_1"
              autoFocus
            />
            <p className="text-fg-muted text-xs">Lowercase + digits + underscores; must start with a letter.</p>
          </div>

          <fieldset>
            <legend className="text-sm font-medium">Role</legend>
            <label className="mr-4 inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="worker"
                checked={role === 'worker'}
                onChange={() => onRoleChange('worker')}
              />
              Worker
            </label>
            <label className="inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="manager"
                checked={role === 'manager'}
                onChange={() => onRoleChange('manager')}
              />
              Manager
            </label>
          </fieldset>

          {role === 'worker' ? (
            teams.length === 0 ? (
              <p className="text-fg-muted text-sm">
                No teams yet. Add a manager to create the first team.
              </p>
            ) : (
              <div>
                <Label htmlFor="agent-team">Team</Label>
                <select
                  id="agent-team"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                  className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
                >
                  <option value="">Select team…</option>
                  {teams.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            )
          ) : (
            <div>
              <Label htmlFor="agent-new-team">New team name</Label>
              <Input
                id="agent-new-team"
                value={newTeam}
                onChange={(e) => onNewTeamChange(e.target.value)}
                placeholder="defaults from name"
              />
            </div>
          )}

          <div>
            <Label htmlFor="agent-executor">Executor</Label>
            {executorOptions.state === 'loading' ? (
              <p className="text-fg-muted text-sm">Loading executor list…</p>
            ) : executorOptions.state === 'error' ? (
              <p className="text-tier-red text-sm">
                Could not load the executor list. Create is disabled.
              </p>
            ) : executorOptions.state === 'empty' ? (
              <p className="text-fg-muted text-sm">
                No executors are registered on this machine.
                {executorOptions.unavailable.length > 0 && (
                  <>
                    {' '}
                    The following built-ins are not registered:{' '}
                    {executorOptions.unavailable.map((e) => e.name).join(', ')}.
                  </>
                )}
                {' '}Register one via Settings → Executors, then reopen this dialog.
              </p>
            ) : (
              <select
                id="agent-executor"
                value={executor}
                onChange={(e) => setExecutor(e.target.value)}
                className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
              >
                {executorOptions.selectable.map((opt) => (
                  <option key={opt.name} value={opt.name}>
                    {opt.name}
                    {opt.kind === 'custom' ? ' (custom)' : ''}
                  </option>
                ))}
                {executorOptions.unavailable.length > 0 && (
                  <>
                    <option disabled>── unregistered ──</option>
                    {executorOptions.unavailable.map((opt) => (
                      <option key={opt.name} value={opt.name} disabled>
                        {opt.name} (not registered)
                      </option>
                    ))}
                  </>
                )}
              </select>
            )}
          </div>

          <div>
            <Label htmlFor="agent-description">Description</Label>
            <Input
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="agent-system-prompt">System prompt</Label>
            <Textarea
              id="agent-system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
            />
          </div>

          {serverError && (
            <p className="text-tier-red text-sm">{serverError}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit} onClick={onSubmit}>
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
