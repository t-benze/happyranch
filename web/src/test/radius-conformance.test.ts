import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const designSystemRoot = join(here, '../design-system');

const REFERENCE_SHA256 = '87e23c25c95b22bdd46570314f6c649667d787ef89128504dbb52905cd52045a';
const EXPECTED_RADIUS = 'rounded-sm';

type RadiusRow = {
  id: string;
  component: string;
  referenceSelector: string;
  source: string;
  startPattern: string;
  endPattern: string;
};

/**
 * Normative denominator recovered from the founder HTML identified by
 * REFERENCE_SHA256. Reference selectors and expected values are deliberately
 * separate from the production classes read below.
 */
const RADIUS_CONTRACT: readonly RadiusRow[] = [
  { id: 'button', component: 'Button', referenceSelector: '.btn', source: 'primitives/Button.tsx', startPattern: 'const\\s+buttonVariants\\s*=\\s*cva\\s*\\(', endPattern: '\\bvariants\\s*:' },
  { id: 'input', component: 'Input', referenceSelector: '.inp', source: 'primitives/Input.tsx', startPattern: '<input\\b', endPattern: '\\bref\\s*=\\s*\\{\\s*ref\\s*\\}' },
  { id: 'textarea', component: 'Textarea', referenceSelector: '.ta', source: 'primitives/Textarea.tsx', startPattern: '<textarea\\b', endPattern: '\\bref\\s*=\\s*\\{\\s*ref\\s*\\}' },
  { id: 'select-trigger', component: 'SelectTrigger', referenceSelector: 'select.inp', source: 'primitives/Select.tsx', startPattern: 'const\\s+SelectTrigger\\s*=', endPattern: 'SelectTrigger\\.displayName' },
  { id: 'tooltip-content', component: 'TooltipContent', referenceSelector: '.tip::after', source: 'primitives/Tooltip.tsx', startPattern: 'const\\s+TooltipContent\\s*=', endPattern: 'TooltipContent\\.displayName' },
  { id: 'mention-textarea', component: 'MentionTextarea', referenceSelector: '.ta', source: 'patterns/MentionTextarea.tsx', startPattern: 'const\\s+DEFAULT_CLASSNAME\\s*=', endPattern: 'export\\s+interface\\s+MentionTextareaProps' },
  { id: 'sidebar-footer-account', component: 'Sidebar footer account row', referenceSelector: '.nav-item', source: 'layouts/AppShell/Sidebar.tsx', startPattern: 'aria-label\\s*=\\s*"Account: You, Founder"', endPattern: '<span\\s+aria-hidden\\s*=\\s*"true"' },
  { id: 'sidebar-nav-disabled', component: 'SidebarNavItem disabled branch', referenceSelector: '.nav-item', source: 'layouts/AppShell/Sidebar.tsx', startPattern: 'if\\s*\\(\\s*!enabled\\s*\\)', endPattern: 'aria-disabled\\s*=\\s*"true"' },
  { id: 'sidebar-nav-enabled', component: 'SidebarNavItem enabled branch', referenceSelector: '.nav-item', source: 'layouts/AppShell/Sidebar.tsx', startPattern: 'return\\s*\\(\\s*<NavLink\\b', endPattern: '<Icon\\s+size\\s*=\\s*\\{16\\}' },
] as const;

const AUTHORITATIVE_REFERENCE_SELECTORS = new Map<string, string>([
  ['button', '.btn'],
  ['input', '.inp'],
  ['textarea', '.ta'],
  ['select-trigger', 'select.inp'],
  ['tooltip-content', '.tip::after'],
  ['mention-textarea', '.ta'],
  ['sidebar-footer-account', '.nav-item'],
  ['sidebar-nav-disabled', '.nav-item'],
  ['sidebar-nav-enabled', '.nav-item'],
]);

/** Exact pre-correction debt. This map must strictly shrink as rows conform. */
const KNOWN_MISMATCHES = new Map<string, string>([
  ['input', 'rounded-md'],
  ['textarea', 'rounded-md'],
  ['select-trigger', 'rounded-md'],
  ['tooltip-content', 'rounded-md'],
  ['mention-textarea', 'rounded-md'],
  ['sidebar-footer-account', 'rounded'],
  ['sidebar-nav-disabled', 'rounded'],
  ['sidebar-nav-enabled', 'rounded'],
]);

/** Closed documentation-only exclusions. These are never scanned as rows. */
const NON_DENOMINATOR_EXCLUSIONS = {
  'approved-pills': 'Pills remain approved status, badge, chip, and tab geometry.',
  dialog: 'Dialog remains 18px.',
  'dropdown-menu': 'DropdownMenu remains a 12px shell with 8px items.',
  'message-bubble': 'MessageBubble remains 18px.',
  'typing-bubble': 'TypingBubble remains 18px.',
  composer: 'Composer is a later local extension from 24px to documented 18px.',
  'inbox-row': 'InboxRow is a later interactive mapping to 8px.',
} as const;

function observedRadius(row: RadiusRow): string {
  const source = readFileSync(join(designSystemRoot, row.source), 'utf8');
  const startMatch = new RegExp(row.startPattern).exec(source);
  if (!startMatch) throw new Error(`${row.id}: missing start anchor in ${row.source}`);
  const start = startMatch.index;
  const endMatch = new RegExp(row.endPattern).exec(source.slice(start + startMatch[0].length));
  if (!endMatch) throw new Error(`${row.id}: missing end anchor in ${row.source}`);
  const end = start + startMatch[0].length + endMatch.index;
  const tokens = source.slice(start, end).match(/\brounded(?:-[a-z0-9[\].]+)?\b/g) ?? [];
  if (tokens.length !== 1) throw new Error(`${row.id}: expected exactly one radius class, found ${tokens.join(', ') || 'none'}`);
  return tokens[0];
}

function assertRadiusContract(
  rows: readonly RadiusRow[],
  baseline: ReadonlyMap<string, string>,
  observations: ReadonlyMap<string, string>,
): void {
  const ids = rows.map(({ id }) => id);
  if (rows.length !== 9 || new Set(ids).size !== 9) throw new Error('radius denominator must contain exactly 9 unique rows');
  if (ids.some((id) => !AUTHORITATIVE_REFERENCE_SELECTORS.has(id)) || AUTHORITATIVE_REFERENCE_SELECTORS.size !== ids.length) {
    throw new Error('radius denominator has a missing or additional identity');
  }

  for (const row of rows) {
    if (AUTHORITATIVE_REFERENCE_SELECTORS.get(row.id) !== row.referenceSelector) {
      throw new Error(`${row.id}: authoritative reference selector mapping changed`);
    }
    const observed = observations.get(row.id);
    if (!observed) throw new Error(`${row.id}: production observation missing`);
    const frozenWrongValue = baseline.get(row.id);
    if (observed === EXPECTED_RADIUS) {
      if (frozenWrongValue) throw new Error(`${row.id}: stale mismatch baseline must be deleted after correction`);
    } else if (!frozenWrongValue) {
      throw new Error(`${row.id}: current mismatch omitted from baseline`);
    } else if (frozenWrongValue !== observed) {
      throw new Error(`${row.id}: observed mismatch drifted from ${frozenWrongValue} to ${observed}`);
    }
  }

  for (const id of baseline.keys()) {
    if (!ids.includes(id)) throw new Error(`${id}: baseline entry has no denominator row`);
  }
}

const productionObservations = new Map(RADIUS_CONTRACT.map((row) => [row.id, observedRadius(row)]));

describe('Pasture radius conformance contract', () => {
  test('pins the independently verified founder reference and the closed denominator', () => {
    expect(REFERENCE_SHA256).toBe('87e23c25c95b22bdd46570314f6c649667d787ef89128504dbb52905cd52045a');
    expect(RADIUS_CONTRACT).toHaveLength(9);
    expect(KNOWN_MISMATCHES).toHaveLength(8);
    expect(KNOWN_MISMATCHES.has('button')).toBe(false);
    expect(() => assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, productionObservations)).not.toThrow();
  });

  test('requires strict baseline shrink when a simulated phase-two correction lands', () => {
    const corrected = new Map(productionObservations);
    corrected.set('input', EXPECTED_RADIUS);
    expect(() => assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, corrected)).toThrow(/stale mismatch baseline/);

    const shrunkenBaseline = new Map(KNOWN_MISMATCHES);
    shrunkenBaseline.delete('input');
    expect(() => assertRadiusContract(RADIUS_CONTRACT, shrunkenBaseline, corrected)).not.toThrow();
  });

  test('fails closed on mapping, drift, omission, and identity changes', () => {
    const remapped = RADIUS_CONTRACT.map((row) => row.id === 'input' ? { ...row, referenceSelector: '.not-inp' } : row);
    expect(() => assertRadiusContract(remapped, KNOWN_MISMATCHES, productionObservations)).toThrow(/selector mapping changed/);

    const drifted = new Map(productionObservations);
    drifted.set('input', 'rounded-lg');
    expect(() => assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, drifted)).toThrow(/observed mismatch drifted/);

    const omitted = new Map(KNOWN_MISMATCHES);
    omitted.delete('input');
    expect(() => assertRadiusContract(RADIUS_CONTRACT, omitted, productionObservations)).toThrow(/mismatch omitted/);

    expect(() => assertRadiusContract([...RADIUS_CONTRACT.slice(0, -1), RADIUS_CONTRACT[0]], KNOWN_MISMATCHES, productionObservations)).toThrow(/9 unique rows/);
  });

  test('keeps exclusions closed, documented, and outside the conformance denominator', () => {
    expect(Object.keys(NON_DENOMINATOR_EXCLUSIONS)).toEqual([
      'approved-pills',
      'dialog',
      'dropdown-menu',
      'message-bubble',
      'typing-bubble',
      'composer',
      'inbox-row',
    ]);
    expect(RADIUS_CONTRACT.every(({ id }) => !(id in NON_DENOMINATOR_EXCLUSIONS))).toBe(true);
  });
});
