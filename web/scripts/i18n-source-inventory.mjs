#!/usr/bin/env node
/** Build-time source inventory. No fs/TypeScript dependency enters the SPA.
 * This checks supported static source shapes, not rendering/dataflow or meaning.
 */
import { existsSync, readFileSync } from 'node:fs';
import { basename, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const proseAttributes = new Set(['placeholder', 'title', 'description', 'aria-label', 'aria-description', 'aria-valuetext', 'alt']);
const fixturePath = /(?:\.(?:test|spec|stories)\.[cm]?[jt]sx?$|\/(?:test|__tests__|mocks|fixtures|prototypes)\/)/;
const catalogPath = /\/lib\/i18n\/locales\//;
const unwrap = n => {
  while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n) || ts.isSatisfiesExpression(n))) n = n.expression;
  return n;
};
function staticText(input) {
  const n = unwrap(input);
  if (!n) return undefined;
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
  if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = staticText(n.left), right = staticText(n.right);
    if (left !== undefined && right !== undefined) return left + right;
  }
  return undefined;
}
function literalBranches(input) {
  const n = unwrap(input);
  if (!n) return [];
  const text = staticText(n);
  if (text !== undefined) return [{ node: n, literal: text }];
  if (ts.isConditionalExpression(n)) return [...literalBranches(n.whenTrue), ...literalBranches(n.whenFalse)];
  if (ts.isBinaryExpression(n)) {
    if ([ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(n.operatorToken.kind)) return [...literalBranches(n.left), ...literalBranches(n.right)];
    if (n.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) return literalBranches(n.right);
  }
  return [];
}
const parse = (path, source) => ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, path.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
const walk = (node, visit) => { visit(node); ts.forEachChild(node, child => walk(child, visit)); };
function hasReturnedJSX(node) {
  if (!node) return false;
  let returned = false;
  walk(node, child => {
    const value = ts.isReturnStatement(child) ? child.expression : ts.isArrowFunction(child) && !ts.isBlock(child.body) ? child.body : undefined;
    if (value) walk(value, expression => { if (ts.isJsxElement(expression) || ts.isJsxSelfClosingElement(expression) || ts.isJsxFragment(expression)) returned = true; });
  });
  return returned;
}
function sourceSymbol(node, source) {
  // Anonymous callbacks belong to their enclosing named shipping declaration.
  for (let p = node.parent; p; p = p.parent) {
    if ((ts.isFunctionDeclaration(p) || ts.isVariableDeclaration(p) || ts.isClassDeclaration(p)) && p.name && ts.isIdentifier(p.name)) {
      if (p.parent === source || (ts.isVariableDeclaration(p) && p.parent?.parent?.parent === source)) return p.name.text;
    }
  }
  return '<module>';
}
export function copySites(path, text) {
  const source = parse(path, text), sites = [];
  function record(node, slot, literal) {
    literal = literal.replace(/\s+/g, ' ').trim();
    if (!literal) return;
    const start = source.getLineAndCharacterOfPosition(node.getStart(source));
    sites.push({ path, symbol: sourceSymbol(node, source), slot, literal, line: start.line + 1, column: start.character, offset: node.getStart(source) });
  }
  walk(source, n => {
    if (ts.isJsxText(n)) record(n, 'children', n.text);
    if (ts.isJsxExpression(n) && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent))) {
      for (const site of literalBranches(n.expression)) record(site.node, 'children-expression', site.literal);
    }
    if (ts.isJsxAttribute(n) && proseAttributes.has(n.name.getText(source))) {
      const value = n.initializer && ts.isJsxExpression(n.initializer) ? n.initializer.expression : n.initializer;
      for (const site of literalBranches(value)) record(site.node, n.name.getText(source), site.literal);
    }
    if (ts.isBindingElement(n) && n.initializer && ['closeLabel', 'mermaidLoadingLabel'].includes(n.name.getText(source))) {
      for (const site of literalBranches(n.initializer)) record(site.node, `default:${n.name.getText(source)}`, site.literal);
    }
  });
  return sites;
}

// Reviewed exact machine/raw/default slots. Punctuation/spacing has no prose.
// No file, glob, element-type or blanket baseline exception is supported.
const raw = (path, symbol, slot, literal, reason) => ({ path: `src/${path}`, symbol, slot, literal, reason });
export const COPY_EXCEPTIONS = [
  raw('design-system/layouts/AppShell/Sidebar.tsx', 'Sidebar', 'children', 'Happy', 'Brand wordmark bytes'),
  raw('design-system/layouts/AppShell/Sidebar.tsx', 'Sidebar', 'children', 'Ranch', 'Brand wordmark bytes'),
  raw('design-system/layouts/AppShell/Sidebar.tsx', 'Sidebar', 'children', 'YT', 'Existing avatar initials'),
  raw('design-system/patterns/CommandPalette.tsx', 'CommandPalette', 'children', 'esc', 'Keyboard key name'),
  raw('design-system/patterns/HelpSheet.tsx', 'HelpSheet', 'children-expression', 'Keyboard shortcuts', 'Prop fallback compatibility; mounted owner supplies title'),
  raw('design-system/patterns/HelpSheet.tsx', 'HelpSheet', 'children-expression', 'List of keyboard shortcuts available on this screen.', 'Prop fallback compatibility; mounted owner supplies description'),
  raw('design-system/patterns/HelpSheet.tsx', 'TabbedBody', 'children-expression', 'No shortcuts defined.', 'Prop fallback compatibility; mounted owner supplies emptyLabel'),
  raw('design-system/primitives/Dialog.tsx', 'DialogContent', 'default:closeLabel', 'Close', 'Standalone default compatibility; direct mounted callers supply closeLabel'),
  raw('design-system/patterns/Markdown.tsx', 'Markdown', 'default:mermaidLoadingLabel', 'Rendering diagram…', 'Standalone default compatibility; mounted callers supply mermaidLoadingLabel'),
  raw('design-system/patterns/TaskCard.tsx', 'TaskCard', 'children', 'supersedes', 'Standalone default compatibility; mounted caller supplies labels'),
  raw('design-system/patterns/TaskCard.tsx', 'TaskCard', 'children', 'superseded by', 'Standalone default compatibility; mounted caller supplies labels'),
  raw('design-system/patterns/MessageBubble.tsx', 'MessageBubble', 'aria-label', 'system event', 'Standalone prop fallback; mounted owner supplies labels.systemEvent'),
  raw('design-system/patterns/MessageBubble.tsx', 'MessageBubble', 'children-expression', 'system event', 'Standalone prop fallback; mounted owner supplies labels.systemEvent'),
  raw('design-system/patterns/MessageBubble.tsx', 'MessageBubble', 'children-expression', 'Declined:', 'Standalone prop fallback; mounted owner supplies labels.declined'),
  raw('design-system/patterns/InboxRow.tsx', 'InboxRow', 'children-expression', 'last', 'Standalone prop fallback; mounted owner supplies labels.last'),
  raw('design-system/patterns/InboxRow.tsx', 'InboxRow', 'aria-label', 'needs you', 'Standalone prop fallback; mounted owner supplies labels.needsYou'),
  raw('features/agents/AgentDetailPane.tsx', 'AgentDetailPane', 'children', 'happyranch memory reindex', 'Literal CLI command'),
  raw('features/agents/AgentDetailPane.tsx', 'AgentDetailPane', 'children', '⌘S', 'Keyboard shortcut'),
  raw('features/agents/TeamEscalationPolicyCard.tsx', 'V2HistorySection', 'children', 'v', 'Version prefix'),
  raw('features/artifacts/ArtifactsPage.tsx', 'ArtifactsPage', 'placeholder', 'dev_agent-2026-06-10-report.pdf', 'Filename example'),
  raw('features/artifacts/ArtifactsPage.tsx', 'ArtifactsPage', 'children', 'name', 'Sort key'),
  raw('features/artifacts/ArtifactsPage.tsx', 'ArtifactsPage', 'children', 'size_bytes', 'Sort key'),
  raw('features/artifacts/ArtifactsPage.tsx', 'ArtifactsPage', 'children', 'modified_at', 'Sort key'),
  raw('features/artifacts/ArtifactsPage.tsx', 'ArtifactsPage', 'children', '&lt;agent&gt;-&lt;YYYY-MM-DD&gt;-&lt;slug&gt;', 'Filename grammar'),
  raw('features/jobs/JobsPage.tsx', 'OutcomeCell', 'children', 'running', 'Raw job outcome'),
  raw('features/jobs/JobsPage.tsx', 'OutcomeCell', 'children', 'exit', 'Raw process exit prefix'),
  raw('features/jobs/JobsPage.tsx', 'OutcomeCell', 'children-expression', 'failed', 'Raw job outcome'),
  raw('features/jobs/JobsPage.tsx', 'OutcomeCell', 'children', 'rejected', 'Raw job outcome'),
  raw('features/jobs/OutputPanel.tsx', 'OutputPanel', 'children', '[done]', 'Raw stream protocol marker'),
  raw('features/jobs/OutputPanel.tsx', 'OutputPanel', 'children', 'exit=', 'Raw process exit prefix'),
  raw('features/jobs/OutputPanel.tsx', 'OutputPanel', 'children-expression', 'n/a', 'Raw unavailable exit value'),
  raw('features/jobs/OutputPanel.tsx', 'OutputPanel', 'children', 'stdout', 'Stream name'),
  raw('features/jobs/OutputPanel.tsx', 'OutputPanel', 'children', 'stderr', 'Stream name'),
  raw('features/onboarding/OnboardingPage.tsx', 'WelcomeStep', 'children', 'claude', 'Executable name'),
  raw('features/onboarding/OnboardingPage.tsx', 'WelcomeStep', 'children', 'codex', 'Executable name'),
  raw('features/onboarding/OnboardingPage.tsx', 'WelcomeStep', 'children', 'node', 'Executable name'),
  raw('features/onboarding/OnboardingPage.tsx', 'CreateStep', 'children', '· ^[a-z0-9-]&#123;1,40&#125;$', 'Machine slug grammar'),
  raw('features/settings/sections/AssistantSection.tsx', 'SetupActions', 'children', 'happyranch assistant register', 'Literal CLI command'),
  raw('features/settings/sections/AssistantSection.tsx', 'RegisterExecutorSection', 'placeholder', 'my-cli', 'Executable slug example'),
  raw('features/settings/sections/AssistantSection.tsx', 'RegisterExecutorSection', 'placeholder', 'claude', 'Executable name example'),
  raw('features/settings/sections/AssistantSection.tsx', 'RegisterExecutorSection', 'placeholder', 'claude --dangerously-skip-permissions', 'Exact executable arguments example'),
  raw('features/settings/sections/DaemonCapacitySection.tsx', 'DaemonCapacitySection', 'children', 'queue_workers · host_global_session_cap', 'Configuration key names'),
  raw('features/settings/sections/ExecutorBinariesSection.tsx', 'ExecutorBinariesSection', 'children', 'which claude', 'Literal executable lookup command'),
  raw('features/settings/sections/OrganizationSection.tsx', 'OrganizationSection', 'placeholder', 'HH:MM', 'Time format grammar'),
  raw('features/settings/sections/OrganizationSection.tsx', 'OrganizationSection', 'placeholder', 'UTC', 'Timezone identifier'),
  raw('features/settings/sections/OrganizationSection.tsx', 'OrganizationSection', 'children', 'all', 'Policy enum'),
  raw('features/settings/sections/OrganizationSection.tsx', 'OrganizationSection', 'children', 'whitelist', 'Policy enum'),
  ...['CustomSkillCard', 'CustomSkillDetailPage', 'SkillCard', 'SkillDetailPage', 'SkillValidationPage'].map(symbol => raw(`features/skills/${symbol}.tsx`, symbol === 'SkillValidationPage' ? 'EventRow' : symbol, 'children', 'v', 'Version prefix')),
  raw('features/skills/CustomSkillCreatePage.tsx', 'CustomSkillCreatePage', 'children', 'SKILL.md', 'Canonical filename'),
  raw('features/system-assistant/AssistantDockHost.tsx', 'AssistantDockHost', 'children', 'Enter', 'Keyboard key name'),
  raw('features/system-assistant/AssistantDockHost.tsx', 'AssistantDockHost', 'children', 'Shift+Enter', 'Keyboard key combination'),
  raw('features/tasks/FanoutBand.tsx', 'FanoutBand', 'children', 'all-terminal', 'Raw fanout state'),
  raw('features/tasks/TasksPage.tsx', 'TasksList', 'children-expression', '@media (max-width: 767px) { [data-tasks-responsive-list] > div:first-child { display: none; } [data-tasks-responsive-list] section li > div > a { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: .5rem .75rem; align-items: center; } [data-tasks-responsive-list] section li > div > a > div { width: auto; min-width: 0; } [data-tasks-responsive-list] section li > div > a > div:nth-child(1) { grid-column: 1; grid-row: 1; } [data-tasks-responsive-list] section li > div > a > div:nth-child(2) { grid-column: 2; grid-row: 1; } [data-tasks-responsive-list] section li > div > a > div:nth-child(3) { grid-column: 1 / -1; grid-row: 2; overflow: visible; } [data-tasks-responsive-list] section li > div > a > div:nth-child(3) > span { white-space: normal; overflow: visible; text-overflow: clip; } [data-tasks-responsive-list] section li > div > a > div:nth-child(4) { grid-column: 1; grid-row: 3; } [data-tasks-responsive-list] section li > div > a > div:nth-child(5) { grid-column: 2; grid-row: 3; } [data-tasks-responsive-list] section li > div > a > div:nth-child(6) { grid-column: 2; grid-row: 4; justify-self: end; } }', 'Existing stylesheet source bytes, not displayed prose'),
  raw('features/todos/TodoDetailPage.tsx', 'TodoDetailPage', 'children', 'task_id=', 'Raw relation key'),
  raw('features/work-hours-config/AgentDetailPage.tsx', 'AgentDetailPage', 'children', '## Routine Tasks', 'Exact Markdown section heading required in authored prompt'),
  raw('features/work-hours-config/TierEditorDialog.tsx', 'TierEditorDialog', 'children', 'windowed', 'Tier mode enum'),
  raw('features/work-hours-config/TierEditorDialog.tsx', 'TierEditorDialog', 'children', 'continuous', 'Tier mode enum'),
  raw('features/work-hours-config/TierEditorDialog.tsx', 'TierEditorDialog', 'placeholder', '2h', 'Interval syntax example'),
  raw('shared/connect/ConnectFlow.tsx', 'AdapterConnect', 'children', '&lt;name&gt;-adapter', 'Executable naming grammar'),
  raw('shared/threads/NewThreadDialog.tsx', 'NewThreadDialog', 'placeholder', 'agent_a, agent_b', 'Agent ID examples'),
  raw('features/threads/InviteDialog.tsx', 'InviteDialog', 'placeholder', 'agent_a, agent_b', 'Agent ID examples'),
  raw('shared/work-hours/EligibilityEditorDialog.tsx', 'EligibilityEditorDialog', 'children', 'all', 'Policy enum'),
  raw('shared/work-hours/EligibilityEditorDialog.tsx', 'EligibilityEditorDialog', 'children', 'whitelist', 'Policy enum'),
];
const siteKey = s => JSON.stringify([s.path, s.symbol, s.slot, s.literal]);
function validateExceptions(exceptions) {
  const keys = new Set();
  for (const entry of exceptions) {
    if (!entry || !entry.path?.startsWith('src/') || /[*?]/.test(entry.path) || !entry.symbol || /[*?]/.test(entry.symbol) || !entry.slot || !entry.literal || !entry.reason?.trim()) throw new Error('i18n inventory: exact path/symbol/slot/literal/reason required for exception');
    if (keys.has(siteKey(entry))) throw new Error(`i18n inventory: duplicate exception ${siteKey(entry)}`);
    keys.add(siteKey(entry));
  }
  return keys;
}
const hasProse = text => /[\p{L}]/u.test(text.replace(/&(?:[a-z]+|#\d+);/gi, ''));
function readInput(root, path) {
  const file = resolve(root, path);
  if (!existsSync(file)) throw new Error(`i18n inventory: missing ${path}`);
  return readFileSync(file, 'utf8');
}

export function inventorySource(webRoot = process.cwd()) {
  const root = resolve(webRoot);
  const html = readInput(root, 'index.html');
  const entries = [...html.matchAll(/<script\b([^>]*)>[\s\S]*?<\/script>/g)].filter(m => /\btype\s*=\s*["']module["']/.test(m[1]));
  if (entries.length !== 1) throw new Error('i18n inventory: index.html requires one supported external module entry; review entry change');
  const entry = /\bsrc\s*=\s*["']([^"']+)["']/.exec(entries[0][1])?.[1];
  if (entry !== '/src/main.tsx') throw new Error(`i18n inventory: unsupported index.html module entry ${entry}; review entry/root before release`);
  const configFile = ts.readConfigFile(resolve(root, 'tsconfig.json'), ts.sys.readFile);
  if (configFile.error || JSON.stringify(configFile.config?.compilerOptions?.paths) !== JSON.stringify({ '@/*': ['src/*'] }) || configFile.config.compilerOptions.baseUrl !== '.') throw new Error('i18n inventory: unsupported tsconfig.json alias/root; expected @/* -> src/*');
  const vite = readInput(root, 'vite.config.ts');
  // Parse, never execute config (which has a daemon/proxy reader).
  const viteAST = parse('vite.config.ts', vite), aliases = [], customInputs = [];
  walk(viteAST, n => {
    if (ts.isPropertyAssignment(n) && n.name.getText(viteAST) === 'alias') aliases.push(n.initializer.getText(viteAST));
    if (ts.isPropertyAssignment(n) && ['input', 'root'].includes(n.name.getText(viteAST))) customInputs.push(n.name.getText(viteAST));
  });
  if (aliases.length !== 1 || !/^\{\s*['"]@['"]\s*:\s*path\.resolve\(__dirname,\s*['"]src['"]\)\s*,?\s*\}$/.test(aliases[0]) || customInputs.length) throw new Error('i18n inventory: unsupported Vite alias/root/input; align static Vite and tsconfig mapping before release');
  const modules = new Map(), resolving = new Set(), mounted = new Set(), routes = [], dialogs = [], mounts = [];
  const portable = p => relative(root, p).split('\\').join('/');
  function localPath(from, specifier) {
    if (!specifier.startsWith('.') && !specifier.startsWith('@/')) return undefined;
    const base = specifier.startsWith('@/') ? resolve(root, 'src', specifier.slice(2)) : resolve(dirname(from), specifier);
    if (!portable(base).startsWith('src/')) throw new Error(`i18n inventory: import leaves src root: ${portable(from)} -> ${specifier}`);
    const file = [base, ...['.ts', '.tsx', '/index.ts', '/index.tsx'].map(s => base + s)].find(p => existsSync(p) && /\.tsx?$/.test(p));
    if (!file) {
      if (/\.(?:css|json|svg|png|woff2?)$/.test(base)) return undefined;
      throw new Error(`i18n inventory: unresolved local import ${portable(from)} -> ${specifier}`);
    }
    return file;
  }
  function module(file) {
    if (modules.has(file)) return modules.get(file);
    const source = readFileSync(file, 'utf8'), ast = parse(file, source);
    const m = { file, path: portable(file), source, ast, definitions: new Map(), imports: new Map(), exports: new Map(), stars: [] };
    modules.set(file, m);
    for (const statement of ast.statements) {
      if (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement)) {
        if (statement.name) {
          m.definitions.set(statement.name.text, statement);
          if (statement.modifiers?.some(n => n.kind === ts.SyntaxKind.ExportKeyword)) m.exports.set(statement.modifiers.some(n => n.kind === ts.SyntaxKind.DefaultKeyword) ? 'default' : statement.name.text, { local: statement.name.text });
        }
      }
      if (ts.isVariableStatement(statement)) for (const d of statement.declarationList.declarations) if (ts.isIdentifier(d.name)) {
        m.definitions.set(d.name.text, d.initializer);
        if (statement.modifiers?.some(n => n.kind === ts.SyntaxKind.ExportKeyword)) m.exports.set(d.name.text, { local: d.name.text });
      }
      if (ts.isImportDeclaration(statement) && !statement.importClause?.isTypeOnly) {
        const target = localPath(file, statement.moduleSpecifier.text), clause = statement.importClause;
        if (target && clause) {
          if (clause.name) m.imports.set(clause.name.text, { target, name: 'default' });
          if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) for (const i of clause.namedBindings.elements) if (!i.isTypeOnly) m.imports.set(i.name.text, { target, name: i.propertyName?.text ?? i.name.text });
          if (clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) m.imports.set(clause.namedBindings.name.text, { target, name: '*' });
        }
      }
      if (ts.isExportDeclaration(statement) && !statement.isTypeOnly) {
        const target = statement.moduleSpecifier && localPath(file, statement.moduleSpecifier.text);
        if (statement.exportClause && ts.isNamedExports(statement.exportClause)) {
          for (const i of statement.exportClause.elements) if (!i.isTypeOnly) m.exports.set(i.name.text, target ? { target, name: i.propertyName?.text ?? i.name.text } : { local: i.propertyName?.text ?? i.name.text });
        } else if (target) m.stars.push(target);
      }
      if (ts.isExportAssignment(statement) && ts.isIdentifier(statement.expression)) m.exports.set('default', { local: statement.expression.text });
    }
    return m;
  }
  function exported(file, name) {
    const id = `${file}#${name}`;
    if (resolving.has(id)) return undefined;
    resolving.add(id);
    try {
      const m = module(file), e = m.exports.get(name);
      if (e) return e.local ? binding(m, e.local) : exported(e.target, e.name);
      const results = m.stars.map(target => exported(target, name)).filter(Boolean);
      if (results.length > 1) throw new Error(`i18n inventory: ambiguous reexport ${m.path}#${name}`);
      return results[0];
    } finally { resolving.delete(id); }
  }
  function binding(m, name) {
    const imported = m.imports.get(name);
    if (imported) return exported(imported.target, imported.name);
    if (m.definitions.has(name)) {
      const n = unwrap(m.definitions.get(name));
      if (n && ts.isIdentifier(n) && n.text !== name) return binding(m, n.text);
      // Literal lazy imports, including the named-export .then wrapper.
      if (n && ts.isCallExpression(n) && /(?:^|\.)lazy$/.test(n.expression.getText(m.ast))) {
        let target, exportName = 'default';
        walk(n, child => {
          if (ts.isCallExpression(child) && child.expression.kind === ts.SyntaxKind.ImportKeyword) {
            const spec = staticText(child.arguments[0]);
            if (spec === undefined) throw new Error(`i18n inventory: unsupported computed lazy import ${m.path}#${name}`);
            target = localPath(m.file, spec);
          }
          if (ts.isPropertyAssignment(child) && child.name.getText(m.ast) === 'default' && ts.isPropertyAccessExpression(child.initializer)) exportName = child.initializer.name.text;
        });
        if (!target) throw new Error(`i18n inventory: unsupported lazy factory ${m.path}#${name}`);
        return exported(target, exportName);
      }
      return { m, name, node: m.definitions.get(name) };
    }
    return undefined;
  }
  function tagBinding(m, tag) {
    if (ts.isIdentifier(tag)) return binding(m, tag.text);
    if (ts.isPropertyAccessExpression(tag) && ts.isIdentifier(tag.expression)) {
      const imported = m.imports.get(tag.expression.text);
      if (imported?.name === '*') return exported(imported.target, tag.name.text);
    }
    return undefined;
  }
  function copyFreeElement(node, m, seen = new Set()) {
    if (!node) return false;
    let free = true;
    walk(node, n => {
      if (ts.isJsxText(n) && n.text.trim()) free = false;
      if (ts.isJsxExpression(n) && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent)) && n.expression && n.expression.getText(m.ast) !== 'children') free = false;
      if (ts.isJsxAttribute(n) && proseAttributes.has(n.name.getText(m.ast))) free = false;
      if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
        const tag = n.tagName.getText(m.ast), target = tagBinding(m, n.tagName);
        if (target) {
          const id = `${target.m.path}#${target.name}`;
          if (!seen.has(id)) { seen.add(id); if (!copyFreeElement(target.node, target.m, seen)) free = false; }
        } else if (/^[A-Z]/.test(tag) && !['Navigate', 'Outlet'].includes(tag) && !tag.endsWith('.Provider')) free = false;
      }
    });
    return free;
  }
  function visitOwner(m, name, node) {
    const id = `${m.path}#${name}`;
    if (mounted.has(id)) return;
    if (fixturePath.test('/' + m.path) || catalogPath.test('/' + m.path)) throw new Error(`i18n inventory: promoted fixture/catalog/prototype owner ${id}; review shipping ownership`);
    mounted.add(id);
    const roleCounts = new Map();
    function visit(n) {
      if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n) || ts.isTypeNode(n)) return;
      if (name === '<module>' && (ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n) || ts.isVariableStatement(n))) return;
      if (n !== node && (ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n))) return;
      // The sole supported prototype gate is proven from its actual definition.
      if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && n.left.getText(m.ast) === '!PROTOTYPES_DISABLED') {
        const flag = binding(m, 'PROTOTYPES_DISABLED');
        if (flag?.m.path !== 'src/prototypes/index.tsx' || flag.node?.getText(flag.m.ast).replace(/\s+/g, '') !== 'import.meta.env.PROD&&!import.meta.env.VITE_ENABLE_PROTOTYPES') throw new Error(`i18n inventory: unsupported prototype gate ${m.path}; review production promotion`);
        return;
      }
      if (ts.isCallExpression(n) && n.expression.kind === ts.SyntaxKind.ImportKeyword && staticText(n.arguments[0]) === undefined) throw new Error(`i18n inventory: unsupported dynamic import in ${id}`);
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) {
        const called = binding(m, n.expression.text);
        if (called && /Dialog$/.test(called.name) && hasReturnedJSX(called.node) && !called.m.path.startsWith('src/design-system/primitives/')) dialogs.push({ path: called.m.path, symbol: called.name, consumerPath: m.path, consumerSymbol: name, site: 'render-call' });
      }
      if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
        const tag = n.tagName.getText(m.ast), target = tagBinding(m, n.tagName);
        const imported = ts.isIdentifier(n.tagName) && m.imports.get(n.tagName.text);
        if (imported && !target || !target && /Dialog$/.test(tag) && !m.ast.statements.some(s => ts.isImportDeclaration(s) && s.importClause?.namedBindings && ts.isNamedImports(s.importClause.namedBindings) && s.importClause.namedBindings.elements.some(e => e.name.text === tag))) throw new Error(`i18n inventory: undefined component ${id} -> ${tag}`);
        const attrs = new Map(n.attributes.properties.filter(ts.isJsxAttribute).map(a => [a.name.getText(m.ast), a.initializer && ts.isJsxExpression(a.initializer) ? a.initializer.expression : a.initializer]));
        const routerAlias = ts.isIdentifier(n.tagName) && [...m.ast.statements].some(s => ts.isImportDeclaration(s) && s.moduleSpecifier.text === 'react-router-dom' && s.importClause?.namedBindings && ts.isNamedImports(s.importClause.namedBindings) && s.importClause.namedBindings.elements.some(e => e.name.text === tag && (e.propertyName?.text ?? e.name.text) === 'Route'));
        if (routerAlias) {
          const token = attrs.has('index') ? 'index' : staticText(attrs.get('path'));
          if (attrs.has('path') && token === undefined) throw new Error(`i18n inventory: unsupported computed route path ${id}`);
          const parents = [];
          for (let p = n.parent; p; p = p.parent) if (ts.isJsxElement(p) && p.openingElement !== n && p.openingElement.tagName.getText(m.ast) === tag) {
            const parentPath = p.openingElement.attributes.properties.find(a => ts.isJsxAttribute(a) && a.name.getText(m.ast) === 'path');
            const value = parentPath?.initializer && ts.isJsxExpression(parentPath.initializer) ? parentPath.initializer.expression : parentPath?.initializer;
            const parentToken = staticText(value);
            if (parentToken !== undefined) parents.unshift(parentToken);
          }
          if (token !== undefined) routes.push({ path: m.path, symbol: name, token, parents, copyFree: copyFreeElement(attrs.get('element'), m) });
        }
        const role = staticText(attrs.get('role'));
        if (target && /Dialog$/.test(target.name) && !target.m.path.startsWith('src/design-system/primitives/')) dialogs.push({ path: target.m.path, symbol: target.name, consumerPath: m.path, consumerSymbol: name, site: 'component' });
        if (['dialog', 'alertdialog'].includes(role)) {
          const count = (roleCounts.get(role) ?? 0) + 1;
          roleCounts.set(role, count);
          dialogs.push({ path: m.path, symbol: name, consumerPath: m.path, consumerSymbol: name, site: `role:${role}${count > 1 ? `:${count}` : ''}` });
        }
        mounts.push({ path: m.path, symbol: name, tag, targetPath: target?.m.path, targetSymbol: target?.name, props: [...attrs.keys()], spreads: n.attributes.properties.some(ts.isJsxSpreadAttribute) });
        if (target) visitOwner(target.m, target.name, target.node);
      }
      if (ts.isIdentifier(n) && !(ts.isPropertyAccessExpression(n.parent) && n.parent.name === n) && !(ts.isPropertyAssignment(n.parent) && n.parent.name === n)) {
        const target = binding(m, n.text);
        if (target?.node && !(ts.isJsxOpeningElement(n.parent) || ts.isJsxSelfClosingElement(n.parent) || ts.isJsxClosingElement(n.parent))) {
          // References to static render-return functions/JSX-valued constants,
          // including render prop callbacks; an import declaration alone never
          // gets here. Calls without a provable JSX body do not create mounts.
          let jsx = false;
          walk(target.node, child => { if (ts.isJsxElement(child) || ts.isJsxSelfClosingElement(child)) jsx = true; });
          const parent = n.parent;
          const renderReference = ts.isCallExpression(parent) && parent.expression === n
            || ts.isJsxExpression(parent) || ts.isReturnStatement(parent)
            || ts.isPropertyAssignment(parent) && ['element', 'render', 'code'].includes(parent.name.getText(m.ast));
          if (jsx && renderReference && target.name !== name) visitOwner(target.m, target.name, target.node);
        }
      }
      ts.forEachChild(n, visit);
    }
    if (node) visit(node);
  }
  const main = module(resolve(root, 'src/main.tsx'));
  visitOwner(main, '<module>', main.ast);
  const seen = new Set();
  const unique = rows => rows.filter(row => { const key = JSON.stringify(row); if (seen.has(key)) return false; seen.add(key); return true; });
  const copies = [...modules.values()].flatMap(m => copySites(m.path, m.source).filter(site => mounted.has(`${site.path}#${site.symbol}`)));
  return { entry: 'src/main.tsx', modules: [...modules.values()].map(m => m.path).sort(), mountedSymbols: [...mounted].sort(), routes: unique(routes), dialogs: unique(dialogs), mounts, copies, limits: 'Supported static source sites only; imports are not mounts; no rendering, dataflow, translation-quality or bundler completeness claim.' };
}

const inventoryCache = new Map();
function cachedInventory(root) {
  if (!inventoryCache.has(root)) inventoryCache.set(root, inventorySource(root));
  return inventoryCache.get(root);
}
export const ownedCopyPlugin = {
  rules: {
    'no-untranslated-copy': {
      meta: { type: 'problem', schema: [], messages: { owned: 'App-owned {{slot}} literal {{literal}} requires en + zh-CN catalogs or localized props.', inventory: '{{reason}}', caller: 'Mounted {{tag}} requires localized {{prop}}; opaque spread needs ownership review.' } },
      create(context) {
        return { Program() {
          let inventory;
          try { inventory = cachedInventory(context.cwd); } catch (error) { context.report({ loc: { line: 1, column: 0 }, messageId: 'inventory', data: { reason: error.message } }); return; }
          const path = relative(context.cwd, context.filename).split('\\').join('/');
          const text = context.sourceCode.text;
          const diskMatches = existsSync(context.filename) && readFileSync(context.filename, 'utf8') === text;
          const keys = validateExceptions(COPY_EXCEPTIONS);
          if (fixturePath.test('/' + path) || catalogPath.test('/' + path)) return;
          for (const site of copySites(path, text)) {
            if (diskMatches && !inventory.mountedSymbols.includes(`${path}#${site.symbol}`)) continue;
            if (hasProse(site.literal) && !keys.has(siteKey(site))) context.report({ loc: { line: site.line, column: site.column }, messageId: 'owned', data: site });
          }
          // Known default-bearing shared components. Preserve their standalone
          // bytes while refusing direct production mounts without presentation.
          const required = { DialogContent: ['closeLabel'], Markdown: ['mermaidLoadingLabel'], TaskCard: ['labels'], HelpSheet: ['title', 'description', 'emptyLabel'], MessageBubble: ['labels'], InboxRow: ['labels'] };
          const sites = diskMatches ? inventory.mounts : [];
          if (!diskMatches) {
            const ast = parse(path, text), aliases = new Map();
            walk(ast, node => {
              if (ts.isImportDeclaration(node) && node.importClause?.namedBindings && ts.isNamedImports(node.importClause.namedBindings)) for (const item of node.importClause.namedBindings.elements) aliases.set(item.name.text, item.propertyName?.text ?? item.name.text);
              if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) sites.push({ path, tag: node.tagName.getText(ast), targetSymbol: aliases.get(node.tagName.getText(ast)) ?? node.tagName.getText(ast), props: node.attributes.properties.filter(ts.isJsxAttribute).map(a => a.name.getText(ast)) });
            });
          }
          for (const site of sites.filter(s => s.path === path)) {
            const props = required[site.targetSymbol] ?? [];
            if (site.targetSymbol === 'StatusBadge' && site.props.includes('blockKind')) props.push('waitingLabels');
            for (const prop of props) if (!site.props.includes(prop)) context.report({ loc: { line: 1, column: 0 }, messageId: 'caller', data: { tag: site.tag, prop } });
          }
        } };
      },
    },
  },
};

export function auditCopyExceptions(inventory) {
  const exceptions = COPY_EXCEPTIONS;
  const keys = validateExceptions(exceptions), observed = new Set(inventory.copies.map(siteKey));
  return {
    omissions: inventory.copies.filter(site => hasProse(site.literal) && !keys.has(siteKey(site))),
    stale: exceptions.filter(entry => !observed.has(siteKey(entry))),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const inventory = inventorySource(process.argv[2] ?? process.cwd());
    const audit = auditCopyExceptions(inventory);
    console.log(JSON.stringify({ ...inventory, audit }, null, 2));
    if (audit.omissions.length || audit.stale.length) process.exitCode = 1;
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
