import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { AlertCircle, ShieldCheck } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ApiError } from '@/lib/api';
import {
  type AuthorityPolicyTemplate,
  useActivateTeamEscalationPolicyRelease,
  useCreateTeamEscalationPolicyRelease,
  useTeamEscalationPolicy,
} from '@/hooks/authorityPolicy';

export function TeamEscalationPolicyCard({ agent }: { agent: { name: string; team: string; role: string } }): JSX.Element {
  const query = useTeamEscalationPolicy(agent);
  const createRelease = useCreateTeamEscalationPolicyRelease();
  const activateRelease = useActivateTeamEscalationPolicyRelease();
  const [draft, setDraft] = useState<AuthorityPolicyTemplate | null>(null);
  const [baseline, setBaseline] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<'activate' | null>(null);
  const [savedInactive, setSavedInactive] = useState<{ id: string; version: number } | null>(null);

  const source = useMemo(() => query.data?.active?.release ?? query.data?.bootstrap_template, [query.data]);
  useEffect(() => {
    if (!source) return;
    const next = {
      title: source.title,
      normative_text: source.normative_text,
      clauses: source.clauses.map((clause) => ({ ...clause })),
      continuation_phrase: source.continuation_phrase,
    };
    setDraft(next);
    setBaseline(JSON.stringify(next));
    setMessage(null);
    setConfirm(null);
    setSavedInactive(null);
  }, [source]);

  if (query.isLoading) return <PolicyShell><p className="text-text-muted text-sm">Loading team policy…</p></PolicyShell>;
  if (query.isError || !query.data || !draft) {
    return <PolicyShell><div role="alert" className="text-tier-red flex items-center gap-2 text-sm"><AlertCircle size={14} />Could not load the team policy. Reload to retry.</div></PolicyShell>;
  }

  const dirty = JSON.stringify(draft) !== baseline;
  const expected = query.data.bootstrap_template;
  const validationError = validateDraft(draft, expected);
  const active = query.data.active;
  const guard = query.data.activation_guard;

  const save = async (andActivate: boolean) => {
    if (validationError) { setMessage(validationError); return; }
    setMessage(null);
    try {
      const saved = await createRelease.mutateAsync({
        agentName: agent.name,
        body: {
          ...draft,
          based_on_release_id: active?.release.id ?? null,
          request_id: crypto.randomUUID(),
        },
      });
      setBaseline(JSON.stringify(draft));
      setSavedInactive({ id: saved.release.id, version: saved.release.version });
      setMessage(`Immutable version ${saved.release.version} saved inactive.`);
      if (andActivate && guard.ready) {
        await activateRelease.mutateAsync({
          agentName: agent.name,
          body: {
            release_id: saved.release.id,
            expected_previous_epoch: active?.epoch ?? 0,
            request_id: crypto.randomUUID(),
            action: 'activate',
            acknowledge_shared_credential_attribution: true,
          },
        });
      }
    } catch (error) {
      const api = error instanceof ApiError ? error : null;
      if (api?.code === 'base_release_changed' || api?.status === 409) {
        setMessage('The active base changed. Your draft was preserved; reload before saving again.');
      } else if (api?.status === 422) {
        setMessage('The server rejected this policy contract. Review the highlighted canonical fields.');
      } else {
        setMessage('The policy could not be saved. Your draft was preserved; try again.');
      }
    }
  };

  return (
    <PolicyShell>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-text-primary text-base font-medium">Team escalation policy</h3>
          <p className="text-text-muted mt-1 text-xs">Owned by the Engineering team, not by this agent.</p>
        </div>
        <span className="bg-accent-soft text-accent-text rounded-full px-2 py-1 text-xs">Team-owned</span>
      </div>
      <div className="bg-tier-amber-soft text-text-secondary mt-3 rounded-md p-3 text-xs">
        Changes are attributed only to the <strong>shared local operator credential</strong>. Individual operator identity is not available.
      </div>
      {active ? <p className="text-text-muted mt-3 text-xs">Active v{active.release.version} · epoch {active.epoch} · {active.release.digest.slice(0, 12)}</p> : <p className="text-text-muted mt-3 text-xs">No active release. Start from the canonical server bootstrap template.</p>}
      {savedInactive && <p className="text-accent-text mt-1 text-xs">Saved inactive v{savedInactive.version} · {savedInactive.id}</p>}
      <label className="text-text-secondary mt-4 block text-xs font-medium">Title
        <input className="border-border-subtle bg-surface mt-1 w-full rounded-md border px-3 py-2 text-sm" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      </label>
      <label className="text-text-secondary mt-3 block text-xs font-medium">Normative policy
        <textarea className="border-border-subtle bg-surface mt-1 min-h-40 w-full rounded-md border px-3 py-2 font-mono text-xs" value={draft.normative_text} onChange={(e) => setDraft({ ...draft, normative_text: e.target.value })} />
      </label>
      <div className="mt-3 space-y-2">
        {draft.clauses.map((clause, index) => (
          <div key={clause.id} className="border-border-subtle bg-surface-sunken rounded-md border p-3">
            <div className="text-text-muted flex flex-wrap gap-2 font-mono text-xs"><span>{clause.id}</span><span>· {clause.category}</span><span>· {clause.action}</span></div>
            <textarea aria-label={`${clause.id} condition`} className="border-border-subtle bg-surface mt-2 min-h-20 w-full rounded border px-2 py-1 text-xs" value={clause.condition} onChange={(e) => setDraft({ ...draft, clauses: draft.clauses.map((item, i) => i === index ? { ...item, condition: e.target.value } : item) })} />
          </div>
        ))}
      </div>
      <label className="text-text-secondary mt-3 block text-xs font-medium">Canonical continuation phrase
        <input readOnly className="border-border-subtle bg-surface-sunken mt-1 w-full rounded-md border px-3 py-2 font-mono text-xs" value={draft.continuation_phrase} />
      </label>
      {(validationError || message) && <p role="status" className={`mt-3 text-xs ${validationError ? 'text-tier-red' : 'text-text-secondary'}`}>{validationError ?? message}</p>}
      {!guard.ready && <div className="text-tier-amber mt-3 flex items-center gap-2 text-xs"><ShieldCheck size={14} />Activation unavailable: {guard.reason}</div>}
      {confirm && <div role="dialog" aria-label="activate policy confirmation" className="border-border-default mt-3 rounded-md border p-3 text-sm"><p>Save a new immutable version and activate it?</p><div className="mt-2 flex gap-2"><Button size="sm" onClick={() => { setConfirm(null); void save(true); }}>Confirm</Button><Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button></div></div>}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" disabled={!dirty || !!validationError || createRelease.isPending} onClick={() => void save(false)}>Save immutable version</Button>
        <Button size="sm" disabled={!dirty || !!validationError || !guard.ready || createRelease.isPending} onClick={() => setConfirm('activate')}>Save &amp; activate</Button>
        {active && <Button size="sm" variant="ghost" disabled title="Rollback selection and history arrive in S6">Reactivate older version</Button>}
        {message?.includes('base changed') && <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>Reload base</Button>}
      </div>
    </PolicyShell>
  );
}

function validateDraft(draft: AuthorityPolicyTemplate, expected: AuthorityPolicyTemplate): string | null {
  if (!draft.title.trim() || !draft.normative_text.trim()) return 'Title and normative policy are required.';
  if (draft.continuation_phrase !== expected.continuation_phrase) return 'The canonical continuation phrase cannot be changed.';
  if (draft.clauses.length !== expected.clauses.length) return 'Every canonical clause is required.';
  for (let index = 0; index < expected.clauses.length; index += 1) {
    const actual = draft.clauses[index]; const canonical = expected.clauses[index];
    if (!actual || actual.id !== canonical.id || actual.category !== canonical.category || actual.action !== canonical.action) return 'Clause ids, order, categories, and actions are server-controlled.';
    if (!actual.condition.trim()) return `Clause ${actual.id} needs a condition.`;
  }
  return null;
}

function PolicyShell({ children }: { children: ReactNode }): JSX.Element {
  return <section data-testid="team-escalation-policy" className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">{children}</section>;
}
