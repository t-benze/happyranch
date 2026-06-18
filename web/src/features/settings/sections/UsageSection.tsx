/**
 * UsageSection — token consumption summary in Settings page.
 *
 * Founder-directed honesty-lens relabel: "Usage", NOT "Billing" (§4.11).
 * Tokens-only — the dollar cost meter is DEFERRED/D1.
 *
 * Fetches recent token rollup via useSpendByAgent and renders a simple
 * table of per-agent token consumption.
 */
import { useMemo } from 'react';
import { useSpendByAgent } from '@/hooks/spend';


function fmt(n: number | undefined | null): string {
  if (n == null || n === 0) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function UsageSection(): JSX.Element {
  const query = useSpendByAgent({});

  const rows = useMemo(() => {
    const data = query.data ?? [];
    return [...data].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0));
  }, [query.data]);

  if (query.isLoading) {
    return <p className="text-fg-muted text-sm">Loading usage data…</p>;
  }
  if (query.isError) {
    return <p className="text-tier-red text-sm">Could not load usage data.</p>;
  }

  if (rows.length === 0) {
    return (
      <div className="border-border bg-bg-subtle rounded-md border p-4">
        <p className="text-fg-muted text-sm">No token activity yet.</p>
      </div>
    );
  }

  const total = rows.reduce((sum, r) => sum + (r.total_tokens ?? 0), 0);

  return (
    <section>
      <div className="mb-4 grid grid-cols-4 gap-3">
        <StatCard label="Total Tokens" value={fmt(total)} />
        <StatCard label="Cache Reads" value={fmt(rows.reduce((s, r) => s + (r.cache_read_tokens ?? 0), 0))} />
        <StatCard label="Sessions" value={String(rows.reduce((s, r) => s + (r.sessions ?? 0), 0))} />
        <StatCard label="Agents Active" value={String(rows.length)} />
      </div>

      <div className="border-border overflow-hidden rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-surface-sunken text-fg-muted text-xs tracking-wider uppercase">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Agent</th>
              <th className="px-3 py-2 text-right font-medium">Tokens</th>
              <th className="px-3 py-2 text-right font-medium">Input</th>
              <th className="px-3 py-2 text-right font-medium">Output</th>
              <th className="px-3 py-2 text-right font-medium">Cache read</th>
              <th className="px-3 py-2 text-right font-medium">Sessions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.agent ?? `row-${i}`} className="border-border-subtle hover:bg-surface-raised/60 border-t">
                <td className="text-fg px-3 py-2 font-medium">{r.agent ?? '—'}</td>
                <td className="text-fg px-3 py-2 text-right">{fmt(r.total_tokens)}</td>
                <td className="text-fg-muted px-3 py-2 text-right">{fmt(r.input_tokens)}</td>
                <td className="text-fg-muted px-3 py-2 text-right">{fmt(r.output_tokens)}</td>
                <td className="text-fg-muted px-3 py-2 text-right">{fmt(r.cache_read_tokens)}</td>
                <td className="text-fg-muted px-3 py-2 text-right">{r.sessions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatCard({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="border-border bg-bg-subtle rounded-md border p-3">
      <div className="text-fg-muted text-xs">{label}</div>
      <div className="text-fg mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}
