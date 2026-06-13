import { describe, it, expect } from 'vitest';
import {
  MODEL_FIX_CUTOVER_TS,
  classifyModel,
  toTopRows,
  type RollupRow,
} from './topTokens';

/**
 * The Model-label precedence here MUST mirror the CLI's `classify_model`
 * (cli/commands/tasks.py, Leg B) exactly — same EM-DASH labels, same
 * cutover boundary (Date compare, never string). These cases pin that parity.
 */

function row(over: Partial<RollupRow> = {}): RollupRow {
  return {
    thread_id: 'THR-1',
    sessions: 1,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    ...over,
  };
}

describe('classifyModel', () => {
  it('renders the observed model id verbatim when uniform and non-NULL', () => {
    expect(
      classifyModel(
        row({ non_null_sessions: 3, model_distinct: 1, model_any: 'claude-opus-4-8[1m]' }),
      ),
    ).toBe('claude-opus-4-8[1m]');
  });

  it('renders (mixed) when >1 distinct non-NULL model id', () => {
    expect(
      classifyModel(row({ non_null_sessions: 4, model_distinct: 2, model_any: 'z' })),
    ).toBe('(mixed)');
  });

  it('renders (mixed) when observed models coexist with NULL-model sessions', () => {
    expect(
      classifyModel(
        row({
          non_null_sessions: 2,
          model_distinct: 1,
          model_any: 'claude-opus-4-8[1m]',
          null_codex_sessions: 1,
        }),
      ),
    ).toBe('(mixed)');
  });

  it('renders (cli-unreported) for an all-NULL codex rollup', () => {
    expect(
      classifyModel(row({ non_null_sessions: 0, null_codex_sessions: 3 })),
    ).toBe('(cli-unreported)');
  });

  it('renders (mixed) for an all-NULL rollup spanning codex + claude', () => {
    expect(
      classifyModel(
        row({ non_null_sessions: 0, null_codex_sessions: 2, null_claude_sessions: 1 }),
      ),
    ).toBe('(mixed)');
  });

  it('renders (unknown — pre-fix) for a NULL-model claude row whose max created_at is before the cutover', () => {
    expect(
      classifyModel(
        row({
          non_null_sessions: 0,
          null_claude_sessions: 2,
          null_claude_max_created_at: '2026-06-10T09:00:00+00:00',
        }),
      ),
    ).toBe('(unknown — pre-fix)');
  });

  it('renders (unknown — ANOMALY) at the cutover boundary using a Date compare, not a string compare', () => {
    // The DB stamps created_at as `...+00:00`; the constant uses `Z`. A
    // lexicographic compare ('+' < 'Z') would mislabel this same-instant row
    // as pre-fix and hide the parser-drift canary (LRN-035). Pin the row to
    // the cutover instant in +00:00 form and require ANOMALY.
    const sameInstant = MODEL_FIX_CUTOVER_TS.replace('Z', '+00:00');
    expect(
      classifyModel(
        row({
          non_null_sessions: 0,
          null_claude_sessions: 1,
          null_claude_max_created_at: sameInstant,
        }),
      ),
    ).toBe('(unknown — ANOMALY)');
    // And a string compare would have produced the wrong answer here:
    expect(sameInstant < MODEL_FIX_CUTOVER_TS).toBe(true); // '+' < 'Z'
  });

  it('renders (unknown — ANOMALY) for a NULL-model claude row strictly after the cutover', () => {
    expect(
      classifyModel(
        row({
          non_null_sessions: 0,
          null_claude_sessions: 1,
          null_claude_max_created_at: '2026-06-13T00:00:00+00:00',
        }),
      ),
    ).toBe('(unknown — ANOMALY)');
  });
});

describe('toTopRows', () => {
  it('sorts by total_tokens DESC and slices to N', () => {
    const rollup: RollupRow[] = [
      row({ thread_id: 'A', total_tokens: 100 }),
      row({ thread_id: 'B', total_tokens: 900 }),
      row({ thread_id: 'C', total_tokens: 300 }),
      row({ thread_id: 'D', total_tokens: 50 }),
    ];
    const top = toTopRows(rollup, 2);
    expect(top.map((r) => r.threadId)).toEqual(['B', 'C']);
    expect(top.map((r) => r.totalTokens)).toEqual([900, 300]);
  });

  it('ranks on total_tokens ONLY — cache reads never enter the churn ordering', () => {
    // Row LOW has a huge cache_read but small churn; row HIGH has small cache
    // but large churn. The churn invariant requires HIGH to outrank LOW.
    const rollup: RollupRow[] = [
      row({
        thread_id: 'LOW',
        input_tokens: 10,
        output_tokens: 5,
        reasoning_tokens: 0,
        cache_read_tokens: 9_999_999,
        total_tokens: 15,
      }),
      row({
        thread_id: 'HIGH',
        input_tokens: 1000,
        output_tokens: 500,
        reasoning_tokens: 200,
        cache_read_tokens: 1,
        total_tokens: 1700,
      }),
    ];
    const top = toTopRows(rollup, 10);
    expect(top.map((r) => r.threadId)).toEqual(['HIGH', 'LOW']);
    // The mapped row carries cache as a secondary number, but totalTokens is
    // the SQL total (= input+output+reasoning), never input+...+cache.
    const low = top.find((r) => r.threadId === 'LOW')!;
    expect(low.totalTokens).toBe(15);
    expect(low.cacheReadTokens).toBe(9_999_999);
    expect(low.totalTokens).not.toBe(low.inputTokens + low.outputTokens + low.cacheReadTokens);
  });

  it('breaks ties by sessions DESC then thread id ASC for stable output', () => {
    const rollup: RollupRow[] = [
      row({ thread_id: 'B', total_tokens: 100, sessions: 1 }),
      row({ thread_id: 'A', total_tokens: 100, sessions: 1 }),
      row({ thread_id: 'C', total_tokens: 100, sessions: 5 }),
    ];
    expect(toTopRows(rollup, 10).map((r) => r.threadId)).toEqual(['C', 'A', 'B']);
  });

  it('carries the derived modelLabel through onto each row', () => {
    const rollup: RollupRow[] = [
      row({
        thread_id: 'A',
        total_tokens: 10,
        non_null_sessions: 1,
        model_distinct: 1,
        model_any: 'claude-opus-4-8[1m]',
      }),
    ];
    expect(toTopRows(rollup, 10)[0].modelLabel).toBe('claude-opus-4-8[1m]');
  });
});
