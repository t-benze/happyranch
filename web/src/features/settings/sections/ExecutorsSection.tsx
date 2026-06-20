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
      <div className="border-border bg-bg-subtle rounded-md border p-4">
        <h3 className="text-sm font-medium">Executor configuration</h3>
        <p className="text-fg-muted mt-1 text-sm">
          Executor paths and settings are configured in the daemon config file
          (<code className="text-fg-subtle bg-bg-raised rounded px-1 text-xs">~/.happyranch/config.yaml</code>).
          Changes require a daemon restart.
        </p>
      </div>

      <div className="border-border bg-bg-subtle rounded-md border p-4">
        <h3 className="text-sm font-medium">Per-agent executor assignment</h3>
        <p className="text-fg-muted mt-1 text-sm">
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
