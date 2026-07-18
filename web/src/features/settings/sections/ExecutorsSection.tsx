/**
 * ExecutorsSection — Settings ▸ Executors ADD action (THR-107 S2).
 *
 * The stale THR-052 registration-token generator (hand-typed command / argv /
 * adapter → legacy POST /auth/registration-token → two static <pre> blocks: a
 * conformance prompt that told the user to run the removed
 * `happyranch executors register` CLI, and an org/config.yaml snippet) is
 * REPLACED here by the SAME shared, chrome-free connect flow the onboarding step
 * uses (web/src/shared/connect/ConnectFlow). One implementation, no fork:
 * mode toggle (built-in binary vs custom profile) → scoped runtime-token mint →
 * copy-paste connect prompt → live GET /health/prereqs poll → connected card.
 *
 * Capability note (confusion-protocol, THR-107 S2): the old form's command /
 * argv_template / adapter were never sent to the mint — they only rendered the
 * legacy CLI string + config.yaml snippet, both now deleted. In the ratified
 * flow the candidate CLI self-describes those same fields during conformance
 * (POST /executors/runtime/register), so nothing is dropped — the capture moves
 * from founder-typed to CLI-reported.
 *
 * Onboarding chrome (step eyebrow, wizard headings, Continue/Skip) is injected
 * by the onboarding wrapper via ConnectFlow slots and does NOT render here —
 * Settings has no wizard navigation; its panel supplies the section heading.
 *
 * Scope (S2 = ADD action only): the registered-list-first management layout and
 * built-in convergence are S3; custom-profile list/remove is S4 (needs a backend
 * route). ExecutorBinariesSection is untouched.
 */
import { ConnectFlow } from '@/shared/connect/ConnectFlow';
import type { ConnectMode } from '@/shared/connect/useRuntimeConnect';

/** Connected-card subtitle for the Settings surface. Unlike onboarding it omits
 *  the "manage from Settings" clause — circular here, since this IS Settings. */
function connectedSubtitle(via: ConnectMode): string {
  return via === 'builtin'
    ? 'Its binary path is registered on this machine — HappyRanch can launch it now.'
    : 'Your custom CLI is registered and available to every org.';
}

export function ExecutorsSection(): JSX.Element {
  return (
    <section className="space-y-6" data-testid="executors-connect">
      <ConnectFlow connectedSubtitle={connectedSubtitle} />

      {/* Read-only notice — orthogonal to the connect flow, preserved verbatim. */}
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
