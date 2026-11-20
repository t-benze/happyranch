import { defineConfig } from 'vite';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { writeResourceManifest } from './resource-manifest';

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
      writeFileSync(path.join(outDir, 'HankenGrotesk-OFL.txt'),
        readFileSync(path.resolve(root, '../public/fonts/HankenGrotesk-OFL.txt')));
      writeResourceManifest(outDir);
    },
  }],
  build: { outDir, emptyOutDir: true, modulePreload: false, assetsInlineLimit: 0,
    sourcemap: false, target: 'es2022' },
});
