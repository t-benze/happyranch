/**
 * Pure U8a presentation model over the U7 sequential projections.
 *
 * Everything here derives from RECORDED facts returned by the U7 GETs: the
 * admitted definition text, run state/counters/original limits, visits with
 * their recorded outcomes/findings and observation holds. Nothing invents a
 * task, progress, cause or assessment. Unknown tokens stay verbatim.
 */
import type { SequentialRun, SequentialRunDetail } from './api/workflowSequential';
export type { SequentialRun, SequentialRunDetail };

export type DefEdge =
  | { action: 'next'; node: string; consume_counter?: string | null; invalidate_dependents?: boolean }
  | { action: 'hold'; owner: string; remedy: string }
  | { action: 'fail'; code: string }
  | { action: 'terminal'; condition: string };
export interface DefNode {
  id: string; role: string; responsibilities: string[];
  inputs: { name: string; source: string; required: boolean }[];
  output: { slot: string; type: string }; checks: string[];
  outcomes: { outcome: string; edge: DefEdge }[];
}
export interface DefRole { name: string; eligibility: string; independentOf: string[] }
export interface DefLimits { content_returns: number; max_content_epochs: number; max_visits: number; max_events: number }
export interface DefView {
  identity: string; version: number; description: string; entry: string;
  roles: DefRole[]; nodes: DefNode[]; limits: DefLimits;
  inputs: { name: string; contract: string; required: boolean }[];
  terminals: { name: string; output: string; requires: string[] }[];
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const int = (v: unknown): number => (typeof v === 'number' && Number.isSafeInteger(v) ? v : 0);

function edge(v: unknown): DefEdge | null {
  if (!isObj(v)) return null;
  if (v.action === 'next' && typeof v.node === 'string') {
    return { action: 'next', node: v.node, consume_counter: typeof v.consume_counter === 'string' ? v.consume_counter : null, invalidate_dependents: v.invalidate_dependents === true };
  }
  if (v.action === 'hold') return { action: 'hold', owner: str(v.owner), remedy: str(v.remedy) };
  if (v.action === 'fail') return { action: 'fail', code: str(v.code) };
  if (v.action === 'terminal') return { action: 'terminal', condition: str(v.condition) };
  return null;
}

/** Parses the original canonical definition text; null when it cannot be displayed structurally. */
export function parseDefinition(text: string): DefView | null {
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { return null; }
  if (!isObj(raw) || raw.format !== 'workflow-sequential-definition@1' || !Array.isArray(raw.nodes) || !isObj(raw.roles)) return null;
  const nodes: DefNode[] = [];
  for (const n of raw.nodes) {
    if (!isObj(n) || typeof n.id !== 'string' || typeof n.role !== 'string' || !isObj(n.output_contract) || !isObj(n.outcomes)) return null;
    const outcomes: DefNode['outcomes'] = [];
    for (const [outcome, value] of Object.entries(n.outcomes)) {
      const e = edge(value);
      if (!e) return null;
      outcomes.push({ outcome, edge: e });
    }
    nodes.push({
      id: n.id, role: n.role, responsibilities: strs(n.responsibilities),
      inputs: Array.isArray(n.inputs) ? n.inputs.filter(isObj).map(i => ({ name: str(i.name), source: str(i.source), required: i.required !== false })) : [],
      output: { slot: str(n.output_contract.slot), type: str(n.output_contract.type) }, checks: strs(n.completion_checks), outcomes,
    });
  }
  const limits = isObj(raw.limits) ? raw.limits : {};
  return {
    identity: str(raw.identity), version: int(raw.version), description: str(raw.description), entry: str(raw.entry_node),
    roles: Object.entries(raw.roles).map(([name, r]) => ({ name, eligibility: isObj(r) ? str(r.eligibility) : '', independentOf: isObj(r) ? strs(r.independent_of) : [] })),
    nodes,
    limits: { content_returns: int(limits.content_returns), max_content_epochs: int(limits.max_content_epochs), max_visits: int(limits.max_visits), max_events: int(limits.max_events) },
    inputs: Array.isArray(raw.required_inputs) ? raw.required_inputs.filter(isObj).map(i => ({ name: str(i.name), contract: str(i.contract), required: i.required !== false })) : [],
    terminals: isObj(raw.terminal_conditions) ? Object.entries(raw.terminal_conditions).map(([name, c]) => ({ name, output: isObj(c) ? str(c.output) : '', requires: isObj(c) ? strs(c.requires) : [] })) : [],
  };
}

export type Visit = SequentialRunDetail['visits'][number];

/** The recorded outcome is a counter-consuming return in the admitted definition. */
export function isReturnOutcome(def: DefView, nodeId: string, outcome: string | null): DefNode | null {
  const node = def.nodes.find(n => n.id === nodeId);
  const e = node?.outcomes.find(o => o.outcome === outcome)?.edge;
  return e && e.action === 'next' && e.consume_counter ? def.nodes.find(n => n.id === e.node) ?? null : null;
}

export type RunHeadline =
  | { kind: 'completed' } | { kind: 'failed' } | { kind: 'cancelled' }
  | { kind: 'exhausted'; target: DefNode; visit: Visit }
  | { kind: 'held'; cause: string | null; visit: Visit | null }
  | { kind: 'active'; visit: Visit | null };

export interface RunFacts {
  headline: RunHeadline;
  /** Visits keyed by node id, ordered by ordinal (执行轮次). */
  byNode: Map<string, Visit[]>;
  active: Visit | null;
  /** Recorded assessments for the highest recorded content revision, never upgraded or dropped. */
  pendingFinal: Visit[];
}

/**
 * Exhaustion uses the sequential reducer's own predicate over recorded values:
 * effective limit = the lower of the admitted definition and the run's
 * original limits; a returning outcome holds when returns/epochs/visits are
 * spent. No later visit may exist (no successor was created).
 */
export function deriveRun(detail: SequentialRunDetail, def: DefView | null): RunFacts {
  const visits = [...detail.visits].sort((a, b) => a.ordinal - b.ordinal);
  const byNode = new Map<string, Visit[]>();
  for (const v of visits) byNode.set(v.node_id, [...(byNode.get(v.node_id) ?? []), v]);
  const run = detail.run;
  const active = run.active_visit_id ? visits.find(v => v.id === run.active_visit_id) ?? null : null;
  const finalNodes = new Set(def?.nodes.filter(n => n.output.type === 'guarded-repository-merge@1').map(n => n.id) ?? []);
  const pendingFinal = visits.filter(v => finalNodes.has(v.node_id) && v.outcome === null && ['queued', 'running', 'waiting'].includes(v.state));
  let headline: RunHeadline;
  if (run.state === 'completed') headline = { kind: 'completed' };
  else if (run.state === 'failed') headline = { kind: 'failed' };
  else if (run.state === 'cancelled') headline = { kind: 'cancelled' };
  else if (run.state === 'held') {
    const heldVisit = active ?? [...visits].reverse().find(v => v.selected) ?? null;
    const target = def && heldVisit ? isReturnOutcome(def, heldVisit.node_id, heldVisit.outcome) : null;
    const lim = def ? {
      returns: Math.min(def.limits.content_returns, detail.limits.content_returns),
      epochs: Math.min(def.limits.max_content_epochs, detail.limits.max_content_epochs),
      visits: Math.min(def.limits.max_visits, detail.limits.max_visits),
    } : null;
    const c = run.counters;
    const spent = !!lim && (c.content_returns >= lim.returns || c.epoch >= lim.epochs || c.visits >= lim.visits);
    const later = heldVisit ? visits.some(v => v.ordinal > heldVisit.ordinal) : true;
    const hold = detail.holds.find(h => h.visit_id === heldVisit?.id) ?? detail.holds[0] ?? null;
    headline = target && heldVisit && spent && !later && !hold
      ? { kind: 'exhausted', target, visit: heldVisit }
      : { kind: 'held', cause: hold?.cause ?? null, visit: heldVisit };
  } else headline = { kind: 'active', visit: active };
  return { headline, byNode, active, pendingFinal };
}

/** Real task links only, by recorded relationship. */
export function taskLinks(run: SequentialRun): { source: string | null; carrier: string | null; activeTask: string | null } {
  return {
    source: run.tasks.find(t => t.relationship === 'source')?.task_id ?? null,
    carrier: run.tasks.find(t => t.relationship === 'carrier')?.task_id ?? null,
    activeTask: run.active_visit_id ? run.tasks.find(t => t.visit_id === run.active_visit_id)?.task_id ?? null : null,
  };
}
