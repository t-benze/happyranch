import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';

/** Inventory emitted files, including public assets, without listing the manifest itself. */
export function writeResourceManifest(outDir: string): void {
  const resources: { path: string; bytes: number; sha256: string }[] = [];
  function inventory(directory: string): void {
    for (const name of readdirSync(directory).sort()) {
      const absolute = path.join(directory, name);
      if (statSync(absolute).isDirectory()) inventory(absolute);
      else {
        const relative = path.relative(outDir, absolute).split(path.sep).join('/');
        if (relative === 'resource-manifest.json') continue;
        const bytes = readFileSync(absolute);
        resources.push({ path: relative, bytes: bytes.length,
          sha256: createHash('sha256').update(bytes).digest('hex') });
      }
    }
  }
  inventory(outDir);
  resources.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  writeFileSync(path.join(outDir, 'resource-manifest.json'), JSON.stringify({
    version: 1, entry: 'index.html', resources,
  }, null, 2) + '\n');
}
