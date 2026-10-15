/** Private, invocation-bound packaging observations. No release/rights predicate. */
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { writeSync } from 'node:fs';
import path from 'node:path';
import type { Plugin, ResolvedConfig } from 'vite';

type Bytes = { size: number; sha256: string };
type BlobRef = Bytes & { path: string; descriptor_sha256: string };
type Row = Record<string, any>;
export type CssRead = { schema: string; path: string; relative: string; base: string; content: string; content_sha256: string; raw_sha256: string; token: Row };
const HOOKS = ['load', 'transform', 'moduleParsed', 'resolveId', 'renderStart', 'renderChunk', 'generateBundle', 'writeBundle', 'buildEnd', 'closeBundle', 'banner', 'footer', 'intro', 'outro'] as const;
const MAX = 32768;
const CAP = 16 * 1024 ** 2;
const digest = (raw: Buffer | string): Bytes => {
  const bytes = typeof raw === 'string' ? Buffer.from(raw) : raw;
  if (bytes.byteLength > CAP) throw new Error('WEB_BODY_CAP');
  return { size: bytes.byteLength, sha256: createHash('sha256').update(bytes).digest('hex') };
};
const codeOf = (value: any): string | Buffer | null => typeof value === 'string' || Buffer.isBuffer(value) ? value : value && typeof value.code === 'string' ? value.code : null;
const clean = (value: any): any => {
  if (value === undefined) return null;
  if (value instanceof RegExp) return { pattern: value.source, flags: value.flags };
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clean(v)]));
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
  private config: ResolvedConfig | null = null;
  private finalBundle: Row[] = [];
  private completion = false;
  readonly caller: string;
  readonly context: string;
  readonly preparation: string;
  readonly helper: string;
  readonly python: string;
  readonly fd: number;

  constructor() {
    if (Number(process.versions.node.split('.')[0]) !== 24) throw new Error('WEB_NODE24_REQUIRED');
    const names = ['HAPPYRANCH_WEB_CALLER', 'HAPPYRANCH_WEB_CONTEXT', 'HAPPYRANCH_WEB_PREPARATION', 'HAPPYRANCH_WEB_PYTHON', 'HAPPYRANCH_WEB_HELPER', 'HAPPYRANCH_WEB_COMPLETION_FD'];
    for (const name of names) if (!process.env[name]) throw new Error(`WEB_BINDING_MISSING:${name}`);
    this.caller = process.env.HAPPYRANCH_WEB_CALLER!;
    this.context = process.env.HAPPYRANCH_WEB_CONTEXT!;
    this.preparation = process.env.HAPPYRANCH_WEB_PREPARATION!;
    this.python = process.env.HAPPYRANCH_WEB_PYTHON!;
    this.helper = process.env.HAPPYRANCH_WEB_HELPER!;
    this.fd = Number(process.env.HAPPYRANCH_WEB_COMPLETION_FD);
    if (!path.isAbsolute(this.python) || !path.isAbsolute(this.helper) || !Number.isSafeInteger(this.fd) || this.fd < 3 || !/^[a-f0-9]{64}$/.test(this.context) || !/^[a-f0-9]{64}$/.test(this.preparation)) throw new Error('WEB_BINDING_INVALID');
    if (process.env.I18N_BROWSER_EVIDENCE || process.env.I18N_W2A_EVIDENCE || process.env.NODE_OPTIONS || process.env.NODE_PATH || process.env.ESBUILD_BINARY_PATH) throw new Error('WEB_PROFILE_ENV_UNSUPPORTED');
    // Python authenticates the caller/context and supplies a descriptor-free projection.
    this.request('read-input', { site: 'content', id: '', base: '' });
  }

  request(stage: string, value?: unknown, index?: number): any {
    const input = Buffer.isBuffer(value) ? value : value === undefined ? undefined : Buffer.from(JSON.stringify(value));
    if (input && input.length > 32 * 1024 ** 2) throw new Error('WEB_PIPE_CAP');
    const args = [this.helper, stage, '--caller', this.caller, '--context', this.context, '--preparation', this.preparation];
    if (index !== undefined) args.push('--index', String(index));
    const result = spawnSync(this.python, args, { input, maxBuffer: 32 * 1024 ** 2, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
    if (result.error || result.status !== 0) throw new Error(`WEB_HELPER_${stage}:${result.stderr || result.error}`);
    return JSON.parse(result.stdout);
  }

  private charge(raw: Buffer | string): void {
    this.processed += Buffer.byteLength(raw);
    if (this.processed > 512 * 1024 ** 2) throw new Error('WEB_AGGREGATE_BODY_CAP');
  }

  snapshot(value: string | Buffer): BlobRef {
    this.charge(value); digest(value);
    if (++this.blobOrdinal > MAX) throw new Error('WEB_BLOB_CAP');
    return this.request('snapshot', Buffer.isBuffer(value) ? value : Buffer.from(value), this.blobOrdinal);
  }

  private next(kind: string, data: Row): Row {
    if (this.observations.length >= MAX) throw new Error('WEB_OBSERVATION_CAP:' + JSON.stringify(this.observations.reduce((counts, row) => { const key = row.kind === 'hook_call' ? row.hook : row.kind; counts[key] = (counts[key] ?? 0) + 1; return counts; }, {} as Record<string, number>)));
    const row = { schema: 'web-observation@1', kind, id: `o${this.observations.length + 1}`, ...data };
    this.observations.push(row);
    return row;
  }

  private observe(action: () => void): void {
    if (this.sticky) return;
    try { action(); } catch (error) { this.sticky = error; }
  }

  /** Fixed private sink, called by the original patched CSS operations. */
  record(stage: string, supplied: Row): void {
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
    if (this.operations.length >= MAX) throw new Error('WEB_CSS_OPERATION_CAP');
    const blobFields: Record<string, string[]> = {
      compile: ['input'], content_scan: ['inputs', 'returned'], candidate_build: ['candidates', 'output'], optimize: ['input', 'output'],
      vite_css: ['input', 'output'], style_store: ['css'], css_chunk: ['css', 'js'], css_url: ['input', 'output'],
      css_finalize: ['input', 'hoisted', 'minified', 'output'], css_minify: ['input', 'code', 'output'], css_emit: ['source'], css_asset_read: ['content'], css_asset_store: [], css_asset_hit: [],
    };
    if (!(stage in blobFields)) throw new Error(`WEB_CSS_STAGE_INVALID:${stage}`);
    const data = { ...supplied };
    for (const field of blobFields[stage]) data[field] = this.snapshot(data[field]);
    if (stage === 'compile') data.imports = data.imports.map((row: Row) => ({ ...row, content: this.snapshot(row.content) }));
    if (stage === 'css_chunk') data.styles = data.styles.map((row: Row) => ({ ...row, css: row.css === null ? null : this.snapshot(row.css) }));
    this.operations.push({ schema: 'css-operation@1', id: `c${this.operations.length + 1}`, parent_outer_call: parent.id, stage, data: clean(data), status: 'returned', error: null });
  }

  private entries(bundle: Row): Row[] {
    const maps = new Set(Object.values(bundle).filter((row: any) => row.type === 'chunk').map((row: any) => row.sourcemapFileName).filter(Boolean));
    return Object.values(bundle).map((row: any) => ({ path: row.fileName, preliminary_path: row.type === 'chunk' ? row.preliminaryFileName : null, type: row.type === 'chunk' ? 'chunk' : maps.has(row.fileName) ? 'map' : 'asset', bytes: digest(row.type === 'chunk' ? row.code : row.source), module_ids: row.type === 'chunk' ? Object.keys(row.modules) : [], sourcemap_path: row.type === 'chunk' ? row.sourcemapFileName ?? null : null })).sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
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
            if (property === 'addWatchFile') { call.watch_ids.push(args[0]); return; }
            const emission = property === 'emitFile' ? args[0] : { type: 'asset', source: args[1] };
            const source = emission.source === undefined ? null : digest(emission.source);
            const row = this.next('plugin_emission', { call_id: call.id, action: property === 'emitFile' ? 'emit' : 'set_source', reference_id: property === 'emitFile' ? result : args[0], emitted_type: emission.type, declared_file_name: emission.fileName ?? null, names: emission.names ?? (emission.name ? [emission.name] : []), original_names: emission.originalFileNames ?? (emission.originalFileName ? [emission.originalFileName] : []), source, resolved_file_name: null });
            call.emission_ids.push(row.id);
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
          self.core('render_input', [{ path: chunk, preliminary_path: chunk, type: 'chunk', bytes: digest(args[0]), module_ids: Object.keys(args[1].modules), sourcemap_path: null }], chunk);
        }
        if (hook === 'generateBundle' && !self.generateSeen) { self.generateSeen = true; self.bundleBefore = self.core('generate_input', self.entries(args[1])).id; }
        if (hook === 'writeBundle' && !self.writeSeen) { self.writeSeen = true; self.core('write_input', self.entries(args[1])); }
        const input = hook === 'moduleParsed' ? args[0].code : ['transform','renderChunk'].includes(hook) ? args[0] : null;
        row = self.next('hook_call', { parent_id: self.scope.getStore()?.id ?? null, plugin_ordinal: ordinal, hook, subject_token: null, chunk_id: chunk, input: input === null ? null : digest(input), result: null, status: 'open', watch_ids: [], emission_ids: [] });
        self.pending.add(row);
        const id = hook === 'moduleParsed' ? args[0].id : hook === 'transform' ? args[1] : hook === 'load' ? args[0] : null;
        if (id) {
          const module = self.modules.get(id) ?? { id, code: null, calls: [], source: id.startsWith('/') ? id.split('?')[0] : null };
          module.calls.push(row.id); self.modules.set(id, module);
          if (hook === 'moduleParsed') module.code = digest(args[0].code);
        }
      });
      const settle = (value: any, failed = false) => self.observe(() => {
        if (!row) return;
        const text = codeOf(value);
        row.result = text === null ? null : digest(text);
        row.status = failed ? 'failed' : value === null || value === undefined ? 'no_result' : 'returned';
        self.pending.delete(row);
        if (hook === 'generateBundle' && !failed) self.bundleBefore = self.next('bundle_delta', { call_id: row.id, before_snapshot_id: self.bundleBefore, after_entries: self.entries(args[1]) }).id;
      });
      try {
        const result: any = row ? self.scope.run(row, () => Reflect.apply(original, self.facade(this), args)) : Reflect.apply(original, this, args);
        if (result && typeof result.then === 'function') {
          // Observe settlement, return the exact original promise/thenable.
          result.then((value: any) => settle(value), () => settle(null, true));
        } else settle(result);
        return result;
      } catch (error) { settle(null, true); throw error; }
    };
    if (typeof descriptor === 'function') return handler;
    return Object.create(Object.getPrototypeOf(descriptor), { ...Object.getOwnPropertyDescriptors(descriptor), handler: { ...Object.getOwnPropertyDescriptor(descriptor, 'handler'), value: handler } });
  }

  plugins(): Plugin[] {
    const self = this;
    const html = (stage: string): Plugin => ({ name: `happyranch:html-${stage}`, transformIndexHtml: { order: stage === 'pre' ? 'pre' : 'post', handler(html, ctx) { self.observe(() => { self.next('html_snapshot', { stage, document_id: ctx.filename, input: digest(html), result: digest(html) }); }); return html; } } });
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
        self.profile = {
          command: config.command, mode: config.mode, isProduction: config.isProduction, isWorker: config.isWorker, root: config.root, base: config.base, appType: config.appType,
          input: options.input, output: { dir: config.build.outDir, format: 'es', addons: [] },
          build: clean(Object.fromEntries(['outDir','assetsDir','assetsInlineLimit','cssCodeSplit','sourcemap','minify','cssMinify','target','cssTarget','write','copyPublicDir','lib','ssr','watch','modulePreload','commonjsOptions'].map(k => [k, (config.build as any)[k]]))),
          css: { devSourcemap: false, postcss: { plugins: [] }, transformer: config.css.transformer ?? null },
          esbuild: clean(config.esbuild), define: clean(config.define ?? {}), env: Object.fromEntries(Object.entries(config.env).map(([k,v]) => [k,digest(String(v))])),
          config_dependencies: config.configFileDependencies,
          plugins: all.map((p, i) => ({ ordinal: i, name: p.name, hooks: HOOKS.filter(h => p[h]).map(h => ({ name: h, order: order(p[h]) ?? null, sequential: typeof p[h] === 'object' ? p[h].sequential ?? false : false })) })),
        };
        return { ...options, plugins: all.map((p, ordinal) => {
          if (p === observer) return p;
          const descriptors = Object.getOwnPropertyDescriptors(p);
          for (const hook of HOOKS) if (p[hook]) descriptors[hook] = { ...descriptors[hook], value: self.wrap(p, ordinal, hook) };
          return Object.create(Object.getPrototypeOf(p), descriptors);
        }) };
      } },
      writeBundle: { order: 'post', sequential: true, handler(_options, bundle) {
        if (!self.writeSeen) { self.writeSeen = true; self.core('write_input', self.entries(bundle)); }
        self.finalBundle = self.entries(bundle);
        for (const row of self.observations) if (row.kind === 'plugin_emission') row.resolved_file_name = this.getFileName(row.reference_id);
      } },
      closeBundle: { order: 'post', sequential: true, handler() {
        if (self.sticky) throw self.sticky;
        if (self.pending.size || self.completion || !self.writeSeen) throw new Error('WEB_CALL_INCOMPLETE');
        const payload = { complete: true, profile: self.profile, observations: self.observations, css_operations: self.operations, modules: [...self.modules.values()], final_bundle: self.finalBundle, counts: { observations: self.observations.length, operations: self.operations.length, processed_bytes: self.processed } };
        if (Buffer.byteLength(JSON.stringify(payload)) > 16 * 1024 ** 2) throw new Error('WEB_BUILD_RECORD_CAP');
        const token = self.request('publish-build', payload);
        const line = JSON.stringify(token) + '\n';
        if (Buffer.byteLength(line) > 4096) throw new Error('WEB_COMPLETION_CAP');
        writeSync(self.fd, line); self.completion = true;
      } },
    };
    return [html('pre'), html('post'), observer];
  }
}

export function packagingCustody(): PackagingCustody | null {
  const keys = ['HAPPYRANCH_WEB_CALLER','HAPPYRANCH_WEB_CONTEXT','HAPPYRANCH_WEB_PREPARATION','HAPPYRANCH_WEB_PYTHON','HAPPYRANCH_WEB_HELPER','HAPPYRANCH_WEB_COMPLETION_FD'];
  if (!keys.some(key => process.env[key] !== undefined)) return null;
  return new PackagingCustody();
}
