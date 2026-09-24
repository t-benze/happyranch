import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
// This dedicated authoritative gate ignores inline configuration while the
// ordinary repository lint keeps its justified suppressions. That makes the
// feature boundary independently enforceable without changing global policy.
const eslint = new ESLint({ cwd: webRoot, allowInlineConfig: false })
const RULE_ID = 'feature-boundaries/no-cross-feature-imports'

async function boundaryMessages(source: string, relativePath: string) {
  const [result] = await eslint.lintText(source, {
    filePath: path.join(webRoot, relativePath),
  })
  return result.messages.filter(({ ruleId }) => ruleId === RULE_ID)
}

describe('feature-domain import boundary', () => {
  it('rejects an alias import even beside the legacy ESLint suppression', async () => {
    const messages = await boundaryMessages(
      `// eslint-disable-next-line no-restricted-imports
import { value } from '@/features/usage/value'
export { value }
`,
      'src/features/dashboard/probe.ts',
    )

    expect(messages).toEqual([
      expect.objectContaining({
        ruleId: RULE_ID,
        message: expect.stringContaining(
          'src/features/dashboard/probe.ts -> @/features/usage/value',
        ),
      }),
    ])
  })

  it('rejects a normalized alias despite suppression of both boundary rules', async () => {
    const messages = await boundaryMessages(
      `// eslint-disable-next-line feature-boundaries/no-cross-feature-imports, no-restricted-imports
import { value } from '@/features/dashboard/../usage/value'
export { value }
`,
      'src/features/dashboard/probe.ts',
    )

    expect(messages).toEqual([
      expect.objectContaining({
        ruleId: RULE_ID,
        message: expect.stringContaining(
          'src/features/dashboard/probe.ts -> @/features/dashboard/../usage/value',
        ),
      }),
    ])
  })

  it.each([
    [`import { value } from '../../usage/value'`, '../../usage/value'],
    [`export { value } from '../../usage/value'`, '../../usage/value'],
    [`export * from '../../usage/value'`, '../../usage/value'],
  ])('rejects a relative cross-feature static edge: %s', async (source, target) => {
    const messages = await boundaryMessages(
      `// eslint-disable-next-line no-restricted-imports
${source}`,
      'src/features/dashboard/components/probe.ts',
    )

    expect(messages).toEqual([
      expect.objectContaining({
        ruleId: RULE_ID,
        message: expect.stringContaining(`-> ${target}`),
      }),
    ])
  })

  it.each([
    `import { sibling } from './sibling'`,
    `import { root } from '@/features/dashboard/topTokens'`,
    `export { helper } from '@/lib/modelClassification'`,
    `export * from '@/shared/example'`,
  ])('allows same-feature and neutral-layer static edges: %s', async (source) => {
    expect(
      await boundaryMessages(source, 'src/features/dashboard/components/probe.ts'),
    ).toEqual([])
  })

  it('documents that non-static dynamic import is intentionally excluded', async () => {
    expect(
      await boundaryMessages(
        `export async function load() { return import('@/features/usage/UsagePage') }`,
        'src/features/dashboard/probe.ts',
      ),
    ).toEqual([])
  })

  it('also rejects TypeScript import-equals as a static edge', async () => {
    const messages = await boundaryMessages(
      `import value = require('@/features/usage/value')`,
      'src/features/dashboard/probe.ts',
    )

    expect(messages).toEqual([
      expect.objectContaining({ ruleId: RULE_ID }),
    ])
  })

  it('reports no cross-feature static edges in the complete current feature tree', async () => {
    const results = await eslint.lintFiles(['src/features/**/*.{ts,tsx}'])
    const messages = results.flatMap((result) =>
      result.messages
        .filter(({ ruleId }) => ruleId === RULE_ID)
        .map((message) => `${path.relative(webRoot, result.filePath)}:${message.line} ${message.message}`),
    )

    expect(messages).toEqual([])
  // Hosted Node 24 runs this complete scan under full-suite contention; keep
  // the authoritative check finite while allowing ample headroom above the
  // previously observed 17.98-second execution.
  }, 45_000)
})
