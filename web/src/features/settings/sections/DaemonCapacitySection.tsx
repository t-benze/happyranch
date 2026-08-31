import { useEffect, useState } from 'react';
import { useDaemonCapacity, useUpdateDaemonCapacity } from '@/hooks/settings';

export function DaemonCapacitySection(): JSX.Element {
  const query = useDaemonCapacity();
  const save = useUpdateDaemonCapacity();
  const [workers, setWorkers] = useState('');
  const [cap, setCap] = useState('');
  const [rationale, setRationale] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [status, setStatus] = useState('');
  useEffect(() => {
    if (query.data) {
      setWorkers(String(query.data.persisted_yaml.queue_workers ?? query.data.next_start.queue_workers));
      setCap(String(query.data.persisted_yaml.host_global_session_cap ?? query.data.next_start.host_global_session_cap));
    }
  }, [query.data]);
  if (query.isLoading) return <p role="status">Loading daemon capacity…</p>;
  if (query.isError || !query.data) return <p role="alert">Could not load daemon capacity. No values are displayed. {query.error?.message}</p>;
  const data = query.data;
  const shadowed = data.environment_shadowed.length > 0;
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setStatus('Saving for next restart…');
    try {
      const result = await save.mutateAsync({ revision: data.revision, queue_workers: Number(workers), host_global_session_cap: Number(cap), rationale, confirm_environment_shadow: confirmed });
      setStatus(result.message ?? 'Saved for next daemon restart. Running capacity was not changed.');
    } catch { setStatus('Save failed. Reload the latest snapshot and review the values.'); }
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
    <button type="submit" disabled={save.isPending || (shadowed && !confirmed)} className="rounded bg-accent-solid px-4 py-2 text-white">Save for next restart</button>
    <p role="status" aria-live="polite">{status}</p>
  </form>;
}
