import { useEffect, useRef, useState } from 'react';
import { useDaemonCapacity, useUpdateDaemonCapacity } from '@/hooks/settings';
import { ApiError } from '@/lib/api';

function safeSaveError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Save failed safely. The draft is unchanged; retry or reload.';
  if (error.status === 401 || error.status === 403) return 'Unauthorized. A valid daemon bearer is required; no values were changed.';
  if (error.code === 'stale_revision') return 'This configuration changed elsewhere. Review the latest safe snapshot below; your draft is preserved.';
  if (error.code === 'environment_confirmation_required') return 'Confirm the environment-shadow warning before saving.';
  if (error.status === 422) return 'The server rejected these values. Review both positive integers and the rationale.';
  if (error.code === 'audit_failed') return 'Audit storage is unavailable, so the configuration was not changed.';
  if (error.code === 'config_write_failed') {
    const detail = error.detail as { artifact_state?: 'absent' | 'present' | 'unknown' };
    const artifact = detail?.artifact_state === 'present'
      ? ' A temporary artifact remains; inspect it before cleanup.'
      : detail?.artifact_state === 'unknown'
        ? ' Temporary artifact state is unknown; inspect it before cleanup.'
        : '';
    return `Configuration storage failed safely. The previous authoritative file remains in use.${artifact}`;
  }
  if (error.code === 'config_publication_uncertain') {
    const detail = error.detail as { artifact_state?: 'absent' | 'present' | 'unknown' };
    const artifact = detail?.artifact_state === 'present'
      ? ' A temporary artifact remains; inspect it before cleanup.'
      : detail?.artifact_state === 'unknown'
        ? ' Temporary artifact state is unknown; inspect it before cleanup.'
        : '';
    return `The new configuration was published, but durability, verification, or cleanup did not complete. Your draft is preserved. Reload and inspect the authoritative values before retrying.${artifact}`;
  }
  return 'Save failed safely. The draft is unchanged; no live capacity was changed.';
}

export function DaemonCapacitySection(): JSX.Element {
  const query = useDaemonCapacity();
  const save = useUpdateDaemonCapacity();
  const [workers, setWorkers] = useState('');
  const [cap, setCap] = useState('');
  const [rationale, setRationale] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [status, setStatus] = useState('');
  const [latestConflict, setLatestConflict] = useState<string | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (query.data) {
      setWorkers(String(query.data.persisted_yaml.queue_workers ?? query.data.next_start.queue_workers));
      setCap(String(query.data.persisted_yaml.host_global_session_cap ?? query.data.next_start.host_global_session_cap));
    }
  }, [query.data]);
  const initialWorkers = query.data ? String(query.data.persisted_yaml.queue_workers ?? query.data.next_start.queue_workers) : '';
  const initialCap = query.data ? String(query.data.persisted_yaml.host_global_session_cap ?? query.data.next_start.host_global_session_cap) : '';
  const dirty = Boolean(query.data) && (workers !== initialWorkers || cap !== initialCap || rationale.length > 0);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  if (query.isLoading) return <p role="status">Loading daemon capacity…</p>;
  if (query.isError || !query.data) return <p role="alert">Could not load daemon capacity. No values are displayed. {query.error?.message}</p>;
  const data = query.data;
  const shadowed = data.environment_shadowed.length > 0;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLatestConflict(null);
    const workerValue = Number(workers);
    const capValue = Number(cap);
    if (!Number.isInteger(workerValue) || workerValue <= 0 || !Number.isInteger(capValue) || capValue <= 0 || !rationale.trim()) {
      setStatus('Enter positive whole numbers for both fields and a non-blank rationale.');
      queueMicrotask(() => statusRef.current?.focus());
      return;
    }
    setStatus('Saving for next restart…');
    try {
      const result = await save.mutateAsync({ revision: data.revision, queue_workers: workerValue, host_global_session_cap: capValue, rationale: rationale.trim(), confirm_environment_shadow: confirmed });
      setStatus(result.message ?? 'Saved for next daemon restart. Running capacity was not changed.');
      setRationale('');
    } catch (error) {
      setStatus(safeSaveError(error));
      if (error instanceof ApiError && error.code === 'stale_revision') {
        const detail = error.detail as { latest?: { revision?: string; persisted_yaml?: { queue_workers?: number | null; host_global_session_cap?: number | null } } };
        const latest = detail?.latest;
        if (latest) setLatestConflict(`Latest revision ${latest.revision ?? 'unknown'}: ${latest.persisted_yaml?.queue_workers ?? 'not set'} / ${latest.persisted_yaml?.host_global_session_cap ?? 'not set'}.`);
      }
      queueMicrotask(() => statusRef.current?.focus());
    }
  }
  return <form onSubmit={submit} className="space-y-5">
    <div role="alert" className="rounded-md border p-3 text-sm"><strong>Daemon bearer required.</strong> This bearer-based authorization cannot be attributed to a verified person. This resource affects every org.</div>
    <dl className="grid grid-cols-2 gap-2 text-sm">
      <dt>Running at daemon start</dt><dd>{data.running_at_daemon_start.queue_workers} workers / cap {data.running_at_daemon_start.host_global_session_cap}</dd>
      <dt>Persisted YAML</dt><dd>{data.persisted_yaml.queue_workers ?? 'Not set'} / {data.persisted_yaml.host_global_session_cap ?? 'Not set'}</dd>
      <dt>Next-start resolution</dt><dd>{data.next_start.queue_workers} / {data.next_start.host_global_session_cap}</dd>
      <dt>Admission/capability</dt><dd>{data.effective_admission_reason}</dd>
      <dt>Revision</dt><dd className="break-all">{data.revision}</dd>
    </dl>
    {data.restart_pending && <p role="status">Restart required: a persisted next-start value differs from the running startup snapshot. Saving never applies live and this page cannot restart the daemon.</p>}
    {shadowed && <div role="alert"><p>{data.environment_warning}</p><label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I understand restart alone will not make YAML win.</label></div>}
    <label className="block">Concurrent task sessions<input aria-label="Concurrent task sessions" type="number" min="1" required value={workers} onChange={e => setWorkers(e.target.value)} className="block w-full border p-2" /></label>
    <p className="text-sm">{data.guidance.queue_workers} Guidance only; it is not an enforced aggregate-host range.</p>
    <label className="block">Host global session cap<input aria-label="Host global session cap" type="number" min="1" required value={cap} onChange={e => setCap(e.target.value)} className="block w-full border p-2" /></label>
    <p className="text-sm">{data.guidance.host_global_session_cap} There is no aggregate happyranch.slice policy.</p>
    <label className="block">Rationale<textarea required value={rationale} onChange={e => setRationale(e.target.value)} className="block w-full border p-2" /></label>
    {dirty && <p role="status">Unsaved changes. Leaving or reloading will discard this draft.</p>}
    <button type="submit" disabled={save.isPending || (shadowed && !confirmed)} className="rounded bg-accent-solid px-4 py-2 text-white">{save.isPending ? 'Saving…' : 'Save for next restart'}</button>
    <p ref={statusRef} tabIndex={-1} role="status" aria-live="assertive">{status}</p>
    {latestConflict && <p role="alert">{latestConflict}</p>}
  </form>;
}
