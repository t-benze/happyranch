/**
 * build-registry.ts — generate `src/design-system/registry.json` from every
 * `export const meta = { ... } as const;` literal under the design-system tree.
 *
 * The agent designer reads `registry.json` directly off disk (no daemon route,
 * no MCP sidecar) — see DESIGN_SYSTEM.md §9. The committed file MUST be a
 * deterministic byte-for-byte rebuild from the sources, so CI can detect drift
 * with `git diff --quiet src/design-system/registry.json`.
 *
 * Determinism rules:
 *   - components sorted by `name`
 *   - keys in each entry emitted in a fixed order
 *   - no `generatedAt` timestamp (volatile)
 *   - trailing newline
 *
 * The `meta` literal is parsed by regex + `Function(...)` eval. The format is
 * deliberately constrained: a single object literal with primitive values,
 * arrays of primitives, and plain string keys. No spreads, no template
 * literals with interpolation, no comments inside the object. The match regex
 * is non-greedy on `}` so it stops at the first balanced brace.
 */
import { readdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = "src/design-system";
const SCAN_DIRS = ["primitives", "patterns", "layouts"];

interface MetaEntry {
  name: string;
  layer: "primitive" | "pattern" | "layout";
  import: string;
  variants: Record<string, readonly string[]>;
  consumes: readonly string[];
  example: string;
}

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      out.push(...walk(path));
    } else if (path.endsWith(".tsx")) {
      out.push(path);
    }
  }
  return out;
}

function extractMeta(filePath: string): MetaEntry | null {
  const src = readFileSync(filePath, "utf8");
  // Non-greedy on the closing brace; the meta literal must be a single
  // object expression with no nested unbalanced braces.
  const match = src.match(/export const meta\s*=\s*(\{[\s\S]*?\})\s*as const;/);
  if (!match) return null;
  try {
    // The source files are ours, the format is constrained — see header.
    const value = Function(
      `"use strict"; return (${match[1]});`,
    )() as MetaEntry;
    return value;
  } catch (err) {
    throw new Error(
      `Failed to parse meta block in ${filePath}: ${(err as Error).message}`,
    );
  }
}

function emitEntry(entry: MetaEntry): Record<string, unknown> {
  // Stable key order for deterministic JSON serialization.
  return {
    name: entry.name,
    layer: entry.layer,
    import: entry.import,
    variants: entry.variants,
    consumes: entry.consumes,
    example: entry.example,
  };
}

function main(): void {
  const files: string[] = [];
  for (const sub of SCAN_DIRS) {
    const dir = join(ROOT, sub);
    try {
      statSync(dir);
    } catch {
      continue;
    }
    files.push(...walk(dir));
  }

  const entries: MetaEntry[] = [];
  for (const file of files.sort()) {
    const meta = extractMeta(file);
    if (!meta) continue;
    entries.push(meta);
  }

  entries.sort((a, b) => a.name.localeCompare(b.name));

  const doc = {
    version: 1,
    components: entries.map(emitEntry),
  };

  const outPath = join(ROOT, "registry.json");
  writeFileSync(outPath, JSON.stringify(doc, null, 2) + "\n");
  // eslint-disable-next-line no-console
  console.log(
    `✓ wrote ${relative(process.cwd(), outPath)} (${entries.length} components)`,
  );
}

main();
