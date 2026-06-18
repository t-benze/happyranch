/**
 * AgentsSection — read-only agent configuration in Settings page.
 *
 * Honesty lens: no founder-facing write route exists for agent config via
 * Settings — the add/edit agent surface is at /orgs/:slug/agents.
 * This panel shows a gap notice directing the founder there.
 *
 * The "Founder handle" field follows iAC4 / §A.2: broadcast framing only,
 * no @mention routing promise.
 */
export function AgentsSection(): JSX.Element {
  return (
    <section className="space-y-4">
      <div className="border-border bg-bg-subtle rounded-md border p-4">
        <div className="flex items-start gap-3">
          <div className="flex-1">
            <h3 className="text-sm font-medium">Founder handle</h3>
            <p className="text-fg-muted mt-1 text-sm">
              The handle agents reference when they broadcast to you.
            </p>
            <div className="border-border bg-bg-raised text-fg-muted mt-2 rounded border px-3 py-1.5 text-sm">
              founder
            </div>
            <p className="text-fg-subtle mt-1 text-xs">
              This is your system identity. It cannot be changed from this
              surface.
            </p>
          </div>
        </div>
      </div>

      {/* Gap notice */}
      <div className="border-border bg-bg-subtle rounded-md border p-4">
        <h3 className="text-sm font-medium">Agent roster</h3>
        <p className="text-fg-muted mt-1 text-sm">
          Add, edit, or remove agents from the{' '}
          <a href="../agents" className="text-accent hover:underline">
            Agents page
          </a>.
          Agent configuration is not editable from Settings.
        </p>
      </div>
    </section>
  );
}
