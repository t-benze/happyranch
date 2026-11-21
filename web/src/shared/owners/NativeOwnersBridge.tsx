/**
 * Desktop owner summary + lifecycle gate wiring (THR-387 S6-W2b). Rendered
 * once by `OwnerGateProvider`; a no-op in browser mode (no native bridge).
 *
 * Order (bridge spec "Publish and admission"): subscribe to
 * `app.lifecycleRequested` first, then push the initial all-owner summary and
 * one on every registry change (`@/lib/native/owners` serializes and dedupes).
 * A native lifecycle request runs the ONE contextual gate over every owner
 * (Discard changes and close / quit / relaunch); the gate's outcome is the
 * answer: proceed (nothing blocking) or keep (Keep editing / Back / another
 * gate already open). A repeated request only focuses the open gate.
 */
import { useEffect } from 'react';
import {
  answerLifecycle,
  publishOwners,
  subscribeLifecycle,
  type LifecycleAction,
} from '@/lib/native/owners';
import { isNativeTarget } from '@/lib/native/target';
import type { OwnerRecord, OwnerRegistry } from '@/lib/owners/ownerRegistry';
import type { OwnerGateEffect } from '@/lib/owners/ownerTransitions';

interface LifecycleTransition {
  effect: OwnerGateEffect;
  affects: (owner: OwnerRecord) => boolean;
  proceed: () => void;
  cancel?: () => void;
}

const EFFECTS: Record<LifecycleAction, OwnerGateEffect> = { close: 'close', quit: 'quit', relaunch: 'relaunch' };
const everyOwner = (): boolean => true;
const noop = (): void => undefined;

export function NativeOwnersBridge({
  registry,
  requestOwnerTransition,
}: {
  registry: OwnerRegistry;
  requestOwnerTransition: (transition: LifecycleTransition) => boolean;
}): null {
  useEffect(() => {
    if (!isNativeTarget()) return;
    const read = (): readonly OwnerRecord[] => registry.getSnapshot();
    const off = subscribeLifecycle((request) => {
      const effect = EFFECTS[request.action];
      if (request.repeat) {
        // Same ID again: the gate for it is open; focusing it is all we do.
        requestOwnerTransition({ effect, affects: everyOwner, proceed: noop, cancel: noop });
        return;
      }
      requestOwnerTransition({
        effect,
        affects: everyOwner,
        proceed: () => void answerLifecycle(request.requestId, 'proceed', read),
        cancel: () => void answerLifecycle(request.requestId, 'keep', read),
      });
    });
    const unsubscribe = registry.subscribe(() => void publishOwners(read()));
    void publishOwners(read());
    return () => {
      unsubscribe();
      off();
    };
  }, [registry, requestOwnerTransition]);
  return null;
}
