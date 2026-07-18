/**
 * ConnectRuntimeStep — THR-088 onboarding Step 1 of 2: "Connect your agentic
 * CLI". The connect flow itself (mode toggle → scoped-token mint → copy-paste
 * prompt → live GET /health/prereqs poll → connected card, for BOTH built-in
 * binary-path and custom profile executors) now lives in the shared, chrome-
 * free <ConnectFlow> (web/src/shared/connect/), consumed identically by
 * Settings ▸ Executors (THR-107). One implementation, no logic/contract fork.
 *
 * This file is the ONBOARDING WRAPPER: it supplies the onboarding-only chrome
 * the shared module deliberately excludes — the step eyebrow ("Step 1 of 2"),
 * the wizard heading, the Continue/Skip navigation, and the
 * "manage from Settings" connected-card copy. See the shared module's header
 * for the honesty-fence rationale (scoped tokens only, no invented status, the
 * connected card shows only register-real data — THR-061 §D; THR-088).
 */
import { ArrowRight } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import { ConnectFlow } from '@/shared/connect/ConnectFlow';
import type { ConnectMode } from '@/shared/connect/useRuntimeConnect';

/** Connected-card subtitle — onboarding copy, keyed on the originating flow.
 *  Kept here (not in the shared module) because the "manage from Settings"
 *  clause is onboarding chrome — it is redundant/circular on the Settings
 *  surface itself. */
function connectedSubtitle(via: ConnectMode): string {
  return via === 'builtin'
    ? 'Its binary path is registered on this machine — HappyRanch can launch it now. You can manage your CLIs anytime from Settings.'
    : 'Your custom CLI is registered and available to every org. You can manage your CLIs anytime from Settings.';
}

export function ConnectRuntimeStep({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}): JSX.Element {
  return (
    <ConnectFlow
      className="pt-6 sm:pt-10"
      eyebrow={<StepEyebrow />}
      formHeading={
        <h1 className="font-display text-display text-text-primary mt-3 font-medium">
          Connect your agentic CLI.
        </h1>
      }
      formSkipSlot={
        <button
          type="button"
          onClick={onSkip}
          className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
        >
          Skip — I&rsquo;ll connect a CLI later
        </button>
      }
      waitingSkipSlot={
        <button
          type="button"
          onClick={onSkip}
          className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
        >
          Skip for now
        </button>
      }
      connectedSubtitle={connectedSubtitle}
      connectedPrimaryAction={
        <Button onClick={onContinue}>
          Continue
          <ArrowRight aria-hidden="true" />
        </Button>
      }
    />
  );
}

function StepEyebrow(): JSX.Element {
  return (
    <p className="text-accent-text text-xs font-semibold tracking-wider uppercase">
      Step 1 of 2 · Connect your agentic CLI
    </p>
  );
}
