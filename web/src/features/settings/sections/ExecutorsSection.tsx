/**
 * ExecutorsSection — read-only executor configuration in Settings page.
 *
 * Honesty lens: no founder-facing write route exists for executor config.
 * The executors are configured via system-level config (daemon config.yaml)
 * and are restart-required. This panel shows a gap notice.
 */
export function ExecutorsSection(): JSX.Element {
  return (
    <section className="space-y-4">
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <h3 className="text-sm font-medium text-text-primary">Executor configuration</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Executor paths and settings are configured in the daemon config file
          (<code className="text-text-secondary bg-surface-sunken rounded px-1 text-xs font-mono">~/.happyranch/config.yaml</code>).
          Changes require a daemon restart.
        </p>
      </div>

      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <h3 className="text-sm font-medium text-text-primary">Per-agent executor assignment</h3>
        <p className="text-text-secondary mt-1 text-sm">
          Assign executors to individual agents from the{' '}
          <a href="../agents" className="text-accent hover:underline">
            Agents page
          </a>.
          Each agent's executor (claude, codex, opencode, pi) is set during
          enrollment and cannot be changed from Settings.
        </p>
      </div>
    </section>
  );
}
