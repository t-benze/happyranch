// Nonsecret projections only; the language-neutral v1 schema owns validation.
export type Runtime = 'available' | 'offline' | 'restarting' | 'unknown';
export type Observation = 'known' | 'stale' | 'unavailable';
export type SavedAccess = 'none' | 'saved' | 'notSaved' | 'needsPermission' | 'removalIncomplete' | 'unknown';
export type Route = 'direct' | 'relay' | 'unknown';
export type Effect = 'none' | 'confirmed' | 'unknown';
export type Cleanup = 'confirmed' | 'incomplete' | 'unknown' | 'notApplicable';
export type InviteState = 'none' | 'issued' | 'expired' | 'used' | 'unknown'
  | 'minting' | 'encoding' | 'pairing' | 'refused' | 'uncertain' | 'recovered' | 'cancelled';
export interface Device { label: string; comparisonPhrase: string | null }
export interface Computer { computerRef: string; label: string }
export interface HostSnapshot {
  firewallPorts?: Record<'8443/tcp' | '3478/udp', 'added' | 'preexisting_not_owned' | 'blocked_by_existing'
    | 'skipped_ufw_absent' | 'skipped_ufw_inactive' | 'skipped_subnet_rejected'
    | 'pending' | 'add_intent' | 'remove_intent' | 'removed' | 'unverified'>;
  firewallWarning?: 'likely_blocked' | 'unverified' | 'clear';
  setup: 'off' | 'pending' | 'ready' | 'paused' | 'failed' | 'unknown'
    | 'prepared_no_receipt' | 'failed_uncertain' | 'partial_cleanup' | 'runtime_unavailable' | 'setup_unverified' | 'renewal_failed';
  runtime: Runtime; observation: Observation; devices: Device[] | null;
  pending: Device | null; selected: Device | null; inviteState: InviteState;
  expiresAt: string | null;
}
export interface ClientSnapshot {
  stage: 'idle' | 'validating' | 'joining' | 'pairing' | 'waitingApproval' | 'saving' |
    'opening' | 'connected' | 'stopping' | 'failed';
  runtime: Runtime; savedAccess: SavedAccess; route: Route; observedAt: string | null;
  observation: Observation; computers: Computer[] | null; activeComputerRef: string | null;
  expiresAt: string | null; comparisonPhrase: string | null;
}
export interface ActionResult {
  effect: Effect; hostDenial: Cleanup; hostCleanup: Cleanup; localCleanup: Cleanup;
  networkCleanup: Cleanup; inviteState: InviteState; expiresAt: string | null;
  host: HostSnapshot | null; client: ClientSnapshot | null;
}
export interface Health {
  access: 'allowed' | 'accessDenied' | 'revokedConfirmed' | 'unknown';
  runtime: Runtime; route: Route; observedAt: string | null; observation: Observation;
}
export interface SnapshotEvent<T extends HostSnapshot | ClientSnapshot> {
  version: 1; sequence: number; observedAt: string; snapshot: T;
}
