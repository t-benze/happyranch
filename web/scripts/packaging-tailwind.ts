/** One explicit-input compiler path for the bound packaging build. */
import path from 'node:path';
import { compile } from 'tailwindcss';
import { optimize } from '@tailwindcss/node';
import { Scanner } from '@tailwindcss/oxide';
import type { Plugin } from 'vite';
import type { PackagingCustody, CssRead } from './packaging-custody';

/** Reject ambiguous imported URL syntax before content reaches the compiler. */
function importedUrls(css: string): void {
  let i = 0;
  while (i < css.length) {
    if (css.startsWith('/*', i)) { const end = css.indexOf('*/', i + 2); if (end < 0) throw new Error('WEB_CSS_URL_SYNTAX'); i = end + 2; continue; }
    if (css[i] === '"' || css[i] === "'") {
      const quote = css[i++];
      while (i < css.length && css[i] !== quote) { if (css[i] === '\\') i++; i++; }
      if (i >= css.length) throw new Error('WEB_CSS_URL_SYNTAX'); i++; continue;
    }
    if (css[i] === '\\') throw new Error('WEB_CSS_URL_ESCAPE');
    if (/[a-z_-]/i.test(css[i])) {
      const start = i;
      while (i < css.length && /[a-z0-9_-]/i.test(css[i])) i++;
      if (css[i] === '\\') throw new Error('WEB_CSS_URL_ESCAPE');
      const ident = css.slice(start, i).toLowerCase();
      let j = i; while (/\s/.test(css[j] ?? '') && j < css.length) j++;
      if (css[j] === '(') {
        if (ident === 'image-set' || ident === '-webkit-image-set') throw new Error('WEB_CSS_IMAGE_SET_UNSUPPORTED');
        if (ident === 'url') {
          let k = j + 1; while (k < css.length && /\s/.test(css[k])) k++;
          const quote = css[k] === '"' || css[k] === "'" ? css[k++] : null;
          const begin = k;
          while (k < css.length && css[k] !== (quote ?? ')')) { if (css[k] === '\\') throw new Error('WEB_CSS_URL_ESCAPE'); k++; }
          if (k >= css.length) throw new Error('WEB_CSS_URL_SYNTAX');
          const url = css.slice(begin, k).trim();
          if (quote) { k++; while (k < css.length && /\s/.test(css[k])) k++; }
          if (css[k] !== ')') throw new Error('WEB_CSS_URL_SYNTAX');
          if (!/^(?:\/|#|data:|https?:)/.test(url)) throw new Error('WEB_CSS_RELATIVE_URL_UNSUPPORTED');
          i = k + 1;
        }
      }
      continue;
    }
    i++;
  }
}

export function packagingTailwind(custody: PackagingCustody): Plugin {
  const seen = new Set<string>();
  return {
    name: 'happyranch:packaging-tailwind', enforce: 'pre',
    async transform(input, id) {
      if (!/\.css(?:\?transform-only)?$/.test(id)) return null;
      if (seen.has(id)) throw new Error(`WEB_CSS_STATE_CHAIN_INVALID:${id}`);
      seen.add(id);
      const base = path.dirname(id.split('?')[0]);
      const imports: Record<string, unknown>[] = [];
      const compiler = await compile(input, {
        base, from: id, polyfills: 0,
        loadModule(module, moduleBase) { throw new Error(`WEB_CSS_MODULE_UNSUPPORTED:${module}:${moduleBase}`); },
        async loadStylesheet(module, moduleBase) {
          const response = custody.request('read-input', { site: 'stylesheet', id: module, base: moduleBase }) as CssRead;
          importedUrls(response.content);
          imports.push({ id: module, base: moduleBase, relative: response.relative, content: response.content, raw_sha256: response.raw_sha256, decoded_sha256: response.content_sha256 });
          return { path: response.path, base: response.base, content: response.content };
        },
      });
      if (compiler.root !== null || compiler.sources.length) throw new Error('WEB_CSS_UNSUPPORTED_SOURCE_DIRECTIVE');
      custody.record('compile', { module: id, input, options: { base, from: id, polyfills: 0, sourcemap: false }, imports, features: compiler.features, root: compiler.root, sources: compiler.sources });
      const candidates = new Set<string>();
      const utilities = !!(compiler.features & 16 /* pinned Tailwind4.3.0 Features.Utilities */);
      if (utilities) {
        const manifest = custody.request('read-input', { site: 'content', id: '', base: '' });
        const scanner = new Scanner({ sources: [] });
        for (let offset = 0, batch = 0; offset < manifest.entries.length; offset += 64, batch++) {
          const rows = manifest.entries.slice(offset, offset + 64).map((r: any) => ({ relative_path: r.relative_path, extension: r.extension, content: r.content }));
          const result = scanner.scanFiles(rows.map((r: any) => ({ content: r.content, extension: r.extension })));
          custody.record('content_scan', { module: id, batch, inputs: JSON.stringify(rows), returned: JSON.stringify(result) });
          for (const candidate of result) { candidates.add(candidate); if (candidates.size > 32768) throw new Error('WEB_CSS_CANDIDATE_CAP'); }
        }
      }
      const ordered = [...candidates];
      const built = compiler.build(ordered);
      custody.record('candidate_build', { module: id, candidates: JSON.stringify(ordered), output: built, utilities });
      const options = { file: id, minify: true, map: undefined };
      const result = optimize(built, options);
      custody.record('optimize', { module: id, input: built, options: { file: id, minify: true, map: null }, output: result.code, map: result.map ?? null });
      return { code: result.code, map: null };
    },
  };
}
