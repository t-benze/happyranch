import definition from '../../../protocol/native-connection-bridge.v1.json';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Schema = { $ref?: string; const?: Json; enum?: Json[]; anyOf?: Schema[]; allOf?: Schema[];
  type?: string; properties?: Record<string, Schema>; required?: string[];
  additionalProperties?: boolean; items?: Schema; maxItems?: number;
  uniqueItems?: boolean; minimum?: number; maximum?: number; minLength?: number;
  maxUtf8Bytes?: number; pattern?: string; format?: string };
const spec = definition as unknown as { $defs: Record<string, Schema>;
  commands: Record<string, { args: Schema; result: Schema }>; events: Record<string, Schema> };
export const commands = Object.keys(spec.commands);
export const events = Object.keys(spec.events);
const encoder = new TextEncoder();
function wellFormed(value: string): boolean {
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
  }
  return true;
}
export const byteLength = (text: string): number => encoder.encode(text).length;
export const closedError = (code: string, effect = 'none', recovery = 'none'): string =>
  JSON.stringify({ code, effect, recovery });

// Bounded input size and closed schemas bound traversal. No coercion/defaults.
function matches(value: unknown, schema: Schema): boolean {
  if (schema.$ref) return matches(value, spec.$defs[schema.$ref.slice('#/$defs/'.length)]);
  if ('const' in schema && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value as Json)) return false;
  if (schema.allOf && !schema.allOf.every((item) => matches(value, item))) return false;
  if (schema.properties && schema.type !== 'object') {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    if (!Object.entries(schema.properties).every(([key, item]) => Object.hasOwn(record, key) && matches(record[key], item))) return false;
  }
  if (schema.anyOf && !schema.anyOf.some((item) => matches(value, item))) return false;
  if (schema.type === 'null') return value === null;
  if (schema.type === 'object') {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    const properties = schema.properties ?? {};
    return (schema.required ?? []).every((key) => Object.hasOwn(record, key)) &&
      Object.keys(record).every((key) => Object.hasOwn(properties, key)
        ? matches(record[key], properties[key]) : schema.additionalProperties !== false);
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || value.length > (schema.maxItems ?? 32)) return false;
    return (!schema.uniqueItems || new Set(value.map((item) => JSON.stringify(item))).size === value.length) &&
      value.every((item) => schema.items !== undefined && matches(item, schema.items));
  }
  if (schema.type === 'integer') return typeof value === 'number' && Number.isSafeInteger(value) &&
    value >= (schema.minimum ?? 0) && value <= (schema.maximum ?? Number.MAX_SAFE_INTEGER);
  if (schema.type === 'string') {
    if (typeof value !== 'string' || !wellFormed(value) ||
      byteLength(value) > (schema.maxUtf8Bytes ?? 256) || value.length < (schema.minLength ?? 0) ||
      (schema.pattern && !new RegExp(schema.pattern, 'u').test(value))) return false;
    if (schema.format === 'utc') {
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)) return false;
      const time = Date.parse(value);
      return Number.isFinite(time) && new Date(time).toISOString() === value.replace(/Z$/, value.includes('.') ? 'Z' : '.000Z');
    }
  }
  return true;
}

function hasDuplicateKeys(raw: string): boolean {
  const stack: { keys: Set<string>; keyExpected: boolean; object: boolean }[] = [];
  // JSON.parse already established syntax; tokens here only track object keys.
  const tokens = raw.match(/"(?:[^"\\]|\\.)*"|[{}[\],:]/g) ?? [];
  for (const token of tokens) {
    if (token === '{' || token === '[') {
      if (stack.length >= 16) return true;
      stack.push({ keys: new Set(), keyExpected: token === '{', object: token === '{' });
    } else if (token === '}' || token === ']') stack.pop();
    else {
      const top = stack.at(-1);
      if (top?.object && token === ',') top.keyExpected = true;
      else if (top?.object && top.keyExpected && token.startsWith('"')) {
        const key = JSON.parse(token) as string;
        if (top.keys.has(key)) return true;
        top.keys.add(key); top.keyExpected = false;
      }
    }
  }
  return false;
}

export function parseString(raw: unknown, schema: Schema, limit: number): Json {
  if (typeof raw !== 'string' || raw.length > limit || byteLength(raw) > limit || !wellFormed(raw)) {
    throw closedError('invalidRequest');
  }
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw closedError('invalidRequest'); }
  if (hasDuplicateKeys(raw) || !matches(value, schema)) throw closedError('invalidRequest');
  return value as Json;
}
export function parseArgs(command: string, raw: unknown): Json {
  if (typeof command !== 'string' || !Object.hasOwn(spec.commands, command)) throw closedError('unsupportedCommand');
  return parseString(raw, spec.commands[command].args, 4096);
}
export function parseResult(command: string, raw: unknown): Json {
  if (typeof command !== 'string' || !Object.hasOwn(spec.commands, command)) throw closedError('unsupportedCommand');
  return command === 'bridge.hello' ? parseHello(raw) as unknown as Json : parseString(raw, spec.commands[command].result, 32768);
}
export const parseError = (raw: unknown): Json => parseString(raw, spec.$defs.error, 32768);
export function parseEvent(event: string, raw: unknown): Json {
  if (typeof event !== 'string' || !Object.hasOwn(spec.events, event)) throw closedError('unsupportedCommand');
  return parseString(raw, spec.events[event], 32768);
}

export interface Hello {
  version: 1; platform: 'linux' | 'mac'; roles: ('host' | 'client')[];
  capabilities: { command: string; availability: 'available' | 'unavailable' | 'unsupportedVersion' }[];
  observationLimits: { host: string[]; client: string[] };
}
export function parseHello(raw: unknown): Hello {
  const hello = parseString(raw, spec.$defs.hello, 32768) as unknown as Hello;
  const names = hello.capabilities.map((item) => item.command);
  if (new Set(names).size !== names.length ||
    (hello.platform === 'mac' && (hello.roles.includes('host') || names.some((name) => name.startsWith('host.')))) ||
    hello.capabilities.some((item) => item.availability === 'available' &&
      !hello.roles.includes(item.command.startsWith('host.') ? 'host' : 'client'))) throw closedError('invalidRequest');
  return hello;
}

// For the later isolated native relay. These never become page-visible methods.
export function encodeRequest(command: string, data: string, requestId: string): string {
  const raw = JSON.stringify({ protocol: 'happyranch.connection', version: 1, requestId,
    command, args: parseArgs(command, data) });
  parseString(raw, spec.$defs.request, 4096);
  return raw;
}
export function decodeReply(command: string, requestId: string, raw: string): string {
  let reply: Record<string, Json>;
  try { reply = parseString(raw, spec.$defs.reply, 32768) as Record<string, Json>; }
  catch { throw closedError('invalidRequest', 'unknown', 'refresh'); }
  if (reply.requestId !== requestId) throw closedError('documentChanged', 'unknown', 'refresh');
  if (reply.ok === false) throw JSON.stringify(reply.error);
  const result = JSON.stringify(reply.result);
  try { parseResult(command, result); } catch { throw closedError('invalidRequest', 'unknown', 'refresh'); }
  return result;
}
export function decodeEvent(raw: string): { event: string; data: string } {
  const event = parseString(raw, spec.$defs.event, 32768) as Record<string, Json>;
  return { event: event.event as string, data: JSON.stringify(event.data) };
}
