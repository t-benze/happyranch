/** Private, invocation-bound packaging observations. No release/rights predicate. */
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { createRequire } from 'node:module';
import type { Plugin, ResolvedConfig } from 'vite';

type Bytes = { size: number; sha256: string };
type BlobRef = Bytes & { path: string; descriptor_sha256: string };
type Row = Record<string, any>;
export type CssRead = { schema: string; path: string; relative: string; base: string; content: string; content_sha256: string; raw_sha256: string; token: Row; read_token: Row };
const HOOKS = ['load', 'transform', 'moduleParsed', 'resolveId', 'renderStart', 'renderChunk', 'generateBundle', 'writeBundle', 'buildEnd', 'closeBundle', 'banner', 'footer', 'intro', 'outro'] as const;
const MAX_OBSERVATIONS = 262144;
const MAX_MODULES = 32768;
const MAX_OPERATIONS = 32768;
const MAX_BLOBS = 32768;
const MAX_HELPERS = 65536;
const BUILD_INPUT = 96 * 1024 ** 2;
const BUILD_NODES = 12582912;
const BODY_WORK = 1024 * 1024 ** 2;
const NAME_FIELDS = new Set(['importer','selected','execPath','loaded','request','path','preliminary_path','sourcemap_path','chunk_id','document_id','reference_id','declared_file_name','resolved_file_name','names','original_names','watch_ids','module_ids','id','source','module','file','base','from','relative','relative_path','filename','cssAssetName','originalFileName','source_site','config_dependencies','input','root','dir','outDir','assetsDir','name']);
type Measure = { bytes: number; nodes: number; names: number };

// Validate/count before emission or retention; no stringify-to-discover overflow.
function measure(value: any, cap: number, maxNodes: number, metadata = false, rootField = ''): Measure {
  const total = { bytes: 0, nodes: 0, names: 0 };
  const add = (n: number) => { total.bytes += n; if (total.bytes > cap) throw new Error('WEB_BUILD_RECORD_CAP'); };
  const stringBytes = (value: string): number => {
    let bytes = 2;
    for (let i = 0; i < value.length; i++) {
      const c = value.charCodeAt(i);
      if (c === 34 || c === 92 || [8,9,10,12,13].includes(c)) bytes += 2;
      else if (c < 32) bytes += 6;
      else if (c < 128) bytes++;
      else if (c < 2048) bytes += 2;
      else if (c >= 0xd800 && c <= 0xdbff) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error('WEB_JSON_SURROGATE');
        bytes += 4;
      } else if (c >= 0xdc00 && c <= 0xdfff) throw new Error('WEB_JSON_SURROGATE');
      else bytes += 3;
      if (bytes > cap) throw new Error('WEB_BUILD_RECORD_CAP');
    }
    return bytes;
  };
  const visit = (item: any, depth: number, field: string) => {
    if (++total.nodes > maxNodes || depth > 32) throw new Error('WEB_JSON_COMPLEXITY_CAP');
    if (item === null) { add(4); return; }
    if (typeof item === 'string') {
      const size = Buffer.byteLength(item);
      if (metadata && size > 4096) throw new Error('WEB_METADATA_STRING_CAP');
      if (metadata && NAME_FIELDS.has(field)) { total.names += size; if (total.names > 8 * 1024 ** 2) throw new Error('WEB_AGGREGATE_NAME_CAP'); }
      add(stringBytes(item)); return;
    }
    if (typeof item === 'boolean') { add(item ? 4 : 5); return; }
    if (typeof item === 'number' && Number.isSafeInteger(item)) { add(String(item).length); return; }
    if (Array.isArray(item)) {
      if (item.length > MAX_OBSERVATIONS) throw new Error('WEB_METADATA_LIST_CAP');
      add(2 + Math.max(0, item.length - 1)); for (const child of item) visit(child, depth + 1, field); return;
    }
    if (item && Object.getPrototypeOf(item) === Object.prototype) {
      const entries = Object.entries(item); add(2 + Math.max(0, entries.length - 1));
      for (const [key, child] of entries) { visit(key, depth + 1, ''); add(1); visit(child, depth + 1, key); } return;
    }
    throw new Error('WEB_JSON_SCALAR');
  };
  visit(value, 0, rootField); return total;
}

export function boundedJson(value: any, cap = CAP, maxNodes = 500000): Buffer {
  const expected = measure(value, cap, maxNodes);
  const output = Buffer.allocUnsafe(expected.bytes); let offset = 0;
  const put = (text: string) => { offset += output.write(text, offset, output.length - offset, 'utf8'); };
  const emit = (item: any) => {
    if (typeof item === 'string') {
      put('"');
      for (let start = 0; start < item.length;) {
        let end = Math.min(start + 8192, item.length);
        if (end < item.length && item.charCodeAt(end - 1) >= 0xd800 && item.charCodeAt(end - 1) <= 0xdbff) end--;
        put(JSON.stringify(item.slice(start, end)).slice(1, -1)); start = end;
      }
      put('"');
    } else if (Array.isArray(item)) { put('['); item.forEach((child, i) => { if (i) put(','); emit(child); }); put(']'); }
    else if (item && typeof item === 'object') { put('{'); Object.entries(item).forEach(([key, child], i) => { if (i) put(','); emit(key); put(':'); emit(child); }); put('}'); }
    else put(String(item));
  };
  emit(value); if (offset !== expected.bytes) throw new Error('WEB_SERIALIZATION_ACCOUNTING'); return output;
}

const sorted = (value: any): any => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, sorted(value[k])])) : value;
const canonicalLine = (value: any): Buffer => Buffer.concat([boundedJson(sorted(value), 256 * 1024), Buffer.from('\n')]);
const closed = (value: any, keys: string[]) => value && Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const HEX = /^[a-f0-9]{64}$/;
const CAP = 16 * 1024 ** 2;
const digest = (raw: Buffer | string): Bytes => {
  if (Buffer.byteLength(raw) > CAP) throw new Error('WEB_BODY_CAP');
  const bytes = typeof raw === 'string' ? Buffer.from(raw) : raw;
  if (bytes.byteLength > CAP) throw new Error('WEB_BODY_CAP');
  return { size: bytes.byteLength, sha256: createHash('sha256').update(bytes).digest('hex') };
};
const codeOf = (value: any): string | Buffer | null => typeof value === 'string' || Buffer.isBuffer(value) ? value : value && typeof value.code === 'string' ? value.code : null;
const clean = (value: any, depth = 0): any => {
  if (depth > 32) throw new Error('WEB_JSON_COMPLEXITY_CAP');
  if (value === undefined) return null;
  if (value instanceof RegExp) return { pattern: value.source, flags: value.flags };
  if (Array.isArray(value)) { if (value.length > MAX_OBSERVATIONS) throw new Error('WEB_METADATA_LIST_CAP'); return value.map(child => clean(child, depth + 1)); }
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clean(v, depth + 1)]));
  return value;
};

export class PackagingCustody {
  private readonly scope = new AsyncLocalStorage<Row>();
  private readonly observations: Row[] = [];
  private readonly operations: Row[] = [];
  private readonly modules = new Map<string, Row>();
  private readonly facades = new WeakMap<object, object>();
  private readonly pending = new Set<Row>();
  private readonly chunkSeen = new Set<string>();
  private bundleBefore: string | null = null;
  private generateSeen = false;
  private writeSeen = false;
  private blobOrdinal = 0;
  private processed = 0;
  private sticky: unknown = null;
  private profile: Row | null = null;
  private readonly toolSelection: Row;
  private config: ResolvedConfig | null = null;
  private finalBundle: Row[] = [];
  private completion = false;
  private originalFailed = false;
  readonly caller: string;
  readonly context: string;
  readonly preparation: string;
  readonly helper: string;
  readonly python: string;
  readonly completionLeaf: string;
  private readonly binding: string;
  private helperCalls = 0;
  private loadOrdinal = 0;
  private compileOrdinal = 0;
  private transportBytes = 0;
  private readonly compiles = new Map<string, { parent: Row; reads: Row[]; ended: boolean }>();
  private metadata = measure({ resource_profile: 'web-private-plain@2', complete: true, profile: null, observations: [], css_operations: [], modules: [], final_bundle: [], counts: { observations: 0, operations: 0, processed_bytes: 0, transport_bytes: 0 } }, BUILD_INPUT, BUILD_NODES, true);
  private callMemberships = 0;

  constructor() {
    if (Number(process.versions.node.split('.')[0]) !== 24) throw new Error('WEB_NODE24_REQUIRED');
    const names = ['HAPPYRANCH_WEB_CALLER', 'HAPPYRANCH_WEB_CONTEXT', 'HAPPYRANCH_WEB_PREPARATION', 'HAPPYRANCH_WEB_PYTHON', 'HAPPYRANCH_WEB_HELPER', 'HAPPYRANCH_WEB_COMPLETION_LEAF'];
    for (const name of names) if (!process.env[name]) throw new Error(`WEB_BINDING_MISSING:${name}`);
    this.caller = process.env.HAPPYRANCH_WEB_CALLER!;
    this.context = process.env.HAPPYRANCH_WEB_CONTEXT!;
    this.preparation = process.env.HAPPYRANCH_WEB_PREPARATION!;
    this.python = process.env.HAPPYRANCH_WEB_PYTHON!;
    this.helper = process.env.HAPPYRANCH_WEB_HELPER!;
    this.completionLeaf = process.env.HAPPYRANCH_WEB_COMPLETION_LEAF!;
    if (Buffer.byteLength(this.caller) > 256 * 1024) throw new Error('WEB_CALLER_CAP');
    const caller = JSON.parse(this.caller);
    if (!closed(caller, ['candidate','roots','run_id','venue','asset_context_sha256','asset_source_sha256','toolchains_sha256','originals']) ||
        !closed(caller.candidate, ['source_sha','source_tree','version','build','arch']) ||
        !closed(caller.roots, ['repo','exported_source','dist','app','output','evidence_dir','iconset'])) throw new Error('WEB_CALLER_INVALID');
    // Admit exact original caller syntax without duplicate-key collapse. The
    // wrapper supplies canonical bytes; descriptor integers never enter JS.
    const strings = [caller.run_id, caller.venue, caller.originals, caller.toolchains_sha256, ...Object.values(caller.candidate), ...Object.values(caller.roots)];
    if (strings.some(v => typeof v !== 'string') || [caller.asset_context_sha256, caller.asset_source_sha256].some(v => v !== null && typeof v !== 'string')) throw new Error('WEB_CALLER_INVALID');
    if (!canonicalLine(caller).subarray(0, -1).equals(Buffer.from(this.caller))) throw new Error('WEB_CALLER_CANONICAL_REQUIRED');
    this.binding = createHash('sha256').update(canonicalLine(caller).subarray(0, -1)).digest('hex');
    if (!path.isAbsolute(this.python) || !path.isAbsolute(this.helper) || this.completionLeaf !== 'web-completion.jsonl' || !/^[a-f0-9]{64}$/.test(this.context) || !/^[a-f0-9]{64}$/.test(this.preparation)) throw new Error('WEB_BINDING_INVALID');
    if (process.env.I18N_BROWSER_EVIDENCE || process.env.I18N_W2A_EVIDENCE || process.env.NODE_OPTIONS || process.env.NODE_PATH || process.env.ESBUILD_BINARY_PATH) throw new Error('WEB_PROFILE_ENV_UNSUPPORTED');
    // Python authenticates the caller/context and supplies a descriptor-free projection.
    const admitted = this.request('read-input', { site: 'content', id: '', base: '' }).selection;
    // Static config imports have already loaded these CJS native modules.
    // Preparation admitted their routes before Vite/config evaluation. Observe
    // the real build process here; no binary is loaded by this observer.
    const edges = admitted.edges.map((edge: Row) => ({ ...edge, selected: createRequire(edge.importer).resolve(edge.request) }));
    const nativePaths = edges.slice(0, 3).map((edge: Row) => edge.selected);
    const cache = createRequire(path.join(caller.roots.exported_source, 'web/vite.config.ts')).cache;
    const loaded = nativePaths.filter((selected: string) => cache[selected]?.loaded === true);
    this.toolSelection = { platform: process.platform, arch: process.arch, execPath: process.execPath, node: process.versions.node, glibc: (process.report.getReport() as any).header.glibcVersionRuntime ?? null, edges, loaded };
    if (process.platform !== admitted.platform || process.arch !== admitted.arch || loaded.length !== 3 || edges.some((edge: Row, i: number) => edge.selected !== admitted.edges[i].selected)) throw new Error('WEB_TOOL_NODE_SELECTION');
  }

  request(stage: string, value?: unknown, index?: number): any {
    if (stage !== 'fail') {
      if (this.sticky) throw this.sticky;
      if (this.originalFailed || this.completion) throw new Error('WEB_FAILED_BUILD');
    }
    if (++this.helperCalls > MAX_HELPERS) throw this.limit('WEB_HELPER_COUNT_CAP');
    const limit = stage === 'publish-build' ? BUILD_INPUT : stage === 'snapshot' ? CAP : 4096;
    const input = Buffer.isBuffer(value) ? value : value === undefined ? undefined : boundedJson(value, limit, stage === 'publish-build' ? BUILD_NODES : 500000);
    if (input && input.length > limit) throw new Error('WEB_PIPE_CAP');
    const args = [this.helper, stage, '--caller', this.caller, '--context', this.context, '--preparation', this.preparation, '--completion-leaf', this.completionLeaf];
    if (index !== undefined) args.push('--index', String(index));
    const capture = stage === 'read-input' ? 32 * 1024 ** 2 : 256 * 1024;
    const result = spawnSync(this.python, args, { input, maxBuffer: capture, stdio: ['pipe', 'pipe', 'pipe'] });
    if (result.error) { this.sticky ??= result.error; throw result.error; }
    if (result.status !== 0) { const error = new Error(`WEB_HELPER_${stage}:${result.status}:${result.stderr.toString('utf8')}`); this.sticky ??= error; throw error; }
    if (result.stderr.length > 256 * 1024) throw new Error('WEB_HELPER_STDERR_CAP');
    if (stage === 'complete-build') {
      if (result.stdout.length > 4096) throw new Error('WEB_COMPLETION_ACK_CAP');
      if (result.stdout.length !== 555 || result.stdout.at(-1) !== 10) throw new Error('WEB_COMPLETION_ACK_INVALID');
      const expected = canonicalLine({ schema: 'web-completion@1', context_binding_sha256: this.binding, context_sha256: this.context, preparation_sha256: this.preparation, completion_leaf: this.completionLeaf, build_token: value });
      if (!result.stdout.equals(expected)) throw new Error('WEB_COMPLETION_ACK_MISMATCH');
      return;
    }
    if (result.stdout.length > (stage === 'read-input' ? 32 * 1024 ** 2 : 4096)) throw new Error('WEB_HELPER_REPLY_CAP');
    if (stage === 'compile-begin' || stage === 'compile-end' || (stage === 'read-input' && (value as Row)?.site === 'stylesheet')) {
      if (result.stderr.length) throw new Error('WEB_CSS_LOAD_TRANSPORT');
      const bytes = (input?.length ?? 0) + result.stdout.length + result.stderr.length;
      this.retain(this.transportBytes, this.transportBytes + bytes); this.transportBytes += bytes;
      const before = this.processed; this.processed += bytes;
      if (this.processed > BODY_WORK) throw this.limit('WEB_AGGREGATE_BODY_CAP');
      this.retain(before, this.processed);
    }
    const raw = new TextDecoder('utf-8', { fatal: true }).decode(result.stdout);
    const reply = JSON.parse(raw);
    if (stage === 'publish-build') {
      if (!closed(reply, ['schema','record_sha256','context_binding_sha256']) || reply.schema !== 'web-build-token@1' || typeof reply.record_sha256 !== 'string' || !HEX.test(reply.record_sha256) || reply.context_binding_sha256 !== this.binding || !result.stdout.equals(canonicalLine(reply))) throw new Error('WEB_PUBLISH_TOKEN_INVALID');
    }
    return reply;
  }

  private compileState(invocation: string): { parent: Row; reads: Row[]; ended: boolean } {
    const state = this.compiles.get(invocation);
    if (!state || state.ended || state.parent !== this.scope.getStore() || !this.pending.has(state.parent)) throw new Error('WEB_CSS_COMPILE_LIFECYCLE');
    return state;
  }

  private event(stage: string, data: Row): any {
    if (this.loadOrdinal >= 32768) throw this.limit('WEB_CSS_LOAD_EVENT_CAP');
    const response = this.request(stage, data, ++this.loadOrdinal);
    const token = stage === 'read-input' ? response.read_token : response;
    if (!closed(token, ['schema', 'index', 'sha256']) || token.schema !== 'web-css-load-token@1' || token.index !== this.loadOrdinal || typeof token.sha256 !== 'string' || !HEX.test(token.sha256)) throw new Error('WEB_CSS_LOAD_TOKEN_INVALID');
    return response;
  }

  beginCompile(module: string, input: string, options: Row): string {
    const parent = this.scope.getStore();
    if (!parent || !this.pending.has(parent) || parent.hook !== 'transform' || this.compileOrdinal >= 32768) throw new Error('WEB_CSS_CALL_PARENT');
    const invocation = `k${++this.compileOrdinal}`;
    this.event('compile-begin', { invocation, parent_outer_call: parent.id, module, input: this.capture(input), options });
    this.compiles.set(invocation, { parent, reads: [], ended: false });
    return invocation;
  }

  readStylesheet(invocation: string, id: string, base: string): CssRead {
    const state = this.compileState(invocation);
    const response = this.event('read-input', { site: 'stylesheet', id, base, invocation, parent_outer_call: state.parent.id, ordinal: state.reads.length + 1 }) as CssRead;
    state.reads.push(response.read_token);
    return response;
  }

  endCompile(invocation: string): void {
    const state = this.compileState(invocation);
    this.event('compile-end', { invocation, parent_outer_call: state.parent.id, read_tokens: state.reads });
    state.ended = true;
  }

  private reserve(delta: Measure): void {
    for (const key of ['bytes','nodes','names'] as const) this.metadata[key] += delta[key];
    if (this.metadata.bytes > BUILD_INPUT || this.metadata.nodes > BUILD_NODES || this.metadata.names > 8 * 1024 ** 2) throw this.limit('WEB_METADATA_CAP');
  }

  private retain(before: any, after: any, comma = false): void {
    const old = before === undefined ? { bytes: 0, nodes: 0, names: 0 } : measure(before, BUILD_INPUT, BUILD_NODES, true);
    const next = measure(after, BUILD_INPUT, BUILD_NODES, true);
    this.reserve({ bytes: next.bytes - old.bytes + (comma ? 1 : 0), nodes: next.nodes - old.nodes, names: next.names - old.names });
  }

  private append(row: Row, field: string, value: any): void {
    const list = row[field];
    if (list.length >= MAX_OBSERVATIONS) throw this.limit('WEB_METADATA_LIST_CAP');
    const delta = measure(value, BUILD_INPUT, BUILD_NODES, true, field);
    this.reserve({ ...delta, bytes: delta.bytes + (list.length ? 1 : 0) });
    list.push(value);
  }

  private update(row: Row, changes: Row): void {
    this.retain(row, { ...row, ...changes }); Object.assign(row, changes);
  }

  private capture(raw: Buffer | string): Bytes {
    this.charge(raw); return digest(raw);
  }

  private charge(raw: Buffer | string): void {
    const before = this.processed; this.processed += Buffer.byteLength(raw);
    if (this.processed > BODY_WORK) throw this.limit('WEB_AGGREGATE_BODY_CAP');
    this.retain(before, this.processed);
  }

  snapshot(value: string | Buffer): BlobRef {
    this.capture(value);
    if (++this.blobOrdinal > MAX_BLOBS) throw this.limit('WEB_BLOB_CAP');
    return this.request('snapshot', Buffer.isBuffer(value) ? value : Buffer.from(value), this.blobOrdinal);
  }

  private next(kind: string, data: Row): Row {
    if (this.observations.length >= MAX_OBSERVATIONS) throw this.limit('WEB_OBSERVATION_CAP');
    const row = { schema: 'web-observation@1', kind, id: `o${this.observations.length + 1}`, ...data };
    this.retain(this.observations.length, this.observations.length + 1);
    this.retain(undefined, row, this.observations.length > 0); this.observations.push(row);
    return row;
  }

  private limit(code: string): Error {
    const kinds: Record<string, number> = {};
    for (const row of this.observations) { const key = row.kind === 'hook_call' ? row.hook : row.kind; kinds[key] = (kinds[key] ?? 0) + 1; }
    return new Error(code + ':' + JSON.stringify({ observations: this.observations.length, operations: this.operations.length, modules: this.modules.size, blobs: this.blobOrdinal, processed_bytes: this.processed, metadata: this.metadata, helper_calls: this.helperCalls, call_memberships: this.callMemberships, kinds }));
  }

  private observe(action: () => void): void {
    if (this.sticky) return;
    try { action(); } catch (error) {
      this.sticky = error;
      try { this.request('fail'); } catch { /* Preserve the first observation error. */ }
      if (error instanceof Error && /^WEB_(?:BUILD_RECORD|JSON_COMPLEXITY|AGGREGATE_NAME|METADATA_STRING|METADATA_LIST)_CAP$/.test(error.message)) error.message = this.limit(error.message).message;
    }
  }

  /** Fixed private sink, called by the original patched CSS operations. */
  record(stage: string, supplied: Row): void {
    if (stage === 'capture_error') {
      this.sticky ??= new Error('WEB_' + supplied.reason);
      try { this.request('fail'); } catch { /* Preserve the first capture error. */ }
      return;
    }
    if (stage === 'admit') {
      const id = supplied.id as string;
      if (!/\.css(?:\?transform-only)?$/.test(id) || /\.module\.css(?:\?|$)/.test(id) || /@import\s+(?:url\()?\s*["'](?:\.|\/[^/])/.test(supplied.raw)) throw new Error(`WEB_CSS_PROFILE_UNSUPPORTED:${id}`);
      return;
    }
    if (stage === 'post_admit') {
      if (supplied.inlined || supplied.modules) throw new Error('WEB_CSS_INLINE_MODULE_UNSUPPORTED');
      return;
    }
    if (this.sticky) throw this.sticky;
    const parent = this.scope.getStore();
    if (!parent || !this.pending.has(parent)) throw new Error('WEB_CSS_CALL_PARENT');
    if (this.operations.length >= MAX_OPERATIONS) throw this.limit('WEB_CSS_OPERATION_CAP');
    const blobFields: Record<string, string[]> = {
      compile: ['input'], content_scan: ['inputs', 'returned'], candidate_build: ['candidates', 'output'], optimize: ['input', 'output'],
      vite_css: ['input', 'output'], style_store: ['css'], css_chunk: ['css', 'js'], css_url: ['input', 'output'],
      css_finalize: ['input', 'hoisted', 'minified', 'output'], css_minify: ['input', 'code', 'output'], css_emit: ['source'], css_asset_read: ['content'], css_asset_store: [], css_asset_hit: [],
    };
    if (!(stage in blobFields)) throw new Error(`WEB_CSS_STAGE_INVALID:${stage}`);
    const data = { ...supplied };
    for (const field of blobFields[stage]) data[field] = this.snapshot(data[field]);
    if (stage === 'compile') {
      const state = this.compiles.get(data.invocation);
      if (!state || !state.ended || state.parent !== parent || data.imports.length !== state.reads.length || data.imports.some((row: Row, i: number) => boundedJson(row.read_token).compare(boundedJson(state.reads[i])) !== 0)) throw new Error('WEB_CSS_COMPILE_INPUT_MISSING');
    }
    if (stage === 'compile') data.imports = data.imports.map((row: Row) => ({ ...row, content: this.snapshot(row.content) }));
    if (stage === 'css_chunk') data.styles = data.styles.map((row: Row) => ({ ...row, css: row.css === null ? null : this.snapshot(row.css) }));
    const operation = { schema: 'css-operation@1', id: `c${this.operations.length + 1}`, parent_outer_call: parent.id, stage, data: clean(data), status: 'returned', error: null };
    this.retain(this.operations.length, this.operations.length + 1);
    this.retain(undefined, operation, this.operations.length > 0); this.operations.push(operation);
  }

  private entries(bundle: Row): Row[] {
    if (Object.keys(bundle).length > 4096) throw new Error('WEB_OUTPUT_CAP');
    const maps = new Set(Object.values(bundle).filter((row: any) => row.type === 'chunk').map((row: any) => row.sourcemapFileName).filter(Boolean));
    return Object.values(bundle).map((row: any) => ({ path: row.fileName, preliminary_path: row.type === 'chunk' ? row.preliminaryFileName : null, type: row.type === 'chunk' ? 'chunk' : maps.has(row.fileName) ? 'map' : 'asset', bytes: this.capture(row.type === 'chunk' ? row.code : row.source), module_ids: row.type === 'chunk' ? Object.keys(row.modules) : [], sourcemap_path: row.type === 'chunk' ? row.sourcemapFileName ?? null : null })).sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
  }

  private core(phase: string, entries: Row[], chunk: string | null = null): Row {
    return this.next('core_snapshot', { phase, chunk_id: chunk, predecessor_ids: [], plugin_ordinal: null, internal_parent_id: null, internal_reference_id: null, entries });
  }

  private facade(real: any): any {
    let facade = this.facades.get(real);
    if (facade) return facade;
    const bound = new Map<PropertyKey, unknown>();
    facade = new Proxy(real, { get: (target, property) => {
      const value = Reflect.get(target, property, target);
      if (typeof value !== 'function') return value;
      if (bound.has(property)) return bound.get(property);
      let wrapped: unknown;
      if (property === 'emitFile' || property === 'setAssetSource' || property === 'addWatchFile') {
        wrapped = (...args: any[]) => {
          const result = Reflect.apply(value, target, args);
          this.observe(() => {
            const call = this.scope.getStore();
            if (!call || !this.pending.has(call)) throw new Error('WEB_CALL_PARENT_INVALID');
            if (property === 'addWatchFile') { this.append(call, 'watch_ids', args[0]); return; }
            const emission = property === 'emitFile' ? args[0] : { type: 'asset', source: args[1] };
            const source = emission.source === undefined ? null : this.capture(emission.source);
            const row = this.next('plugin_emission', { call_id: call.id, action: property === 'emitFile' ? 'emit' : 'set_source', reference_id: property === 'emitFile' ? result : args[0], emitted_type: emission.type, declared_file_name: emission.fileName ?? null, names: emission.names ?? (emission.name ? [emission.name] : []), original_names: emission.originalFileNames ?? (emission.originalFileName ? [emission.originalFileName] : []), source, resolved_file_name: null });
            this.append(call, 'emission_ids', row.id);
          });
          return result;
        };
      } else wrapped = value.bind(target);
      bound.set(property, wrapped);
      return wrapped;
    } });
    this.facades.set(real, facade!);
    return facade;
  }

  private retainModule(id: string, row: Row): void {
    if (!this.modules.has(id) && this.modules.size >= MAX_MODULES) throw this.limit('WEB_MODULE_CAP');
    if (++this.callMemberships > MAX_OBSERVATIONS) throw this.limit('WEB_CALL_MEMBERSHIP_CAP');
    const previous = this.modules.get(id);
    if (previous) {
      this.append(previous, 'calls', row.id);
      if (row.hook === 'moduleParsed') this.update(previous, { code: row.input });
    } else {
      const module = { id, code: row.hook === 'moduleParsed' ? row.input : null, calls: [row.id], source: id.startsWith('/') ? id.split('?')[0] : null };
      this.retain(undefined, module, this.modules.size > 0); this.modules.set(id, module);
    }
  }

  private failOriginal(): void {
    if (this.originalFailed) return;
    this.originalFailed = true;
    if (!this.completion) {
      // Diagnostic only; a secondary receipt failure never replaces the tool error.
      try { this.request('fail'); } catch (error) { this.sticky ??= error; }
    }
  }

  private wrap(plugin: any, ordinal: number, hook: string): any {
    const descriptor = plugin[hook];
    const original = typeof descriptor === 'function' ? descriptor : descriptor.handler;
    const self = this;
    const handler = function(this: any, ...args: any[]): any {
      let row: Row | null = null;
      self.observe(() => {
        const chunk = hook === 'renderChunk' ? args[1].fileName : null;
        if (hook === 'renderChunk' && !self.chunkSeen.has(chunk)) {
          self.chunkSeen.add(chunk);
          self.core('render_input', [{ path: chunk, preliminary_path: chunk, type: 'chunk', bytes: self.capture(args[0]), module_ids: Object.keys(args[1].modules), sourcemap_path: null }], chunk);
        }
        if (hook === 'generateBundle' && !self.generateSeen) { self.generateSeen = true; self.bundleBefore = self.core('generate_input', self.entries(args[1])).id; }
        if (hook === 'writeBundle' && !self.writeSeen) { self.writeSeen = true; self.core('write_input', self.entries(args[1])); }
        const input = hook === 'moduleParsed' ? args[0].code : ['transform','renderChunk'].includes(hook) ? args[0] : null;
        row = self.next('hook_call', { parent_id: self.scope.getStore()?.id ?? null, plugin_ordinal: ordinal, hook, subject_token: null, chunk_id: chunk, input: input === null ? null : self.capture(input), result: null, status: 'open', watch_ids: [], emission_ids: [] });
        self.pending.add(row);
        const id = hook === 'moduleParsed' ? args[0].id : hook === 'transform' ? args[1] : hook === 'load' ? args[0] : null;
        if (id) self.retainModule(id, row);
      });
      const settle = (value: any, failed = false) => self.observe(() => {
        if (!row) return;
        const text = codeOf(value);
        self.update(row, { result: text === null ? null : self.capture(text), status: failed ? 'failed' : value === null || value === undefined ? 'no_result' : 'returned' });
        self.pending.delete(row);
        if (hook === 'generateBundle' && !failed) self.bundleBefore = self.next('bundle_delta', { call_id: row.id, before_snapshot_id: self.bundleBefore, after_entries: self.entries(args[1]) }).id;
      });
      try {
        const result: any = row ? self.scope.run(row, () => Reflect.apply(original, self.facade(this), args)) : Reflect.apply(original, this, args);
        if (result && typeof result.then === 'function') {
          // Observe settlement, return the exact original promise/thenable.
          result.then((value: any) => settle(value), () => { self.failOriginal(); settle(null, true); });
        } else settle(result);
        return result;
      } catch (error) { self.failOriginal(); settle(null, true); throw error; }
    };
    if (typeof descriptor === 'function') return handler;
    return Object.create(Object.getPrototypeOf(descriptor), { ...Object.getOwnPropertyDescriptors(descriptor), handler: { ...Object.getOwnPropertyDescriptor(descriptor, 'handler'), value: handler } });
  }

  plugins(): Plugin[] {
    const self = this;
    const html = (stage: string): Plugin => ({ name: `happyranch:html-${stage}`, transformIndexHtml: { order: stage === 'pre' ? 'pre' : 'post', handler(html, ctx) { self.observe(() => { self.next('html_snapshot', { stage, document_id: ctx.filename, input: self.capture(html), result: self.capture(html) }); }); return html; } } });
    const observer: Plugin = {
      name: 'happyranch:packaging-custody', enforce: 'post',
      configResolved(config) { self.config = config; (config as any).__happyranchCssCustody = self; },
      options: { order: 'post', handler(options) {
        const config = self.config!;
        const all = (options.plugins as any[]).filter(Boolean);
        const order = (value: any) => typeof value === 'object' ? value.order : undefined;
        const optionsHandlers = [...all.filter(p => order(p.options) === 'pre'), ...all.filter(p => p.options && !order(p.options)), ...all.filter(p => order(p.options) === 'post')];
        if (optionsHandlers.at(-1) !== observer) throw new Error('WEB_OPTIONS_BARRIER_INVALID');
        const close = [...all.filter(p => p.closeBundle && order(p.closeBundle) !== 'post'), ...all.filter(p => order(p.closeBundle) === 'post')];
        if (close.at(-1) !== observer) throw new Error('WEB_CLOSE_BARRIER_INVALID');
        const unsupported = { lib: !!config.build.lib, ssr: !!config.build.ssr, watch: !!config.build.watch, worker: config.isWorker, base: config.base !== '/', cssMap: !!config.css.devSourcemap, split: config.build.cssCodeSplit !== true, cssMinifier: !['esbuild', true].includes(config.build.cssMinify), minifier: config.build.minify !== 'esbuild', input: options.input !== path.join(config.root, 'index.html'), outputPlugins: !!config.build.rollupOptions.plugins, outputOverride: !!config.build.rollupOptions.output };
        for (const [key, invalid] of Object.entries(unsupported)) if (invalid) throw new Error(`WEB_PROFILE_UNSUPPORTED:${key}`);
        if (all.length > 256) throw new Error('WEB_PLUGIN_CAP');
        self.profile = {
          tool_selection: self.toolSelection,
          command: config.command, mode: config.mode, isProduction: config.isProduction, isWorker: config.isWorker, root: config.root, base: config.base, appType: config.appType,
          input: options.input, output: { dir: config.build.outDir, format: 'es', addons: [] },
          build: clean(Object.fromEntries(['outDir','assetsDir','assetsInlineLimit','cssCodeSplit','sourcemap','minify','cssMinify','target','cssTarget','write','copyPublicDir','lib','ssr','watch','modulePreload','commonjsOptions'].map(k => [k, (config.build as any)[k]]))),
          css: { devSourcemap: false, postcss: { plugins: [] }, transformer: config.css.transformer ?? null },
          esbuild: clean(config.esbuild), define: clean(config.define ?? {}), env: Object.fromEntries(Object.entries(config.env).map(([k,v]) => [k,self.capture(String(v))])),
          config_dependencies: config.configFileDependencies,
          plugins: all.map((p, i) => ({ ordinal: i, name: p.name, hooks: HOOKS.filter(h => p[h]).map(h => ({ name: h, order: order(p[h]) ?? null, sequential: typeof p[h] === 'object' ? p[h].sequential ?? false : false })) })),
        };
        self.retain(null, self.profile);
        return { ...options, plugins: all.map((p, ordinal) => {
          if (p === observer) return p;
          const descriptors = Object.getOwnPropertyDescriptors(p);
          for (const hook of HOOKS) if (p[hook]) descriptors[hook] = { ...descriptors[hook], value: self.wrap(p, ordinal, hook) };
          return Object.create(Object.getPrototypeOf(p), descriptors);
        }) };
      } },
      moduleParsed(info) {
        // Observe Rollup's public final module even when no ordinary plugin
        // installs this hook. The callback belongs to this existing observer.
        self.observe(() => {
          if (typeof info.code !== 'string') throw new Error('WEB_MODULE_CODE_MISSING');
          const ordinal = self.profile!.plugins.find((p: Row) => p.name === observer.name).ordinal;
          const row = self.next('hook_call', { parent_id: self.scope.getStore()?.id ?? null, plugin_ordinal: ordinal, hook: 'moduleParsed', subject_token: null, chunk_id: null, input: self.capture(info.code), result: null, status: 'no_result', watch_ids: [], emission_ids: [] });
          self.retainModule(info.id, row);
        });
      },
      buildEnd(error) { if (error) self.failOriginal(); },
      writeBundle: { order: 'post', sequential: true, handler(_options, bundle) {
        if (!self.writeSeen) { self.writeSeen = true; self.core('write_input', self.entries(bundle)); }
        const final = self.entries(bundle); self.retain([], final); self.finalBundle = final;
        for (const row of self.observations) if (row.kind === 'plugin_emission') self.update(row, { resolved_file_name: this.getFileName(row.reference_id) });
      } },
      closeBundle: { order: 'post', sequential: true, handler() {
        // Preserve the original rejected/thrown tool error during cleanup.
        // The native caller's finish path records failure, never a completion.
        if (self.originalFailed) return;
        if (self.sticky) throw self.sticky;
        if ([...self.compiles.values()].some(state => !state.ended) || self.pending.size || self.completion || !self.writeSeen) throw new Error('WEB_CALL_INCOMPLETE');
        const payload = { resource_profile: 'web-private-plain@2', complete: true, profile: self.profile, observations: self.observations, css_operations: self.operations, modules: [...self.modules.values()], final_bundle: self.finalBundle, counts: { observations: self.observations.length, operations: self.operations.length, processed_bytes: self.processed, transport_bytes: self.transportBytes } };
        const measured = measure(payload, BUILD_INPUT, BUILD_NODES, true);
        if (Object.keys(measured).some(key => measured[key as keyof Measure] !== self.metadata[key as keyof Measure])) throw new Error('WEB_METADATA_ACCOUNTING_MISMATCH');
        const encoded = boundedJson(payload, BUILD_INPUT, BUILD_NODES);
        const token = self.request('publish-build', encoded);
        self.request('complete-build', token);
        self.completion = true;
      } },
    };
    return [html('pre'), html('post'), observer];
  }
}

export function packagingCustody(): PackagingCustody | null {
  const keys = ['HAPPYRANCH_WEB_CALLER','HAPPYRANCH_WEB_CONTEXT','HAPPYRANCH_WEB_PREPARATION','HAPPYRANCH_WEB_PYTHON','HAPPYRANCH_WEB_HELPER','HAPPYRANCH_WEB_COMPLETION_LEAF'];
  if (!keys.some(key => process.env[key] !== undefined)) return null;
  return new PackagingCustody();
}
