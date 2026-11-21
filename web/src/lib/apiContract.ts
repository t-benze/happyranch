/**
 * Web client API-contract numbers (THR-387 §5 compatibility owner).
 *
 * Mirrors the daemon owner `runtime/daemon/api_contract.py`:
 * only breaking API changes raise the daemon's `current`; additive fields and
 * routes do not. Raise the daemon's `min_client` only to cut off old clients.
 * On this side, raise `CLIENT_CONTRACT` only when this web app requires a
 * breaking daemon API change, and raise `MIN_DAEMON` only to stop using
 * daemons that are too old for it. Invent no tolerance or assumed legacy value.
 *
 * Pure module: no imports, no I/O.
 */

export const CLIENT_CONTRACT = 1;
export const MIN_DAEMON = 1;

/**
 * Compatible iff `GET /api/v1/health` carries `api_contract` and
 * `api_contract.min_client <= CLIENT_CONTRACT <= api_contract.current` and
 * `api_contract.current >= MIN_DAEMON`. Missing or invalid metadata (absent,
 * non-object, non-integer numbers) is incompatible — no legacy contract is
 * assumed for a daemon without metadata.
 */
export function isCompatible(health: unknown): boolean {
  if (!health || typeof health !== 'object') return false;
  const contract = (health as { api_contract?: unknown }).api_contract;
  if (!contract || typeof contract !== 'object') return false;
  const { current, min_client: minClient } = contract as {
    current?: unknown;
    min_client?: unknown;
  };
  if (!Number.isSafeInteger(current) || !Number.isSafeInteger(minClient)) return false;
  const cur = current as number;
  const min = minClient as number;
  return min <= CLIENT_CONTRACT && CLIENT_CONTRACT <= cur && cur >= MIN_DAEMON;
}
