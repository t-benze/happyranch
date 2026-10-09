import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { useReadAgentSystemPrompt, useSetAgentSystemPrompt } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import type { AgentSummary } from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';
import { classifySystemPromptError } from './strings';

interface Props {
  slug: string;
  agentName: string;
  agent: AgentSummary | undefined;
  loading: boolean;
  failed: boolean;
  empty: boolean;
}

const validRevision = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

/** Identity is keyed by org/target in both consumers; locale never remounts it. */
export function SystemPromptEditor({ slug, agentName, agent, loading, failed, empty }: Props): JSX.Element {
  const { t } = useTranslation();
  const save = useSetAgentSystemPrompt();
  const read = useReadAgentSystemPrompt();
  const [expanded, setExpanded] = useState(false);
  const [base, setBase] = useState<{ system_prompt: string; revision: string } | null>(null);
  const [draft, setDraft] = useState('');
  const [snapshot, setSnapshot] = useState<AgentSummary>();
  const [readUnavailable, setReadUnavailable] = useState(false);
  const [pending, setPending] = useState(false);
  const [needsRead, setNeedsRead] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const mounted = useRef(true);
  const busy = useRef(false);
  const inspectionRequired = useRef(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const previousRevision = useRef(agent?.revision);
  const wasEditing = useRef(false);
  const editing = base !== null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (previousRevision.current !== agent?.revision) {
      previousRevision.current = agent?.revision;
      setSnapshot(undefined);
    }
  }, [agent?.revision]);
  useEffect(() => {
    if (editing) textarea.current?.focus();
    else if (wasEditing.current) editButton.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

  const visible = snapshot ?? agent;
  const available = !!slug && !readUnavailable && (!!snapshot || (!loading && !failed)) && !!visible && validRevision(visible.revision);
  const canSave = !!base && !pending && !needsRead && available && draft.trim() !== '' && draft !== base.system_prompt;

  const beginEdit = () => {
    if (!available || !visible || !validRevision(visible.revision)) return;
    setBase({ system_prompt: visible.system_prompt, revision: visible.revision });
    inspectionRequired.current = false;
    setDraft(visible.system_prompt);
    setExpanded(true); setError(null); setDiagnostic(null); setSaved(false); setNeedsRead(false);
  };
  const cancel = () => {
    if (busy.current) return;
    setBase(null); setDraft(''); setError(null); setDiagnostic(null); setNeedsRead(false);
  };
  const submit = async () => {
    if (!canSave || !base || busy.current || inspectionRequired.current) return;
    // Freeze body, base and URL identity before either network operation.
    const submission = { slug, agentName, body: { system_prompt: draft, expected_revision: base.revision } };
    busy.current = true; inspectionRequired.current = true;
    setPending(true); setError(null); setDiagnostic(null); setSaved(false);
    try {
      const receipt = await save.mutateAsync(submission);
      if (!mounted.current) return;
      const fresh = await read.mutateAsync({ slug: submission.slug, agentName: submission.agentName });
      if (!mounted.current) return;
      if (!fresh || receipt.agent !== submission.agentName || !validRevision(receipt.revision)
          || fresh.system_prompt !== receipt.system_prompt || fresh.revision !== receipt.revision) {
        if (!fresh || !validRevision(fresh.revision)) setReadUnavailable(true);
        setError('agents.prompt.verifyFailed'); setNeedsRead(true); return;
      }
      setSnapshot(fresh); setBase(null); setDraft(''); setSaved(true);
    } catch (err: unknown) {
      if (mounted.current) {
        setError(classifySystemPromptError(err)); setNeedsRead(true);
        const detail = (err as { detail?: unknown } | null)?.detail;
        const text = typeof detail === 'string' ? detail : (detail as { error?: unknown } | null)?.error;
        setDiagnostic(typeof text === 'string' ? text : null);
      }
    } finally {
      if (mounted.current) { busy.current = false; setPending(false); }
    }
  };
  const reload = async () => {
    if (busy.current) return;
    busy.current = true; setPending(true); setDiagnostic(null); setSaved(false);
    try {
      const fresh = await read.mutateAsync({ slug, agentName });
      if (!mounted.current) return;
      if (!fresh || !validRevision(fresh.revision)) {
        setReadUnavailable(true); setError('agents.prompt.unavailable'); setNeedsRead(true); return;
      }
      setSnapshot(fresh); setReadUnavailable(false); inspectionRequired.current = false;
      // Explicit inspection/rebase preserves the authored draft. Background
      // polling can never grant it a different revision.
      if (base) setBase({ system_prompt: fresh.system_prompt, revision: fresh.revision });
      setNeedsRead(false); setError(base ? 'agents.prompt.rebased' : null);
    } catch (err: unknown) {
      if (mounted.current) { setError(classifySystemPromptError(err)); setNeedsRead(true); }
    } finally {
      if (mounted.current) { busy.current = false; setPending(false); }
    }
  };
  const initial: MessageKey | null = snapshot ? null : readUnavailable ? 'agents.prompt.unavailable'
    : loading ? 'agents.prompt.loading' : failed ? 'agents.prompt.loadFailed'
    : !visible ? (empty ? 'agents.prompt.empty' : 'agents.prompt.disappeared')
    : !validRevision(visible.revision) ? 'agents.prompt.unavailable' : null;

  return <section className="bg-surface border-border-default shadow-pasture-sm min-w-0 rounded-lg border"
    onKeyDown={(event) => {
      if (base && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault(); event.stopPropagation(); void submit();
      }
    }}>
    <div className="flex items-center justify-between gap-2 px-4 py-3">
      <button type="button" aria-expanded={expanded} disabled={editing} onClick={() => setExpanded(!expanded)}
        className="text-text-secondary flex items-center gap-2 text-xs font-medium">
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{t('agents.field.systemPrompt')}
      </button>
      {!base && <Button ref={editButton} size="sm" variant="ghost" disabled={!available || pending} onClick={beginEdit}>
        {t('agents.prompt.edit')}
      </Button>}
    </div>
    {initial && <p className="text-text-muted px-4 pb-3 text-xs">{t(initial)}</p>}
    {(initial || error) && <div className="px-4 pb-3">
      <Button size="sm" variant="ghost" disabled={pending || !slug} onClick={() => void reload()}>{t('agents.prompt.reload')}</Button>
    </div>}
    {expanded && <div className="border-border-default border-t p-4">
      {base ? <>
        <textarea ref={textarea} aria-label={t('agents.field.systemPrompt')} value={draft} disabled={pending}
          onChange={(event) => setDraft(event.target.value)} rows={12}
          className="bg-surface-sunken border-border-subtle w-full min-w-0 resize-y rounded-md border p-3 font-mono text-xs" />
        {snapshot && error === 'agents.prompt.rebased' && <pre className="mt-2 max-h-40 overflow-auto text-xs whitespace-pre-wrap">{snapshot.system_prompt}</pre>}
        <div className="bg-surface sticky bottom-0 flex flex-wrap justify-end gap-2 py-2">
          <Button variant="ghost" size="sm" disabled={pending} onClick={cancel}>{t('common.cancel')}</Button>
          <Button size="sm" disabled={!canSave} onClick={() => void submit()}>{pending ? t('agents.prompt.saving') : t('agents.prompt.save')}</Button>
        </div>
      </> : visible && <pre className="bg-surface-sunken border-border-subtle max-h-48 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">{visible.system_prompt}</pre>}
    </div>}
    {error && <p role="alert" className="text-tier-red break-words px-4 pb-3 text-xs">{t(error)}{diagnostic && <span className="mt-1 block whitespace-pre-wrap">{diagnostic}</span>}</p>}
    {saved && <p role="status" className="text-text-secondary px-4 pb-3 text-xs">{t('agents.prompt.saved')}</p>}
  </section>;
}
