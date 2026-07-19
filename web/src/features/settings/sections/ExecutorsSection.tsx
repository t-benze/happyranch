/**
 * ExecutorsSection — the Settings ▸ Executors MANAGEMENT surface (THR-107 S3).
 *
 * Registered-list-first (Step-0 §6, founder-ratified). The panel leads with the
 * registered binary list (ExecutorBinariesSection — per-kind path + validity),
 * which is the "what's already registered" management view onboarding lacks.
 * Below it a SINGLE "Connect a CLI" entry opens the shared, chrome-free connect
 * flow INLINE on the same panel — mode toggle (built-in ↔ custom) → scoped-token
 * mint → copy-paste prompt → live GET /health/prereqs poll → connected card —
 * then collapses back to the refreshed list. Inline (not modal): the WaitingBody
 * is tall and the user leaves the browser to paste into a terminal, so the poll
 * must stay mounted (Step-0 §6-3).
 *
 * ONE implementation, no fork: the built-in/custom split is a parameter of the
 * shared ConnectFlow (S1), not a second code path. Built-in connect mints a
 * RUNTIME binary token → POST /executors/runtime/register-binary; custom mints a
 * RUNTIME profile token → POST /executors/runtime/register. Connected state
 * comes ONLY from the /health/prereqs poll — no invented status (honesty fence,
 * THR-061 §D). The shared CustomConnect keeps the BUILTINS / NAME_RE
 * name-collision guard verbatim.
 *
 * Manual absolute-path entry is NOT removed — it is DEMOTED to a per-row
 * "Advanced: enter path manually" disclosure inside ExecutorBinariesSection (a
 * genuine management convenience: fast re-point of a moved binary; Step-0 §6-4).
 *
 * OUT OF SCOPE for S3 (Step-0 Q1): there is no backend route to list/remove
 * custom profiles today — that lands in S4. This surface does NOT synthesise a
 * throwaway custom-profiles list.
 */
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Plug } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ConnectFlow } from '@/shared/connect/ConnectFlow';
import type { ConnectMode } from '@/shared/connect/useRuntimeConnect';
import { ExecutorBinariesSection } from './ExecutorBinariesSection';
import { CustomProfilesSection } from './CustomProfilesSection';

/** Connected-card subtitle for the Settings mount, keyed on the originating
 *  mode. The onboarding "manage your CLIs anytime from Settings" clause is
 *  dropped — it is circular on the Settings surface itself. */
function connectedSubtitle(via: ConnectMode): string {
  return via === 'custom'
    ? 'Your custom CLI is registered and available to every org.'
    : 'This CLI is registered — the daemon can now launch agents on it.';
}

export function ExecutorsSection(): JSX.Element {
  const [connecting, setConnecting] = useState(false);
  const qc = useQueryClient();

  /** Collapse back to the list. Invalidate the binary registry so a built-in
   *  connect (which registers via the runtime route, bypassing the
   *  register-binary mutation's own invalidation) is reflected immediately —
   *  the "refreshed list on connect" the design calls for. */
  const backToList = (): void => {
    void qc.invalidateQueries({ queryKey: ['executor-binaries'] });
    setConnecting(false);
  };

  return (
    <section className="space-y-6" data-testid="executors-section">
      {connecting ? (
        <div data-testid="executors-connect">
        <ConnectFlow
          connectedSubtitle={connectedSubtitle}
          formHeading={
            <div className="mb-1">
              <button
                type="button"
                onClick={backToList}
                className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs"
              >
                <ArrowLeft aria-hidden="true" size={14} />
                Back to executors
              </button>
              <h3 className="font-display text-text-primary mt-3 text-base font-semibold">
                Connect a CLI
              </h3>
            </div>
          }
          connectedPrimaryAction={
            <Button type="button" onClick={backToList}>
              Done
            </Button>
          }
        />
        </div>
      ) : (
        <div className="space-y-6" data-testid="executors-list">
          {/* Registered list — the primary management view (Step-0 §6-1). */}
          <ExecutorBinariesSection />

          {/* Custom CLIs connected via the shared custom flow — list + remove
              (THR-107 S4b), consuming the S4a list/remove backend. */}
          <CustomProfilesSection />

          {/* The single "Connect a CLI" entry into the shared connect flow. */}
          <Button
            type="button"
            variant="secondary"
            onClick={() => setConnecting(true)}
            data-testid="connect-a-cli"
          >
            <Plug aria-hidden="true" size={16} />
            Connect a CLI
          </Button>
        </div>
      )}

      {/* Read-only notice — always visible at the bottom, verbatim (Step-0 §6-5). */}
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
