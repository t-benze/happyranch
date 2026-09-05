import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const designSystemRoot = join(here, "../design-system");
const tokenSource = join(designSystemRoot, "tokens/tokens.css");
const REFERENCE_SHA256 =
  "87e23c25c95b22bdd46570314f6c649667d787ef89128504dbb52905cd52045a";
const EXPECTED_DENOMINATOR = 23;

type RadiusClass =
  | "rounded"
  | "rounded-sm"
  | "rounded-md"
  | "rounded-lg"
  | "rounded-3xl"
  | "rounded-full";
type RadiusToken = "--radius-sm" | "--radius" | "--radius-lg" | "--radius-pill";
type RadiusRow = {
  id: string;
  component: string;
  region: string;
  referenceSelector: string;
  expected: RadiusClass;
  token: RadiusToken;
  source: string;
  startPattern: string;
  endPattern: string;
};

/**
 * Concrete component/region denominator from the verified founder HTML and
 * seq. 61 local mappings. Expected values never derive from production.
 */
const RADIUS_CONTRACT: readonly RadiusRow[] = [
  {
    id: "button",
    component: "Button",
    region: "base and icon variants",
    referenceSelector: ".btn",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/Button.tsx",
    startPattern: "const\\s+buttonVariants\\s*=\\s*cva\\s*\\(",
    endPattern: "\\bvariants\\s*:",
  },
  {
    id: "input",
    component: "Input",
    region: "control",
    referenceSelector: ".inp",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/Input.tsx",
    startPattern: "<input\\b",
    endPattern: "\\bref\\s*=\\s*\\{\\s*ref\\s*\\}",
  },
  {
    id: "textarea",
    component: "Textarea",
    region: "control",
    referenceSelector: ".ta",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/Textarea.tsx",
    startPattern: "<textarea\\b",
    endPattern: "\\bref\\s*=\\s*\\{\\s*ref\\s*\\}",
  },
  {
    id: "select-trigger",
    component: "SelectTrigger",
    region: "trigger",
    referenceSelector: "select.inp",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/Select.tsx",
    startPattern: "const\\s+SelectTrigger\\s*=",
    endPattern: "SelectTrigger\\.displayName",
  },
  {
    id: "tooltip-content",
    component: "TooltipContent",
    region: "content",
    referenceSelector: ".tip::after",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/Tooltip.tsx",
    startPattern: "const\\s+TooltipContent\\s*=",
    endPattern: "TooltipContent\\.displayName",
  },
  {
    id: "mention-textarea",
    component: "MentionTextarea",
    region: "control",
    referenceSelector: ".ta",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "patterns/MentionTextarea.tsx",
    startPattern: "const\\s+DEFAULT_CLASSNAME\\s*=",
    endPattern: "export\\s+interface\\s+MentionTextareaProps",
  },
  {
    id: "sidebar-footer-account",
    component: "Sidebar footer account row",
    region: "interactive row",
    referenceSelector: ".nav-item",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "layouts/AppShell/Sidebar.tsx",
    startPattern: 'aria-label\\s*=\\s*"Account: You, Founder"',
    endPattern: '<span\\s+aria-hidden\\s*=\\s*"true"',
  },
  {
    id: "sidebar-nav-disabled",
    component: "SidebarNavItem disabled branch",
    region: "interactive row",
    referenceSelector: ".nav-item",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "layouts/AppShell/Sidebar.tsx",
    startPattern: "if\\s*\\(\\s*!enabled\\s*\\)",
    endPattern: 'aria-disabled\\s*=\\s*"true"',
  },
  {
    id: "sidebar-nav-enabled",
    component: "SidebarNavItem enabled branch",
    region: "interactive row",
    referenceSelector: ".nav-item",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "layouts/AppShell/Sidebar.tsx",
    startPattern: "return\\s*\\(\\s*<NavLink\\b",
    endPattern: "<Icon\\s+size\\s*=\\s*\\{16\\}",
  },
  {
    id: "status-badge",
    component: "StatusBadge",
    region: "pill shell",
    referenceSelector: ".tag",
    expected: "rounded-full",
    token: "--radius-pill",
    source: "patterns/StatusBadge.tsx",
    startPattern: "<span\\s+className=\\{\`text-mono-sm",
    endPattern: "\\$\\{cls\\}\`\\}",
  },
  {
    id: "agent-chip",
    component: "AgentChip",
    region: "role indicator",
    referenceSelector: ".tag .led",
    expected: "rounded-full",
    token: "--radius-pill",
    source: "patterns/AgentChip.tsx",
    startPattern: 'aria-hidden="true"',
    endPattern: "\\$\\{DOT_BG\\[role\\]\\}\`",
  },
  {
    id: "tabs-segmented-shell",
    component: "Tabs",
    region: "segmented shell",
    referenceSelector: ".seg",
    expected: "rounded-full",
    token: "--radius-pill",
    source: "primitives/Tabs.tsx",
    startPattern: "segmented:\\s*",
    endPattern: "\\n\\s*\\}",
  },
  {
    id: "tabs-segmented-trigger",
    component: "Tabs",
    region: "segmented trigger",
    referenceSelector: ".seg button",
    expected: "rounded-full",
    token: "--radius-pill",
    source: "primitives/Tabs.tsx",
    startPattern: "segmented:\\s*\\n\\s*'text-text-muted",
    endPattern: "\\n\\s*\\}",
  },
  {
    id: "dialog-content",
    component: "Dialog",
    region: "content shell",
    referenceSelector: ".modal",
    expected: "rounded-lg",
    token: "--radius-lg",
    source: "primitives/Dialog.tsx",
    startPattern: "<DialogPrimitive\\.Content",
    endPattern: "\\{children\\}",
  },
  {
    id: "dropdown-subcontent-shell",
    component: "DropdownMenu",
    region: "subcontent shell",
    referenceSelector: ".popover",
    expected: "rounded-md",
    token: "--radius",
    source: "primitives/DropdownMenu.tsx",
    startPattern: "const\\s+DropdownMenuSubContent",
    endPattern: "DropdownMenuSubContent\\.displayName",
  },
  {
    id: "dropdown-content-shell",
    component: "DropdownMenu",
    region: "content shell",
    referenceSelector: ".popover",
    expected: "rounded-md",
    token: "--radius",
    source: "primitives/DropdownMenu.tsx",
    startPattern: "const\\s+DropdownMenuContent",
    endPattern: "DropdownMenuContent\\.displayName",
  },
  {
    id: "dropdown-subtrigger",
    component: "DropdownMenu",
    region: "subtrigger item",
    referenceSelector: ".popover .opt",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/DropdownMenu.tsx",
    startPattern: "const\\s+DropdownMenuSubTrigger",
    endPattern: "DropdownMenuSubTrigger\\.displayName",
  },
  {
    id: "dropdown-item",
    component: "DropdownMenu",
    region: "ordinary item",
    referenceSelector: ".popover .opt",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/DropdownMenu.tsx",
    startPattern: "const\\s+DropdownMenuItem",
    endPattern: "DropdownMenuItem\\.displayName",
  },
  {
    id: "dropdown-checkbox-item",
    component: "DropdownMenu",
    region: "checkbox item",
    referenceSelector: ".popover .opt",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "primitives/DropdownMenu.tsx",
    startPattern: "const\\s+DropdownMenuCheckboxItem",
    endPattern: "DropdownMenuCheckboxItem\\.displayName",
  },
  {
    id: "message-bubble",
    component: "MessageBubble",
    region: "founder, worker, manager, and decline shells",
    referenceSelector: ".hr-bubble",
    expected: "rounded-lg",
    token: "--radius-lg",
    source: "patterns/MessageBubble.tsx",
    startPattern: "const\\s+VARIANT_CONTAINER",
    endPattern: "system:\\s*''",
  },
  {
    id: "typing-bubble",
    component: "TypingBubble",
    region: "bubble shell",
    referenceSelector: ".reply-bubble, .typing",
    expected: "rounded-lg",
    token: "--radius-lg",
    source: "patterns/TypingBubble.tsx",
    startPattern: '<div\\s+className="border-border-default',
    endPattern: ">",
  },
  {
    id: "composer",
    component: "Composer",
    region: "interactive shell",
    referenceSelector: ".composer (seq. 61 local extension)",
    expected: "rounded-lg",
    token: "--radius-lg",
    source: "patterns/Composer.tsx",
    startPattern:
      '<div\\s+className="border-border-default bg-surface-raised focus-within',
    endPattern: ">",
  },
  {
    id: "inbox-row",
    component: "InboxRow",
    region: "interactive shell",
    referenceSelector: ".thread (seq. 61 interactive mapping)",
    expected: "rounded-sm",
    token: "--radius-sm",
    source: "patterns/InboxRow.tsx",
    startPattern: "const\\s+shellCls\\s*=",
    endPattern: "\\$\\{",
  },
] as const;

const AUTHORITATIVE_ROWS = new Map<
  string,
  Pick<RadiusRow, "referenceSelector" | "expected" | "token">
>([
  [
    "button",
    { referenceSelector: ".btn", expected: "rounded-sm", token: "--radius-sm" },
  ],
  [
    "input",
    { referenceSelector: ".inp", expected: "rounded-sm", token: "--radius-sm" },
  ],
  [
    "textarea",
    { referenceSelector: ".ta", expected: "rounded-sm", token: "--radius-sm" },
  ],
  [
    "select-trigger",
    {
      referenceSelector: "select.inp",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "tooltip-content",
    {
      referenceSelector: ".tip::after",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "mention-textarea",
    { referenceSelector: ".ta", expected: "rounded-sm", token: "--radius-sm" },
  ],
  [
    "sidebar-footer-account",
    {
      referenceSelector: ".nav-item",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "sidebar-nav-disabled",
    {
      referenceSelector: ".nav-item",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "sidebar-nav-enabled",
    {
      referenceSelector: ".nav-item",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "status-badge",
    {
      referenceSelector: ".tag",
      expected: "rounded-full",
      token: "--radius-pill",
    },
  ],
  [
    "agent-chip",
    {
      referenceSelector: ".tag .led",
      expected: "rounded-full",
      token: "--radius-pill",
    },
  ],
  [
    "tabs-segmented-shell",
    {
      referenceSelector: ".seg",
      expected: "rounded-full",
      token: "--radius-pill",
    },
  ],
  [
    "tabs-segmented-trigger",
    {
      referenceSelector: ".seg button",
      expected: "rounded-full",
      token: "--radius-pill",
    },
  ],
  [
    "dialog-content",
    {
      referenceSelector: ".modal",
      expected: "rounded-lg",
      token: "--radius-lg",
    },
  ],
  [
    "dropdown-subcontent-shell",
    {
      referenceSelector: ".popover",
      expected: "rounded-md",
      token: "--radius",
    },
  ],
  [
    "dropdown-content-shell",
    {
      referenceSelector: ".popover",
      expected: "rounded-md",
      token: "--radius",
    },
  ],
  [
    "dropdown-subtrigger",
    {
      referenceSelector: ".popover .opt",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "dropdown-item",
    {
      referenceSelector: ".popover .opt",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "dropdown-checkbox-item",
    {
      referenceSelector: ".popover .opt",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
  [
    "message-bubble",
    {
      referenceSelector: ".hr-bubble",
      expected: "rounded-lg",
      token: "--radius-lg",
    },
  ],
  [
    "typing-bubble",
    {
      referenceSelector: ".reply-bubble, .typing",
      expected: "rounded-lg",
      token: "--radius-lg",
    },
  ],
  [
    "composer",
    {
      referenceSelector: ".composer (seq. 61 local extension)",
      expected: "rounded-lg",
      token: "--radius-lg",
    },
  ],
  [
    "inbox-row",
    {
      referenceSelector: ".thread (seq. 61 interactive mapping)",
      expected: "rounded-sm",
      token: "--radius-sm",
    },
  ],
]);
/** Exact pre-correction debt. This map must strictly shrink. */
const KNOWN_MISMATCHES = new Map<string, RadiusClass>([
  ["input", "rounded-md"],
  ["textarea", "rounded-md"],
  ["select-trigger", "rounded-md"],
  ["tooltip-content", "rounded-md"],
  ["mention-textarea", "rounded-md"],
  ["sidebar-footer-account", "rounded"],
  ["sidebar-nav-disabled", "rounded"],
  ["sidebar-nav-enabled", "rounded"],
  ["composer", "rounded-3xl"],
  ["inbox-row", "rounded-lg"],
]);
/** No remaining exclusion is allowed to make a production-radius claim. */
const NON_DENOMINATOR_EXCLUSIONS = new Map<string, string>();

function readRadiusToken(property: RadiusToken): string {
  const source = readFileSync(tokenSource, "utf8").replace(
    /\/\*[\s\S]*?\*\//g,
    "",
  );
  const definitions = [
    ...source.matchAll(/(?:^|\n)\s*(--[a-z0-9-]+)\s*:\s*([^;]+?)\s*;/g),
  ].filter((match) => match[1] === property);
  if (definitions.length !== 1)
    throw new Error(`${property}: expected exactly one token definition`);
  const value = definitions[0][2].trim();
  const alias = /^var\((--[a-z0-9-]+)\)$/.exec(value)?.[1];
  return alias ? readRadiusToken(alias as RadiusToken) : value;
}

function observedRadius(row: RadiusRow): RadiusClass {
  const source = readFileSync(join(designSystemRoot, row.source), "utf8");
  const startMatch = new RegExp(row.startPattern).exec(source);
  if (!startMatch)
    throw new Error(`${row.id}: missing start anchor in ${row.source}`);
  const start = startMatch.index;
  const endMatch = new RegExp(row.endPattern).exec(
    source.slice(start + startMatch[0].length),
  );
  if (!endMatch)
    throw new Error(`${row.id}: missing end anchor in ${row.source}`);
  const end = start + startMatch[0].length + endMatch.index;
  const tokens =
    source.slice(start, end).match(/\brounded(?:-[a-z0-9[\].]+)?\b/g) ?? [];
  const unique = [...new Set(tokens)];
  if (tokens.length === 0 || unique.length !== 1)
    throw new Error(
      `${row.id}: expected one authoritative radius across the region, found ${tokens.join(", ") || "none"}`,
    );
  return unique[0] as RadiusClass;
}

function assertRadiusContract(
  rows: readonly RadiusRow[],
  baseline: ReadonlyMap<string, RadiusClass>,
  observations: ReadonlyMap<string, RadiusClass>,
): void {
  const ids = rows.map(({ id }) => id);
  if (
    rows.length !== EXPECTED_DENOMINATOR ||
    new Set(ids).size !== EXPECTED_DENOMINATOR
  )
    throw new Error(
      `radius denominator must contain exactly ${EXPECTED_DENOMINATOR} unique rows`,
    );
  if (
    AUTHORITATIVE_ROWS.size !== ids.length ||
    ids.some((id) => !AUTHORITATIVE_ROWS.has(id))
  )
    throw new Error("radius denominator has a missing or additional identity");
  if ([...observations.keys()].some((id) => !ids.includes(id)))
    throw new Error("production observation has no denominator row");
  for (const row of rows) {
    const authority = AUTHORITATIVE_ROWS.get(row.id);
    if (!authority || authority.referenceSelector !== row.referenceSelector)
      throw new Error(
        `${row.id}: authoritative reference selector mapping changed`,
      );
    if (authority.expected !== row.expected || authority.token !== row.token)
      throw new Error(`${row.id}: authoritative expected radius changed`);
    const observed = observations.get(row.id);
    if (!observed) throw new Error(`${row.id}: production observation missing`);
    const frozen = baseline.get(row.id);
    if (observed === row.expected) {
      if (frozen)
        throw new Error(
          `${row.id}: stale mismatch baseline must be deleted after correction`,
        );
    } else if (!frozen)
      throw new Error(`${row.id}: current mismatch omitted from baseline`);
    else if (frozen !== observed)
      throw new Error(
        `${row.id}: observed mismatch drifted from ${frozen} to ${observed}`,
      );
  }
  for (const id of baseline.keys())
    if (!ids.includes(id))
      throw new Error(`${id}: baseline entry has no denominator row`);
}

const productionObservations = new Map(
  RADIUS_CONTRACT.map((row) => [row.id, observedRadius(row)]),
);

describe("Pasture radius conformance contract", () => {
  test("pins authoritative token resolution per concrete row", () => {
    expect(REFERENCE_SHA256).toBe(
      "87e23c25c95b22bdd46570314f6c649667d787ef89128504dbb52905cd52045a",
    );
    const expected = new Map<RadiusToken, string>([
      ["--radius-sm", "8px"],
      ["--radius", "12px"],
      ["--radius-lg", "18px"],
      ["--radius-pill", "999px"],
    ]);
    expect(
      new Map(
        [...new Set(RADIUS_CONTRACT.map(({ token }) => token))].map((token) => [
          token,
          readRadiusToken(token),
        ]),
      ),
    ).toEqual(expected);
  });
  test("pins the complete denominator and shrinking baseline", () => {
    expect(RADIUS_CONTRACT).toHaveLength(EXPECTED_DENOMINATOR);
    expect(KNOWN_MISMATCHES).toHaveLength(10);
    expect(NON_DENOMINATOR_EXCLUSIONS.size).toBe(0);
    expect(KNOWN_MISMATCHES.has("button")).toBe(false);
    expect(() =>
      assertRadiusContract(
        RADIUS_CONTRACT,
        KNOWN_MISMATCHES,
        productionObservations,
      ),
    ).not.toThrow();
  });
  test("requires strict baseline shrink after a correction", () => {
    const corrected = new Map(productionObservations);
    corrected.set("input", "rounded-sm");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, corrected),
    ).toThrow(/stale mismatch baseline/);
    const shrunken = new Map(KNOWN_MISMATCHES);
    shrunken.delete("input");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, shrunken, corrected),
    ).not.toThrow();
  });
  test.each([
    "status-badge",
    "agent-chip",
    "tabs-segmented-shell",
    "tabs-segmented-trigger",
    "dialog-content",
    "dropdown-subcontent-shell",
    "dropdown-content-shell",
    "dropdown-subtrigger",
    "dropdown-item",
    "dropdown-checkbox-item",
    "message-bubble",
    "typing-bubble",
    "button",
  ])("rejects protected-region drift for %s", (id) => {
    const drifted = new Map(productionObservations);
    drifted.set(id, "rounded-3xl");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, drifted),
    ).toThrow(new RegExp(`${id}: current mismatch omitted`));
  });
  test("rejects Composer and InboxRow target or baseline drift", () => {
    const composerCorrected = new Map(productionObservations);
    composerCorrected.set("composer", "rounded-lg");
    expect(() =>
      assertRadiusContract(
        RADIUS_CONTRACT,
        KNOWN_MISMATCHES,
        composerCorrected,
      ),
    ).toThrow(/composer: stale mismatch baseline/);
    const inboxWrong = new Map(productionObservations);
    inboxWrong.set("inbox-row", "rounded-md");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, inboxWrong),
    ).toThrow(/inbox-row: observed mismatch drifted/);
    const omitted = new Map(KNOWN_MISMATCHES);
    omitted.delete("inbox-row");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, omitted, productionObservations),
    ).toThrow(/inbox-row: current mismatch omitted/);
  });
  test("fails closed on selector, expectation, observation, and denominator changes", () => {
    const remapped = RADIUS_CONTRACT.map((row) =>
      row.id === "input" ? { ...row, referenceSelector: ".not-inp" } : row,
    );
    expect(() =>
      assertRadiusContract(remapped, KNOWN_MISMATCHES, productionObservations),
    ).toThrow(/selector mapping changed/);
    const normalized = RADIUS_CONTRACT.map((row) =>
      row.id === "input" ? { ...row, expected: "rounded-md" as const } : row,
    );
    expect(() =>
      assertRadiusContract(
        normalized,
        KNOWN_MISMATCHES,
        productionObservations,
      ),
    ).toThrow(/expected radius changed/);
    const missing = new Map(productionObservations);
    missing.delete("button");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, missing),
    ).toThrow(/production observation missing/);
    const unmapped = new Map(productionObservations);
    unmapped.set("new-radius-region", "rounded-sm");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, KNOWN_MISMATCHES, unmapped),
    ).toThrow(/observation has no denominator row/);
    expect(() =>
      assertRadiusContract(
        [...RADIUS_CONTRACT.slice(0, -1), RADIUS_CONTRACT[0]],
        KNOWN_MISMATCHES,
        productionObservations,
      ),
    ).toThrow(/23 unique rows/);
    expect(() =>
      assertRadiusContract(
        RADIUS_CONTRACT.slice(0, -1),
        KNOWN_MISMATCHES,
        productionObservations,
      ),
    ).toThrow(/23 unique rows/);
  });
  test("rejects silent mismatch-baseline growth", () => {
    const grown = new Map(KNOWN_MISMATCHES);
    grown.set("button", "rounded-md");
    expect(() =>
      assertRadiusContract(RADIUS_CONTRACT, grown, productionObservations),
    ).toThrow(/button: stale mismatch baseline/);
  });
});
