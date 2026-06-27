/**
 * Map a thrown save error (ApiError or otherwise) to a flat list of messages
 * to render in the blocking error panel. The server returns 422 with `detail`
 * that is either a string OR `{errors: string[]}` (or FastAPI's validation
 * list). The client only SURFACES these — it never reimplements the rules.
 */
import { ApiError } from '@/lib/api';

export function extractServerErrors(err: unknown): string[] {
  if (err instanceof ApiError) {
    const d = err.detail;
    if (typeof d === 'string') return [d];
    if (d && typeof d === 'object') {
      const obj = d as Record<string, unknown>;
      if (Array.isArray(obj.errors)) {
        return obj.errors.map((e) => String(e));
      }
      // FastAPI request-validation shape: a list of {loc, msg, ...}
      if (Array.isArray(d)) {
        return (d as { msg?: unknown }[]).map((e) =>
          e && typeof e === 'object' && 'msg' in e ? String(e.msg) : String(e),
        );
      }
      if (typeof obj.msg === 'string') return [obj.msg];
    }
    if (Array.isArray(d)) {
      return (d as { msg?: unknown }[]).map((e) =>
        e && typeof e === 'object' && 'msg' in e ? String(e.msg) : String(e),
      );
    }
    return [err.message];
  }
  if (err instanceof Error) return [err.message];
  return [String(err)];
}
