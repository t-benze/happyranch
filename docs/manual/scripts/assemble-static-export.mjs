// Assemble the optional static export (HAPPYRANCH_BUILD_TARGET=static-export)
// into a static-host package: <destination>/site/ is the only directory to
// upload or serve; <destination>/package-manifest.json sits beside it.
// Usage: node scripts/assemble-static-export.mjs <manual-dir> <new-destination>
import { createHash } from 'node:crypto'
import { copyFile, lstat, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

// Package-only Pages rules. Noindex mirrors the default until indexing is
// approved. Nothing is marked immutable: the search index
// (_next/static/chunks/nextra-data-<locale>.json) keeps one name across builds.
export const HEADERS = `/*
  X-Robots-Tag: noindex, nofollow
`
// Current Cloudflare Pages limits (Free plan, Wrangler Direct Upload).
const MAX_FILES = 20000
const MAX_FILE_BYTES = 25 * 1024 * 1024
const REFUSED = /^(?:_headers|_redirects|_routes\.json|_worker\.js|functions|\.env.*|.*\.map)$/

async function walk(dir, rel = '') {
  const files = []
  for (const entry of (await readdir(dir)).sort()) {
    const full = path.join(dir, entry)
    const name = rel ? `${rel}/${entry}` : entry
    const info = await lstat(full)
    if (info.isSymbolicLink()) throw new Error(`Symlink refused: ${name}`)
    if (REFUSED.test(entry)) throw new Error(`Refused entry in export: ${name}`)
    if (info.isDirectory()) files.push(...(await walk(full, name)))
    else if (info.isFile()) files.push({ name, full, size: info.size })
    else throw new Error(`Not a regular file: ${name}`)
  }
  return files
}

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')

export async function assemble(source, destination) {
  if (!path.isAbsolute(source) || !path.isAbsolute(destination)) throw new Error('Use absolute paths')
  const out = path.join(source, 'out')
  const detail = JSON.parse(await readFile(path.join(source, '.next/export-detail.json'), 'utf8'))
  if (detail.success !== true || detail.outDirectory !== out) throw new Error('Last build was not a successful static export into out/')
  const buildId = (await readFile(path.join(source, '.next/BUILD_ID'), 'utf8')).trim()
  if (!/^[A-Za-z0-9_-]+$/.test(buildId)) throw new Error('Required build ID')
  if (path.relative(source, destination).split(path.sep)[0] !== '..') throw new Error('Destination must be outside the manual directory')
  await mkdir(destination) // fails if it already exists

  const files = await walk(out)
  const names = new Set(files.map((f) => f.name))
  // Without a top-level 404.html Pages answers every unknown path with / (SPA fallback).
  for (const required of ['index.html', '404.html', `_next/static/${buildId}/_buildManifest.js`, '_next/static/chunks/nextra-data-en-US.json']) {
    if (!names.has(required)) throw new Error(`Required file missing from export: ${required}`)
  }
  for (const dir of await readdir(path.join(out, '_next/static'))) {
    if (!['chunks', 'css', 'media', buildId].includes(dir)) throw new Error(`Unexpected static entry (stale build?): ${dir}`)
  }
  if (files.length + 1 > MAX_FILES) throw new Error('Too many files for Pages')

  const site = path.join(destination, 'site')
  const manifest = []
  for (const file of files) {
    if (file.size > MAX_FILE_BYTES) throw new Error(`File over Pages limit: ${file.name}`)
    const target = path.join(site, file.name)
    await mkdir(path.dirname(target), { recursive: true })
    await copyFile(file.full, target)
    manifest.push({ path: file.name, bytes: file.size, sha256: sha256(await readFile(target)) })
  }
  await writeFile(path.join(site, '_headers'), HEADERS)
  manifest.push({ path: '_headers', bytes: Buffer.byteLength(HEADERS), sha256: sha256(HEADERS) })
  manifest.sort((a, b) => (a.path < b.path ? -1 : 1))
  const packageDigest = sha256(manifest.map((f) => `${f.sha256}  ${f.path}\n`).join(''))
  const record = { target: 'static-export', buildId, fileCount: manifest.length, totalBytes: manifest.reduce((n, f) => n + f.bytes, 0), packageDigest, files: manifest }
  await writeFile(path.join(destination, 'package-manifest.json'), `${JSON.stringify(record, null, 2)}\n`)
  return record
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [source, destination] = process.argv.slice(2)
  const record = await assemble(path.resolve(source ?? ''), path.resolve(destination ?? ''))
  console.log(`assembled static export ${record.buildId}: ${record.fileCount} files, ${record.totalBytes} bytes, packageDigest ${record.packageDigest}`)
}
