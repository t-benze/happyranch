/**
 * THR-373: `.gl-prose` must let long unbroken tokens (pasted `ps` dumps,
 * comma-joined feature lists) wrap inside the message column instead of
 * widening it and making the thread transcript scroll horizontally. Table
 * cells opt back out so tables keep their own internal horizontal scroll.
 *
 * Static contract on the shipped stylesheet: jsdom does no layout, so the
 * wrapping itself is proven by browser evidence, not by this file.
 *
 * Run: node --test web/scripts/gl-prose-overflow-wrap.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const CSS_URL = new URL('../src/design-system/patterns/MarkdownTokens.css', import.meta.url);

/** Parse flat (non-nested) CSS into [{ selectors: string[], decls: Map }]. */
function parseRules(css) {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = [];
  for (const chunk of stripped.split('}')) {
    const open = chunk.indexOf('{');
    if (open === -1) continue;
    const selectors = chunk.slice(0, open).split(',').map((s) => s.trim().replace(/\s+/g, ' ')).filter(Boolean);
    const decls = new Map();
    for (const raw of chunk.slice(open + 1).split(';')) {
      const colon = raw.indexOf(':');
      if (colon === -1) continue;
      decls.set(raw.slice(0, colon).trim().toLowerCase(), raw.slice(colon + 1).trim());
    }
    rules.push({ selectors, decls });
  }
  return rules;
}

function ruleFor(rules, selectorList) {
  const wanted = JSON.stringify([...selectorList].sort());
  const matches = rules.filter((r) => JSON.stringify([...r.selectors].sort()) === wanted);
  assert.equal(matches.length, 1, `expected exactly one rule for "${selectorList.join(', ')}", found ${matches.length}`);
  return matches[0];
}

const rules = parseRules(readFileSync(CSS_URL, 'utf8'));

test('.gl-prose root rule declares overflow-wrap: anywhere', () => {
  const rule = ruleFor(rules, ['.gl-prose']);
  assert.equal(rule.decls.get('overflow-wrap'), 'anywhere');
});

test('.gl-prose th/td reset overflow-wrap to normal so tables keep internal scroll', () => {
  const rule = ruleFor(rules, ['.gl-prose th', '.gl-prose td']);
  assert.equal(rule.decls.get('overflow-wrap'), 'normal');
});

test('.gl-prose pre and table keep their own horizontal scroll', () => {
  assert.equal(ruleFor(rules, ['.gl-prose pre']).decls.get('overflow-x'), 'auto');
  assert.equal(ruleFor(rules, ['.gl-prose table']).decls.get('overflow-x'), 'auto');
});
