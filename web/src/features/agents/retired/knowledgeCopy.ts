/**
 * Locale-neutral mapping from S1/S2 closed codes to catalog keys (THR-296 S4).
 * Unknown codes render verbatim — a daemon code is never hidden or reworded
 * into a claim the server did not make.
 */
import type { MessageKey } from '@/lib/i18n';

export const COVERAGE_LABEL: Record<string, MessageKey> = {
  runtime_memory: 'knowledge.coverage.runtime',
  claude_memory: 'knowledge.coverage.claude',
  legacy_learnings: 'knowledge.coverage.legacy',
  other_provider_memory: 'knowledge.coverage.otherProvider',
  shared_kb_bodies: 'knowledge.coverage.sharedKb',
  task_thread_history: 'knowledge.coverage.history',
  credentials_instructions_skills_sessions_caches: 'knowledge.coverage.alwaysExcluded',
};

export const COVERAGE_QUALIFIER: Record<string, MessageKey> = {
  runtime_memory: 'knowledge.coverage.runtimeQualifier',
  claude_memory: 'knowledge.coverage.claudeQualifier',
  legacy_learnings: 'knowledge.coverage.legacyQualifier',
  other_provider_memory: 'knowledge.coverage.otherProviderQualifier',
  shared_kb_bodies: 'knowledge.coverage.sharedKbQualifier',
  task_thread_history: 'knowledge.coverage.historyQualifier',
  credentials_instructions_skills_sessions_caches: 'knowledge.coverage.alwaysExcludedQualifier',
};

/** Reason/error codes (S1 capture). Claude reasons arrive as `not captured — <words>`; errors[0] carries the code. */
export const COVERAGE_REASON: Record<string, MessageKey> = {
  all_structured_entries: 'knowledge.reason.allStructuredEntries',
  verified_empty: 'knowledge.reason.verifiedEmpty',
  retained_path_evidence_and_collision_check: 'knowledge.reason.claudeIncluded',
  legacy_only_unavailable: 'knowledge.reason.legacyOnly',
  runtime_corpus_absent: 'knowledge.reason.runtimeAbsent',
  runtime_corpus_unreadable: 'knowledge.reason.runtimeUnreadable',
  inconsistent_input: 'knowledge.reason.inconsistent',
  missing_claude_directory: 'knowledge.reason.claudeMissingDirectory',
  missing_path_evidence: 'knowledge.reason.claudeMissingPathEvidence',
  unproven_retention: 'knowledge.reason.claudeUnprovenRetention',
  ambiguous_source_attribution: 'knowledge.reason.claudeAmbiguous',
  claude_corpus_unreadable: 'knowledge.reason.claudeUnreadable',
  unsupported_without_migration: 'knowledge.reason.legacyExcluded',
  existence_and_retention_unverified: 'knowledge.reason.otherProviderExcluded',
  pointers_only_no_body_import: 'knowledge.reason.sharedKbExcluded',
  no_unrestricted_history_ingestion: 'knowledge.reason.historyExcluded',
  outside_declared_corpus: 'knowledge.reason.alwaysExcluded',
};

/** The reason code for a coverage row: the first error when present, else the reason. */
export function coverageCode(entry: { reason: string; errors: string[] }): string {
  const first = entry.errors[0];
  if (first) return first.startsWith('declared_claude_corpus_failed:') ? 'claude_corpus_unreadable' : first.split(':')[0];
  return entry.reason;
}

export const PHASE_LABEL: Record<string, MessageKey> = {
  queued: 'knowledge.phase.queued',
  awaiting_task: 'knowledge.phase.awaitingTask',
  curation: 'knowledge.phase.curation',
  awaiting_terminal_task: 'knowledge.phase.awaitingTerminalTask',
  completion_received_awaiting_terminal: 'knowledge.phase.completionReceived',
  completion_evidence_unavailable: 'knowledge.phase.completionEvidenceUnavailable',
  task_binding_unavailable: 'knowledge.phase.taskBindingUnavailable',
  processor_result_failed: 'knowledge.phase.processorResultFailed',
  input_failed: 'knowledge.phase.inputFailed',
  failed: 'knowledge.phase.failed',
  published: 'knowledge.phase.published',
};

export function failureKey(code: string): MessageKey {
  if (code === 'processor_missing_choose_existing_owner') return 'knowledge.failure.processorMissing';
  if (code === 'processor_ineligible_choose_existing_owner' || code.startsWith('processor_unknown_')
    || code.startsWith('processor_owner_')) return 'knowledge.failure.processorIneligible';
  if (code.startsWith('processor_outcome_') || code.startsWith('processor_decision_')
    || code.startsWith('processor_task_')) return 'knowledge.failure.processorOutcome';
  if (code === 'curation_input_too_large') return 'knowledge.failure.inputTooLarge';
  if (code === 'curation_publication_failed') return 'knowledge.failure.publication';
  if (code.startsWith('curation_')) return 'knowledge.failure.validation';
  return 'knowledge.failure.generic';
}

export const ITEM_CATEGORY: Record<string, MessageKey> = {
  lesson_fact: 'knowledge.item.lesson',
  procedure: 'knowledge.item.procedure',
  founder_decision_preference: 'knowledge.item.founderDecision',
  open_work_reference: 'knowledge.item.openWork',
};

export const ITEM_FLAG: Record<string, MessageKey> = {
  historical: 'knowledge.flag.historical',
  uncertain: 'knowledge.flag.uncertain',
  conflict: 'knowledge.flag.conflict',
  superseded: 'knowledge.flag.superseded',
  evicted: 'knowledge.flag.evicted',
  stale: 'knowledge.flag.stale',
  promoted: 'knowledge.flag.promoted',
  attribution_unverified: 'knowledge.flag.attributionUnverified',
};

export const EXCLUSION_LABEL: Record<string, MessageKey> = {
  credentials: 'knowledge.exclusion.credentials',
  instructions_permissions: 'knowledge.exclusion.instructions',
  skills_identity_sessions_caches: 'knowledge.exclusion.skills',
  unrelated_private: 'knowledge.exclusion.private',
  task_conversation_history: 'knowledge.exclusion.history',
  incidental: 'knowledge.exclusion.incidental',
  unsupported: 'knowledge.exclusion.unsupported',
  unusable: 'knowledge.exclusion.unusable',
  redundant: 'knowledge.exclusion.redundant',
};

export const DISPOSITION_LABEL: Record<string, MessageKey> = {
  selected: 'knowledge.disposition.selected',
  reference_only: 'knowledge.disposition.referenceOnly',
  excluded: 'knowledge.disposition.excluded',
};

export const SOURCE_ERROR: Record<string, MessageKey> = {
  unknown_retired_source: 'knowledge.sourceError.unknown',
  source_not_retired: 'knowledge.sourceError.notRetired',
  source_not_retired_worker: 'knowledge.sourceError.notWorker',
  retained_workspace_missing: 'knowledge.sourceError.workspaceMissing',
  retained_identity_unreadable_or_invalid: 'knowledge.sourceError.identityUnreadable',
};

export const SUBMIT_ERROR: Record<string, MessageKey> = {
  knowledge_source_inflight_reopen_operation: 'knowledge.submitError.inflight',
  knowledge_submission_conflict: 'knowledge.submitError.conflict',
};
