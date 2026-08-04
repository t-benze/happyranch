/**
 * Deterministic evidence packaging for THR-105 Todos LIGHT — TASK-4230.
 *
 * Reads the output directory produced by thr105-todos-quick.mjs,
 * thr105-todos-diff-light.mjs and thr105-todos-report-light.mjs, builds a
 * MANIFEST.json that lists every payload file hash, then produces a
 * deterministic tar.gz archive.
 *
 * MANIFEST.json intentionally does NOT contain its own SHA-256; that value is
 * computed after packaging and published externally in the PR body.
 */
import { mkdir, readdir, readFile, writeFile, copyFile, stat } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const OUT = process.argv[2] || join(HERE, 'out', 'thr105-complete-light');

const PR = 548;
const BRANCH = 'task/TASK-4084';
const VIEWPORT = [1440, 900];
const THEME = 'light';

function sha256File(path) {
  return new Promise((res, rej) => {
    const hash = createHash('sha256');
    const proc = spawn('sh', ['-c', `cat "${path}"`], { stdio: ['ignore', 'pipe', 'ignore'] });
    proc.stdout.on('data', (d) => hash.update(d));
    proc.on('exit', (code) => {
      if (code !== 0) return rej(new Error(`sha256 read failed for ${path}`));
      res(hash.digest('hex'));
    });
    proc.on('error', rej);
  });
}

function exec(cmd) {
  return new Promise((res, rej) => {
    const proc = spawn('sh', ['-c', cmd], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    proc.stdout.on('data', (d) => (out += d));
    proc.stderr.on('data', (d) => (err += d));
    proc.on('exit', (code) => {
      if (code !== 0) return rej(new Error(`${cmd} failed (${code}): ${err || out}`));
      res(out.trim());
    });
    proc.on('error', rej);
  });
}

async function stageFile(src, dst, mtimeEpoch) {
  await copyFile(src, dst);
  // Normalize mtime for deterministic tar output.
  const d = new Date(mtimeEpoch * 1000).toISOString().replace(/[-:T]/g, '').replace(/\..*/, '');
  await exec(`touch -t ${d} "${dst}"`);
}

async function main() {
  const head = (await exec('git rev-parse HEAD')) || 'UNKNOWN';
  const base = (await exec('git merge-base origin/main HEAD')) || 'UNKNOWN';
  const manifestId = `thr105-pr${PR}-${head}-${THEME}-${VIEWPORT[0]}x${VIEWPORT[1]}`;
  const archiveName = `${manifestId}.tar.gz`;

  const entries = await readdir(OUT, { withFileTypes: true });
  const files = entries
    .filter((e) => e.isFile() && e.name !== 'MANIFEST.json')
    .map((e) => e.name)
    .sort();

  const staging = join(tmpdir(), `thr105-package-${randomUUID()}`);
  await mkdir(staging, { recursive: true });

  // Use HEAD author date as the deterministic archive mtime anchor.
  const headDate = await exec('git log -1 --format=%ct HEAD');
  const mtimeEpoch = Number(headDate) || 1704067200;

  const fileHashes = {};
  for (const name of files) {
    const src = join(OUT, name);
    const dst = join(staging, name);
    await stageFile(src, dst, mtimeEpoch);
    fileHashes[name] = await sha256File(dst);
  }

  const manifest = {
    manifest_id: manifestId,
    task_id: 'TASK-4230',
    pr: PR,
    branch: BRANCH,
    head,
    base,
    viewport: VIEWPORT,
    theme: THEME,
    generated_at: new Date(mtimeEpoch * 1000).toISOString(),
    files: fileHashes,
    archive_sha256: 'PUBLISHED_EXTERNALLY',
    note: 'Archive SHA-256 is published externally in the PR body; it is NOT an internal checksum. MANIFEST.json does not assert its own content hash.',
  };

  const manifestPath = join(staging, 'MANIFEST.json');
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  await exec(`touch -t ${new Date(mtimeEpoch * 1000).toISOString().replace(/[-:T]/g, '').replace(/\..*/, '')} "${manifestPath}"`);

  // Build deterministic tar.gz: sorted names, fixed mtime, no gzip filename/timestamp.
  const archivePath = join(OUT, archiveName);
  const sortedNames = [...files, 'MANIFEST.json'].sort().map((n) => `"${n}"`).join(' ');
  await exec(
    `cd "${staging}" && tar -cf - ${sortedNames} | gzip -n > "${archivePath}"`,
  );

  // Compute external checksums from the actual produced artifacts.
  const archiveSha = await sha256File(archivePath);
  const manifestSha = await sha256File(manifestPath);

  const summary = {
    archive: archivePath,
    archive_sha256: archiveSha,
    manifest: manifestPath,
    manifest_sha256: manifestSha,
    manifest_id: manifestId,
    head,
    base,
  };

  console.log(JSON.stringify(summary, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
