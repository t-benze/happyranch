// Assemble the optional static export (HAPPYRANCH_BUILD_TARGET=static-export)
// into a static-host package: <destination>/site/ is the only directory to
// upload or serve; <destination>/package-manifest.json sits beside it.
// Usage: node scripts/assemble-static-export.mjs <manual-dir> <new-destination>
// Manifest format matches the website's static package (happyranch-services
// scripts/assemble-static-export.mjs): paths relative to the package root,
// packageDigest = sha256 of the JSON payload without that field.
import { createHash } from 'node:crypto'
import { copyFile, lstat, mkdir, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Package-only Pages rules. Noindex mirrors the default until indexing is
// approved. Nothing is marked immutable: the search index
// (_next/static/chunks/nextra-data-<locale>.json) keeps one name across builds.
export const HEADERS = `/*
  X-Robots-Tag: noindex, nofollow
`
// Current Cloudflare Pages limits (Free plan, Wrangler Direct Upload).
const MAX_FILES = 20000
const MAX_FILE_BYTES = 25 * 1024 * 1024
const HOSTING_FILES = /^(?:_headers|_redirects|_routes\.json|_worker\.js|functions)$/
// Same secret/workspace names as the website's assemblers, plus source maps.
const forbidden = (name) =>
  /^(?:\.env(?:\..*)?|\.git|\.claude|\.happyranch|\.aws|\.ssh|\.npmrc|\.netrc|\.worktree-guard\.json)$/i.test(name) ||
  /\.(?:pem|key|p12|pfx|map)$/i.test(name)
const STATIC_DIRS = /^(?:chunks|css|media)$/
const NEXT_PAGES = new Set(['/_app', '/_document', '/_error'])

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')
const within = (parent, child) => child === parent || child.startsWith(parent + path.sep)

// Literal path validation: every existing ancestor must be a real directory,
// never a symlink. This does not guard against a concurrent rename.
async function checkParents(location) {
  let current = path.parse(location).root
  for (const segment of location.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment)
    const info = await lstat(current)
    if (info.isSymbolicLink()) throw new Error(`Unsafe symlink path: ${current}`)
    if (!info.isDirectory()) throw new Error(`Required directory: ${current}`)
  }
}

async function regularFile(location, label) {
  await checkParents(path.dirname(location))
  const info = await lstat(location).catch(() => null)
  if (!info?.isFile() || info.isSymbolicLink()) throw new Error(`Required regular file: ${label}`)
  return readFile(location)
}

// Every name below the (already checked) root is refused if it is a symlink,
// a secret/workspace name or not a regular file or directory.
async function walk(dir, rel = '') {
  const files = []
  for (const entry of (await readdir(dir)).sort()) {
    const full = path.join(dir, entry)
    const name = rel ? `${rel}/${entry}` : entry
    const info = await lstat(full)
    if (info.isSymbolicLink()) throw new Error(`Symlink refused: ${name}`)
    if (forbidden(entry)) throw new Error(`Forbidden input name: ${name}`)
    if (info.isDirectory()) files.push(...(await walk(full, name)))
    else if (info.isFile()) files.push({ name, full })
    else throw new Error(`Not a regular file: ${name}`)
  }
  return files
}

// The expected export is derived from the selected build in .next, never from
// out/ itself: its page manifest (static HTML pages), its prerender manifest
// (getStaticProps routes, which must all be the Nextra _meta 404s) and every
// .next/static asset. The manual has no public/ directory; one is refused.
async function expectedExport(root, next, buildId) {
  const expected = new Map()
  const expect = (name, from) => {
    if (expected.has(name)) throw new Error(`Conflicting export input: ${name}`)
    expected.set(name, from)
  }
  if (await lstat(path.join(root, 'public')).catch(() => null)) throw new Error('Unexpected public/ input: extend the assembler first')
  const prerender = JSON.parse(await regularFile(path.join(next, 'prerender-manifest.json'), '.next/prerender-manifest.json'))
  if (Object.keys(prerender.dynamicRoutes ?? {}).length > 0) throw new Error('Unsupported export shape: dynamic routes')
  const notFound = new Set(prerender.notFoundRoutes ?? [])
  for (const route of Object.keys(prerender.routes ?? {})) {
    if (!notFound.has(route)) throw new Error(`Unsupported export shape: getStaticProps page ${route}`)
  }
  const pages = JSON.parse(await regularFile(path.join(next, 'server/pages-manifest.json'), '.next/server/pages-manifest.json'))
  await checkParents(path.join(next, 'server/pages'))
  for (const [route, file] of Object.entries(pages)) {
    if (NEXT_PAGES.has(route) || notFound.has(route)) continue
    const name = route === '/' ? 'index.html' : `${route.slice(1)}.html`
    if (file !== `pages/${name}`) throw new Error(`Unsupported export shape: ${route} is not a static HTML page`)
    for (const segment of name.split('/')) if (forbidden(segment) || segment === '..') throw new Error(`Forbidden input name: ${name}`)
    const from = path.join(next, 'server', file)
    // Every page must carry the declared build ID and no other.
    const html = (await regularFile(from, `.next/server/${file}`)).toString('utf8')
    const data = html.match(/<script id="__NEXT_DATA__" type="application\/json">(.*?)<\/script>/s)
    const ids = [...html.matchAll(/"buildId":"([^"]*)"/g)].map((match) => match[1])
    if (!data || ids.length !== 1 || JSON.parse(data[1]).buildId !== buildId || ids[0] !== buildId) {
      throw new Error(`Build ID mismatch in ${name}: expected ${buildId}, found ${[...new Set(ids)].join(', ') || 'none'}`)
    }
    expect(name, from)
  }
  // Without a top-level 404.html Pages answers every unknown path with / (SPA fallback).
  for (const required of ['index.html', '404.html']) if (!expected.has(required)) throw new Error(`Required page missing from build: ${required}`)

  const statics = path.join(next, 'static')
  await checkParents(statics)
  for (const name of await readdir(statics)) {
    if (!STATIC_DIRS.test(name) && name !== buildId) throw new Error(`Build output has another build ID or unexpected static entry: ${name}`)
  }
  for (const name of ['_buildManifest.js', '_ssgManifest.js']) {
    if ((await regularFile(path.join(statics, buildId, name), `.next/static/${buildId}/${name}`)).length === 0) throw new Error(`Empty build manifest: ${name}`)
  }
  for (const file of await walk(statics)) expect(`_next/static/${file.name}`, file.full)
  if (!expected.has('_next/static/chunks/nextra-data-en-US.json')) throw new Error('Required search index missing from build: _next/static/chunks/nextra-data-en-US.json')
  return expected
}

export async function assemble(source, destination) {
  if (!path.isAbsolute(source) || !path.isAbsolute(destination)) throw new Error('Use absolute paths')
  const root = path.normalize(source)
  const output = path.normalize(destination)
  await checkParents(root)
  if (within(root, output) || within(output, root)) throw new Error('Destination must be outside the manual directory and its ancestors')
  await checkParents(path.dirname(output))
  const existing = await lstat(output).catch((error) => {
    if (error.code === 'ENOENT') return null
    throw error
  })
  if (existing) throw new Error('Destination already exists; choose a new empty location')

  // The most recent `next build` must have been the static export into <root>/out;
  // a later standalone build removes this record.
  const out = path.join(root, 'out')
  const next = path.join(root, '.next')
  const detail = JSON.parse(await regularFile(path.join(next, 'export-detail.json'), '.next/export-detail.json'))
  if (detail.success !== true || detail.outDirectory !== out) throw new Error('Last build was not a successful static export into out/')
  const marker = JSON.parse(await regularFile(path.join(next, 'export-marker.json'), '.next/export-marker.json'))
  if (marker.hasExportPathMap !== false || marker.exportTrailingSlash !== false) throw new Error('Unsupported export options (exportPathMap or trailingSlash)')
  const buildId = (await regularFile(path.join(next, 'BUILD_ID'), '.next/BUILD_ID')).toString('utf8').trim()
  if (!/^[A-Za-z0-9_-]+$/.test(buildId)) throw new Error('Required build ID')

  // Name policy first, so a refused name is reported as such, then out/ must
  // equal the expected export exactly: no missing, extra, stale or changed file.
  await checkParents(out)
  const inputs = await walk(out)
  for (const file of inputs) {
    if (HOSTING_FILES.test(file.name.split('/')[0])) throw new Error(`Refused hosting file in export: ${file.name}`)
  }
  const expected = await expectedExport(root, next, buildId)
  for (const file of inputs) if (!expected.has(file.name)) throw new Error(`Unexpected export file (not in this build): ${file.name}`)
  const present = new Set(inputs.map((file) => file.name))
  const missing = [...expected.keys()].filter((name) => !present.has(name))
  if (missing.length > 0) throw new Error(`Export is missing build files: ${missing.join(', ')}`)
  if (inputs.length + 1 > MAX_FILES) throw new Error('Too many files for Pages')

  // Read and hash the complete payload before creating the destination.
  const files = []
  for (const file of inputs) {
    const bytes = await readFile(file.full)
    if (bytes.length > MAX_FILE_BYTES) throw new Error(`File over Pages limit: ${file.name}`)
    if (!bytes.equals(await readFile(expected.get(file.name)))) throw new Error(`Export does not match its build bytes: ${file.name}`)
    files.push({ path: `site/${file.name}`, bytes: bytes.length, sha256: sha256(bytes) })
  }
  const headers = Buffer.from(HEADERS)
  files.push({ path: 'site/_headers', bytes: headers.length, sha256: sha256(headers) })
  files.sort((a, b) => (a.path < b.path ? -1 : 1))
  const payload = { format: 1, target: 'static-export', buildId, files }
  const record = { ...payload, packageDigest: sha256(JSON.stringify(payload)) }

  await mkdir(output) // Exclusive reservation; never copy over an existing directory.
  try {
    for (const file of inputs) {
      const target = path.join(output, 'site', file.name)
      await mkdir(path.dirname(target), { recursive: true })
      await copyFile(file.full, target, 1 /* COPYFILE_EXCL */)
    }
    await writeFile(path.join(output, 'site/_headers'), headers, { flag: 'wx' })
    // Detect input changes during assembly rather than publishing a mixed build.
    for (const file of files) {
      if (sha256(await readFile(path.join(output, file.path))) !== file.sha256) throw new Error(`Input changed during assembly: ${file.path}`)
    }
    // Outside site/: the Pages upload directory serves every file it contains.
    await writeFile(path.join(output, 'package-manifest.json'), `${JSON.stringify(record, null, 2)}\n`, { flag: 'wx' })
  } catch (error) {
    await rm(output, { recursive: true }) // Only the directory this invocation created.
    throw error
  }
  return record
}

if (process.argv[1] && (await realpath(process.argv[1])) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 4) throw new Error('Usage: node scripts/assemble-static-export.mjs /absolute/manual-dir /absolute/new-package')
    const record = await assemble(process.argv[2], process.argv[3])
    const bytes = record.files.reduce((n, f) => n + f.bytes, 0)
    console.log(JSON.stringify({ target: record.target, buildId: record.buildId, packageDigest: record.packageDigest, files: record.files.length, bytes }))
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}
