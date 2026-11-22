/**
 * Pure U8a presentation model over the U7 sequential projections.
 *
 * Everything here derives from RECORDED facts returned by the U7 GETs: the
 * admitted definition text, run state, R-M3-7 effective limits and reducer
 * counters, visits with their recorded outcomes/findings and observation holds.
 * Nothing invents a task, progress, cause or assessment. Unknown tokens stay verbatim.
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

export type SetupDefView = Omit<DefView, 'limits'> & {
  limits: import('./api/workflowSequential').SequentialLimits | import('./api/workflowSequential').GenericSequentialLimits;
};
/** Setup supports the generic ABI additively; the historical parser above stays @1. */
export function parseSetupDefinition(text: string): SetupDefView | null {
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { return null; }
  if (!isObj(raw)) return null;
  if (raw.format === 'workflow-sequential-definition@1') {
    if (!isObj(raw.limits) || !['content_returns', 'max_content_epochs', 'max_visits', 'max_events'].every(k => Number.isSafeInteger((raw.limits as Record<string, unknown>)[k]))
      || (raw.limits.content_returns as number) < 0 || (raw.limits.content_returns as number) > 2
      || (raw.limits.max_content_epochs as number) < 1 || (raw.limits.max_content_epochs as number) > 3
      || (raw.limits.max_visits as number) < 1 || (raw.limits.max_events as number) < 1) return null;
    return parseDefinition(text);
  }
  if (raw.format !== 'workflow-sequential-definition@2' || !Array.isArray(raw.nodes) || !isObj(raw.roles)
    || typeof raw.identity !== 'string' || !Number.isSafeInteger(raw.version) || !isObj(raw.limits)) return null;
  const limits = raw.limits;
  if (!['max_visits', 'max_events', 'max_recoveries'].every(k => Number.isSafeInteger(limits[k]))
    || (limits.max_visits as number) < 1 || (limits.max_visits as number) > 64
    || (limits.max_events as number) < 1 || (limits.max_events as number) > 4096
    || (limits.max_recoveries as number) < 0 || (limits.max_recoveries as number) > 64
    || Object.keys(limits).some(k => !['max_visits', 'max_events', 'max_recoveries', 'node_revisions'].includes(k))
    || (limits.node_revisions !== undefined && (!isObj(limits.node_revisions)
      || !Object.values(limits.node_revisions).every(v => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= 63)))) return null;
  const roles: DefRole[] = [];
  for (const [name, r] of Object.entries(raw.roles)) {
    if (!isObj(r) || typeof r.eligibility !== 'string' || !['worker', 'registered-team-manager'].includes(r.eligibility)
      || !Array.isArray(r.independent_of) || !r.independent_of.every(x => typeof x === 'string')) return null;
    roles.push({ name, eligibility: r.eligibility, independentOf: strs(r.independent_of) });
  }
  const nodes: DefNode[] = [];
  for (const n of raw.nodes) {
    if (!isObj(n) || typeof n.id !== 'string' || typeof n.role !== 'string' || !isObj(n.output_contract) || !isObj(n.outcomes)) return null;
    const outcomes: DefNode['outcomes'] = [];
    for (const [outcome, value] of Object.entries(n.outcomes)) {
      const e = edge(value);
      if (!e) return null;
      outcomes.push({ outcome, edge: e });
    }
    nodes.push({ id: n.id, role: n.role, responsibilities: strs(n.responsibilities),
      inputs: Array.isArray(n.inputs) ? n.inputs.filter(isObj).map(i => ({ name: str(i.name), source: str(i.source), required: i.required !== false })) : [],
      output: { slot: str(n.output_contract.slot), type: str(n.output_contract.type) }, checks: [], outcomes });
  }
  if (!roles.length || !nodes.length || nodes.some(n => !roles.some(r => r.name === n.role))) return null;
  return { identity: raw.identity, version: raw.version as number, description: str(raw.description), entry: str(raw.entry_node),
    roles, nodes, limits: { max_visits: limits.max_visits as number, max_events: limits.max_events as number,
      max_recoveries: limits.max_recoveries as number,
      ...(limits.node_revisions !== undefined ? { node_revisions: limits.node_revisions as Record<string, number> } : {}) },
    inputs: Array.isArray(raw.required_inputs) ? raw.required_inputs.filter(isObj).map(i => ({ name: str(i.name), contract: str(i.contract), required: i.required !== false })) : [],
    terminals: isObj(raw.terminal_conditions) ? Object.entries(raw.terminal_conditions).map(([name, c]) => ({ name, output: isObj(c) ? str(c.output) : '', requires: [] })) : [] };
}

export type Visit = SequentialRunDetail['visits'][number];

/** The admitted definition's `next` edge for a recorded outcome, with its target node. */
export function nextEdge(def: DefView, nodeId: string, outcome: string | null): { target: DefNode; returning: boolean } | null {
  const node = def.nodes.find(n => n.id === nodeId);
  const e = node?.outcomes.find(o => o.outcome === outcome)?.edge;
  const target = e && e.action === 'next' ? def.nodes.find(n => n.id === e.node) ?? null : null;
  return e && e.action === 'next' && target ? { target, returning: !!e.consume_counter } : null;
}

/** Which effective ceiling the reducer's limit predicate found spent. */
export type ExhaustedLimit = 'revision' | 'visits' | 'events';

export type RunHeadline =
  | { kind: 'completed' } | { kind: 'failed' } | { kind: 'cancelled' }
  | { kind: 'exhausted'; target: DefNode; visit: Visit; limit: ExhaustedLimit }
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
 * Exhaustion mirrors runtime/workflows/sequential_reducer.py over RunDetail
 * `effective_limits` (L: definition ∩ original settings ∩ admitted lower limits)
 * and `reducer_counters` (R: the last transition's `after`). Neither the
 * settings-snapshot `limits` nor the database event count is used. For the held
 * selected visit V whose recorded outcome follows a `next` edge, with no later
 * visit and no observation hold, the reducer reached `limit_exhausted` when its
 * pre-transition counters (R with one event fewer) met
 * `visits >= L.visits || events + 1 >= L.events || (returning && (returns >= L.returns || epoch >= L.epochs))`,
 * or the run was already outside L before that result. A returning edge whose
 * epoch advanced (R.epoch > V.epoch) was a required-input hold, not exhaustion.
 * Decision.reason is not stored: this is a derivation. The reducer checks
 * settlement/output evidence before limits with the same counter shape, so
 * such a hold cannot be told apart when the limit predicate also holds.
 */
export function exhaustedLimit(detail: SequentialRunDetail, visit: Visit, returning: boolean): ExhaustedLimit | null {
  const L = detail.effective_limits;
  const R = detail.reducer_counters;
  if (!L || !R) return null;
  if (returning && R.epoch !== visit.epoch) return null;
  const outside = R.visits > L.max_visits || R.epoch > L.max_content_epochs || R.content_returns > L.content_returns;
  if (returning && (R.content_returns >= L.content_returns || R.epoch >= L.max_content_epochs)) return 'revision';
  if (R.visits >= L.max_visits) return 'visits';
  if (R.events >= L.max_events) return 'events';
  return outside ? 'revision' : null;
}

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
    const edgeFacts = def && heldVisit ? nextEdge(def, heldVisit.node_id, heldVisit.outcome) : null;
    const limit = edgeFacts && heldVisit ? exhaustedLimit(detail, heldVisit, edgeFacts.returning) : null;
    const later = heldVisit ? visits.some(v => v.ordinal > heldVisit.ordinal) : true;
    const hold = detail.holds.find(h => h.visit_id === heldVisit?.id) ?? detail.holds[0] ?? null;
    headline = edgeFacts && heldVisit && limit && !later && !hold
      ? { kind: 'exhausted', target: edgeFacts.target, visit: heldVisit, limit }
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


/** S7a enum-only facts for S7b. Never infer remote verification from report text. */
export function pluralTaskLinks(value: import('./api/workflowSequential').SequentialTaskRuns) {
  return value.runs.map(link => {
    const run = link.abi === '@1' ? link.run : link;
    return { id: run.id, abi: link.abi, historyLabel: link.history_label,
      relationships: run.tasks.filter(task => task.task_id === value.task_id) };
  });
}

/** One accepted detail snapshot owns its visits and assignments. */
export function genericVisitAssignments(detail: import('./api/workflowSequential').GenericSequentialRunDetail) {
  const assignments = new Map(detail.assignments.map(value => [value.id, value]));
  return detail.visits.map(visit => ({ visit, assignment: assignments.get(visit.assignment_id) ?? null }));
}
