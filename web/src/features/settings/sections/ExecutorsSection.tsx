/**
 * ExecutorsSection — connect a CUSTOM agentic CLI to HappyRanch as an executor
 * profile, using the shared, chrome-free connect flow (THR-107 S2).
 *
 * This REPLACES the former THR-052 surface — a legacy org-scoped token mint
 * plus static CLI-registration and daemon-config-entry snippets that pointed at
 * the removed per-org executor-profile store (THR-107). The custom flow now
 * mints a machine-global RUNTIME token
 * (POST /auth/registration-token/runtime) and the copy-paste prompt targets
 * POST /executors/runtime/register (profile) — the ratified onboarding
 * contract, consumed identically here with no logic/contract fork. The
 * connected state comes ONLY from the GET /health/prereqs poll inside the
 * shared hook — no invented status (honesty fence, THR-061 §D).
 *
 * Built-in CLIs (claude/codex/opencode/pi) are connected from the "CLI binary
 * paths" section above; converging that section onto the shared prompt flow is
 * THR-107 S3, not this slice — so this section is custom-only (no built-in
 * toggle). The shared CustomConnect already blocks names colliding with the
 * built-ins (BUILTINS / NAME_RE); that guard is preserved verbatim.
 */
import { useState } from 'react';
import { ConnectedCard, CustomConnect } from '@/shared/connect/ConnectFlow';
import type { Connected, ConnectMode } from '@/shared/connect/useRuntimeConnect';

/** Connected-card subtitle for the Settings mount. The onboarding
 *  "manage your CLIs anytime from Settings" clause is dropped — it is circular
 *  on the Settings surface itself. */
function connectedSubtitle(_via: ConnectMode): string {
  return 'Your custom CLI is registered and available to every org.';
}

export function ExecutorsSection(): JSX.Element {
  const [connected, setConnected] = useState<Connected | null>(null);

  return (
    <section className="space-y-6">
      {connected ? (
        <ConnectedCard
          connected={connected}
          subtitle={connectedSubtitle}
          onReset={() => setConnected(null)}
        />
      ) : (
        <CustomConnect onConnected={setConnected} />
      )}

      {/* Read-only notice — always visible at the bottom */}
      <div className="bg-surface border-border-default shadow-pasture-sm rounded-lg border p-4">
        <h3 className="text-text-primary text-sm font-medium">
          Per-agent executor assignment
        </h3>
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
