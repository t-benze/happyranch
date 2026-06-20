/**
 * UsageSection — token consumption summary in Settings page.
 *
 * Founder-directed honesty-lens relabel: "Usage", NOT "Billing" (§4.11).
 * Tokens-only — the dollar cost meter is DEFERRED/D1.
 *
 * Fetches recent token rollup via useSpendByAgent and renders a simple
 * table of per-agent token consumption.
 *
 * OMITTED (no backing field): dollar cost, model breakdown, traces,
 * per-thread churn — none of these exist on the token-usage rollup
 * returned by GET /tokens.
 */
import { useMemo } from 'react';
import { useSpendByAgent } from '@/hooks/spend';
import { EmptyState } from '@/design-system/patterns/EmptyState';


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
    return <p className="text-text-secondary text-sm">Loading usage data…</p>;
  }
  if (query.isError) {
    return <p className="text-feedback-danger text-sm">Could not load usage data.</p>;
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        title="No token activity yet"
        body="Token consumption data will appear here as agents work."
      />
    );
  }

  const total = rows.reduce((sum, r) => sum + (r.total_tokens ?? 0), 0);

  return (
    <section>
      {/* Count eyebrow */}
      <p className="text-overline text-text-secondary mb-3 tracking-wider uppercase">
        <span className="font-mono tabular-nums">{fmt(total)}</span> total tokens across{' '}
        <span className="font-mono tabular-nums">{rows.length}</span> agent{rows.length !== 1 ? 's' : ''}
      </p>

      <div className="mb-4 grid grid-cols-4 gap-3">
        <StatCard label="Total Tokens" value={fmt(total)} />
        <StatCard label="Cache Reads" value={fmt(rows.reduce((s, r) => s + (r.cache_read_tokens ?? 0), 0))} />
        <StatCard label="Sessions" value={String(rows.reduce((s, r) => s + (r.sessions ?? 0), 0))} />
        <StatCard label="Agents Active" value={String(rows.length)} />
      </div>

      <div className="border-border-default overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-surface-sunken text-text-secondary text-xs tracking-wider uppercase">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Agent</th>
              <th className="px-3 py-2 text-right font-mono tabular-nums font-medium">Tokens</th>
              <th className="px-3 py-2 text-right font-mono tabular-nums font-medium">Input</th>
              <th className="px-3 py-2 text-right font-mono tabular-nums font-medium">Output</th>
              <th className="px-3 py-2 text-right font-mono tabular-nums font-medium">Cache read</th>
              <th className="px-3 py-2 text-right font-mono tabular-nums font-medium">Sessions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.agent ?? `row-${i}`} className="border-border-subtle hover:bg-surface-hover border-t">
                <td className="text-text-primary px-3 py-2 font-medium">{r.agent ?? '—'}</td>
                <td className="text-text-primary px-3 py-2 text-right font-mono tabular-nums">{fmt(r.total_tokens)}</td>
                <td className="text-text-secondary px-3 py-2 text-right font-mono tabular-nums">{fmt(r.input_tokens)}</td>
                <td className="text-text-secondary px-3 py-2 text-right font-mono tabular-nums">{fmt(r.output_tokens)}</td>
                <td className="text-text-secondary px-3 py-2 text-right font-mono tabular-nums">{fmt(r.cache_read_tokens)}</td>
                <td className="text-text-secondary px-3 py-2 text-right font-mono tabular-nums">{r.sessions}</td>
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
    <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-3">
      <div className="text-overline text-text-secondary tracking-wider uppercase">{label}</div>
      <div className="text-text-primary mt-1 font-mono tabular-nums text-lg font-semibold">{value}</div>
    </div>
  );
}
