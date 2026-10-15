#!/usr/bin/env node
// Explicit offline preparation/check only; never an npm lifecycle or CI build.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cpSync, lstatSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vendor = join(web, 'vendor/react-remove-scroll-bar');
const upstream = join(vendor, 'upstream');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = path => {
  const st = lstatSync(path);
  if (!st.isFile() || st.nlink !== 1) throw Error(`Expected a regular single-link file: ${path}`);
  return readFileSync(path);
};
const census = root => readdirSync(root, { withFileTypes: true }).flatMap(entry => {
  const path = join(root, entry.name);
  if (entry.isDirectory()) return census(path);
  if (!entry.isFile()) throw Error(`Nonregular package member: ${path}`);
  return [path];
});
const provenance = JSON.parse(read(join(vendor, 'provenance.json')));
const lock = JSON.parse(read(join(web, 'package-lock.json')));
if (ts.version !== '5.9.3' || lock.packages['node_modules/typescript'].version !== ts.version) {
  throw Error('Use the existing locked TypeScript 5.9.3; no compiler download');
}
if (provenance.commit !== '8ca9ba5ea52de03308fe8ced94f7b159a44d28ff') throw Error('Unexpected source revision');
const expected = ['LICENSE', 'constants/package.json', 'package.json', 'src/component.tsx', 'src/constants.ts', 'src/index.ts', 'src/utils.ts', 'tsconfig.json'];
if (JSON.stringify(provenance.files.map(row => row.path).sort()) !== JSON.stringify(expected)) throw Error('Incomplete source census');
if (JSON.stringify(census(upstream).map(path => path.slice(upstream.length + 1)).sort()) !== JSON.stringify(expected)) throw Error('Unexpected upstream file census');
for (const row of provenance.files) {
  const bytes = read(join(upstream, row.path));
  const blob = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  if (bytes.length !== row.size || sha(bytes) !== row.sha256 || blob !== row.git_blob) throw Error(`Upstream bytes changed: ${row.path}`);
}
if (!read(join(vendor, 'LICENSE')).equals(read(join(upstream, 'LICENSE')))) throw Error('Original notice changed');
const args = process.argv.slice(2);
if (args.some(arg => !['--write', '--check', '--installed'].includes(arg)) || args.includes('--write') === args.includes('--check')) {
  throw Error('Usage: node scripts/prepare-scrollbar-package.mjs (--write|--check) [--installed]');
}
const manifest = JSON.parse(read(join(vendor, 'package.json')));
if (manifest.name !== 'react-remove-scroll-bar' || manifest.version !== '2.3.7+upstream.8ca9ba5ea52d' || manifest.scripts) throw Error('Unexpected derivative manifest');
const filename = `react-remove-scroll-bar-${manifest.version}.tgz`;
const scratch = mkdtempSync(join(tmpdir(), 'scrollbar-package-'));
try {
  const pkg = join(scratch, 'package');
  cpSync(upstream, join(pkg, 'upstream'), { recursive: true });
  for (const path of ['package.json', 'LICENSE', 'provenance.json', 'README.md']) cpSync(join(vendor, path), join(pkg, path));
  cpSync(join(upstream, 'constants'), join(pkg, 'constants'), { recursive: true });
  const roots = expected.filter(path => path.startsWith('src/')).map(path => join(upstream, path));
  for (const [format, target, module] of [
    ['es5', ts.ScriptTarget.ES5, ts.ModuleKind.CommonJS],
    ['es2015', ts.ScriptTarget.ES2015, ts.ModuleKind.ES2015],
    ['es2019', ts.ScriptTarget.ES2019, ts.ModuleKind.ES2015],
  ]) {
    const program = ts.createProgram(roots, {
      target, module, moduleResolution: ts.ModuleResolutionKind.Node10,
      rootDir: join(upstream, 'src'), outDir: join(pkg, 'dist', format),
      declaration: true, importHelpers: true, esModuleInterop: true,
      strict: true, noEmitOnError: true, jsx: ts.JsxEmit.React,
      types: ['react'], typeRoots: [join(web, 'node_modules/@types')],
      lib: ['lib.dom.d.ts', 'lib.es2015.d.ts'], skipLibCheck: true,
    });
    const diagnostics = ts.getPreEmitDiagnostics(program);
    if (diagnostics.length) throw Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
      getCurrentDirectory: () => web, getCanonicalFileName: x => x, getNewLine: () => '\n',
    }));
    if (program.emit().emitSkipped) throw Error(`Compilation failed: ${format}`);
  }
  const packed = JSON.parse(execFileSync('npm', ['pack', '--offline', '--ignore-scripts', '--json', '--pack-destination', scratch], { cwd: pkg, encoding: 'utf8' }))[0];
  const archive = read(join(scratch, packed.filename));
  const target = join(vendor, filename);
  if (args.includes('--write')) writeFileSync(target, archive);
  else if (!read(target).equals(archive)) throw Error('Checked-in tarball differs from reproducible source compilation');
  if (args.includes('--installed')) {
    const installed = join(web, 'node_modules/react-remove-scroll-bar');
    if (lstatSync(installed).isSymbolicLink()) throw Error('Installed package must not be linked');
    const files = census(pkg).map(p => p.slice(pkg.length + 1)).sort();
    const actual = census(installed).map(p => p.slice(installed.length + 1)).sort();
    if (JSON.stringify(files) !== JSON.stringify(actual)) throw Error(`Installed package file census differs: missing=${files.filter(file => !actual.includes(file))}; extra=${actual.filter(file => !files.includes(file))}`);
    for (const file of files) if (!read(join(pkg, file)).equals(read(join(installed, file)))) throw Error(`Installed bytes differ: ${file}`);
  }
  console.log(JSON.stringify({ filename, compiler: ts.version, size: archive.length,
    sha256: sha(archive), integrity: `sha512-${createHash('sha512').update(archive).digest('base64')}`,
    files: packed.files, installed: args.includes('--installed') }, null, 2));
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
