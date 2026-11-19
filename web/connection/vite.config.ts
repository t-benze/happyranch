import { defineConfig } from 'vite';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
const outDir = path.resolve(root, '../dist-connection');
const tokenFile = path.resolve(root, '../src/design-system/tokens/tokens.css');
// Import only literal semantic values from the shared pure token authority.
const tokenNames = ['surface-canvas', 'surface-raised', 'text-primary', 'text-secondary',
  'border-default', 'border-strong', 'accent-text', 'accent-default', 'surface-sunken', 'text-inverse',
  'accent-soft', 'attention-soft', 'attention-text', 'info-soft', 'info', 'danger-soft', 'danger'];
export default defineConfig({
  root, base: './', publicDir: false,
  plugins: [{
    name: 'connection-assets',
    resolveId(id) { if (id === 'connection-tokens.css') return '\0connection-tokens.css'; },
    load(id) {
      if (id !== '\0connection-tokens.css') return;
      const source = readFileSync(tokenFile, 'utf8');
      const declarations = tokenNames.map((name) => {
        const match = source.match(new RegExp(`--color-${name}:\\s*([^;]+);`));
        if (!match) throw new Error(`Missing connection token: ${name}`);
        return `--color-${name}: ${match[1]};`;
      });
      return `:root {${declarations.join('\n')}}`;
    },
    writeBundle() {
      const resources: { path: string; bytes: number; sha256: string }[] = [];
      function inventory(directory: string): void {
        for (const name of readdirSync(directory).sort()) {
          const absolute = path.join(directory, name);
          if (statSync(absolute).isDirectory()) inventory(absolute);
          else {
            const bytes = readFileSync(absolute);
            resources.push({ path: path.relative(outDir, absolute).split(path.sep).join('/'),
              bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
          }
        }
      }
      writeFileSync(path.join(outDir, 'HankenGrotesk-OFL.txt'),
        readFileSync(path.resolve(root, '../public/fonts/HankenGrotesk-OFL.txt')));
      inventory(outDir);
      writeFileSync(path.join(outDir, 'resource-manifest.json'), JSON.stringify({
        version: 1, entry: 'index.html', resources,
      }, null, 2) + '\n');
    },
  }],
  build: { outDir, emptyOutDir: true, modulePreload: false, assetsInlineLimit: 0,
    sourcemap: false, target: 'es2022' },
});
