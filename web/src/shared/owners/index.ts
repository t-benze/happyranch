/** Config owner registry + contextual owner gate (THR-387 S6-W2a). */
export {
  OwnerGateProvider,
  useOwnerGate,
  useOwnerRecords,
  useOwnerSnapshotReader,
  navigationTransition,
  orgOfPath,
  type OwnerGateEffect,
  type OwnerTransition,
  type NavigationInterceptor,
} from './OwnerGate';
export { useConfigOwner, OwnerScope, type ConfigOwner, type ConfigOwnerOptions, type TrackResult } from './useConfigOwner';
export { OwnerSaveNotice } from './OwnerSaveNotice';
export { ownerContextParams } from './ownerCopy';
