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
 */
import { useState } from 'react';
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
import { useTeamsList } from '@/hooks/teams';

const NAME_RE = /^[a-z][a-z0-9_]*$/;

function defaultTeamForName(name: string): string {
  if (!name) return '';
  const i = name.lastIndexOf('_');
  if (i <= 0) return name;
  return name.slice(0, i);
}

type Role = 'worker' | 'manager';
type Executor = 'claude' | 'codex' | 'opencode';

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
  const [executor, setExecutor] = useState<Executor>('claude');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);

  const create = useCreateAgent();

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
  const fieldsOk =
    nameOk &&
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
            <select
              id="agent-executor"
              value={executor}
              onChange={(e) => setExecutor(e.target.value as Executor)}
              className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
            >
              <option value="claude">claude</option>
              <option value="codex">codex</option>
              <option value="opencode">opencode</option>
            </select>
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
