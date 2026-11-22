/**
 * Remote client configuration policy (THR-387 S8-2b; bridge spec "S8 remote
 * client configuration policy").
 *
 * The web imports the SAME authoritative file the Linux app ships
 * (`protocol/remote-client-config-policy.v1.json`) at build time. Each entry
 * is a `config` (method, path template) pair; everything else is an ordinary
 * operation. While the desktop's committed target is a remote Host, native
 * answers every matching request with `403 {"detail":{"code":
 * "remote_read_only"}}` before forwarding any byte. The web uses the same
 * entries to show those controls read-only up front ("Change this on
 * {Host}"); a disabled control is presentation, native is the enforcement.
 *
 * Pure module: no React, no I/O.
 */
// `@happyranch/remote-client-config-policy` resolves (vite.config.ts alias +
// tsconfig paths) to `protocol/remote-client-config-policy.v1.json` itself.
import policyFile from '@happyranch/remote-client-config-policy';

export type PolicyMethod = 'DELETE' | 'PATCH' | 'POST' | 'PUT';

export interface RemoteConfigEntry {
  method: PolicyMethod;
  /** Absolute daemon path template, e.g. `/api/v1/orgs/{slug}/settings/org`. */
  path_template: string;
  class: 'config';
}

export const REMOTE_READ_ONLY_CODE = 'remote_read_only';

const API_PREFIX = '/api/v1';
const METHODS: ReadonlySet<string> = new Set(['DELETE', 'PATCH', 'POST', 'PUT']);

function loadEntries(raw: unknown): readonly RemoteConfigEntry[] {
  const file = raw as { version?: unknown; entries?: unknown };
  // Closed v1 shape (scripts/check_remote_client_config_policy.py checks the
  // file itself); an unexpected shape is a build defect, so fail loudly.
  if (file.version !== 1 || !Array.isArray(file.entries)) {
    throw new Error('remote-client-config-policy: unsupported policy file');
  }
  return file.entries.map((entry: unknown) => {
    const e = entry as Partial<RemoteConfigEntry>;
    if (
      typeof e.method !== 'string' ||
      !METHODS.has(e.method) ||
      typeof e.path_template !== 'string' ||
      !e.path_template.startsWith(`${API_PREFIX}/`) ||
      e.class !== 'config'
    ) {
      throw new Error('remote-client-config-policy: invalid entry');
    }
    return Object.freeze({ method: e.method, path_template: e.path_template, class: 'config' as const });
  });
}

/** The 21 v1 entries, in file order. */
export const REMOTE_CONFIG_ENTRIES: readonly RemoteConfigEntry[] = Object.freeze(loadEntries(policyFile));

const TEMPLATES: ReadonlySet<string> = new Set(
  REMOTE_CONFIG_ENTRIES.map((entry) => `${entry.method} ${entry.path_template}`),
);

/** A policy operation the UI names: `'<METHOD> <path_template>'`. */
export type RemoteConfigOperation = `${PolicyMethod} ${string}`;

/** True when `operation` (method + exact template) is a policy entry. */
export function isRemoteConfigOperation(operation: RemoteConfigOperation): boolean {
  return TEMPLATES.has(operation);
}

function segments(path: string): string[] {
  return path.split('/').slice(1);
}

function templateMatches(template: string, path: string): boolean {
  const want = segments(template);
  const have = segments(path);
  if (want.length !== have.length) return false;
  return want.every((part, index) => {
    const value = have[index] as string;
    if (part.startsWith('{') && part.endsWith('}')) return value !== '';
    return part === value;
  });
}

/**
 * True when a concrete request (method + path, with or without the
 * `/api/v1` prefix; any query string ignored) matches a policy entry.
 */
export function matchesRemoteConfigRequest(method: string, path: string): boolean {
  const verb = method.toUpperCase();
  if (!METHODS.has(verb)) return false;
  let bare = path.split('?')[0] ?? '';
  if (!bare.startsWith('/')) bare = `/${bare}`;
  const absolute = bare === API_PREFIX || bare.startsWith(`${API_PREFIX}/`) ? bare : `${API_PREFIX}${bare}`;
  return REMOTE_CONFIG_ENTRIES.some(
    (entry) => entry.method === verb && templateMatches(entry.path_template, absolute),
  );
}

/**
 * True for native's pre-forward refusal `403 {"detail":{"code":
 * "remote_read_only"}}` (an `ApiError` from `lib/api`; matched by shape so
 * this module stays import-free). Nothing reached the Host: the draft stays.
 */
export function isRemoteReadOnlyError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const { status, code } = error as { status?: unknown; code?: unknown };
  return status === 403 && code === REMOTE_READ_ONLY_CODE;
}
