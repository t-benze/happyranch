/**
 * Locale-neutral mapping from S3b closed Instill codes to catalog keys
 * (THR-296 S5). Unknown codes render verbatim beside generic copy — a daemon
 * code is never hidden or reworded into a claim the server did not make.
 */
import type { MessageKey } from '@/lib/i18n';

/** Confirmation/retry refusal codes (HTTP 404/409/422 `detail.code`). */
export const REFUSAL: Record<string, MessageKey> = {
  knowledge_import_busy_finish_wait_then_retry: 'knowledge.instill.refusal.busy',
  knowledge_import_recipient_ineligible: 'knowledge.instill.refusal.recipient',
  knowledge_import_recipient_not_active_authorized: 'knowledge.instill.refusal.recipient',
  knowledge_import_recipient_not_executable: 'knowledge.instill.refusal.recipient',
  knowledge_import_recipient_not_found: 'knowledge.instill.refusal.recipient',
  knowledge_import_recipient_unavailable: 'knowledge.instill.refusal.recipientUnavailable',
  knowledge_import_memory_uninitialized_or_unwritable: 'knowledge.instill.refusal.memory',
  knowledge_import_recipient_or_idle_unavailable: 'knowledge.instill.refusal.idleUnknown',
  knowledge_import_idle_read_failed: 'knowledge.instill.refusal.idleUnknown',
  knowledge_import_source_equals_recipient: 'knowledge.instill.refusal.sameAgent',
  knowledge_import_ready_same_org_archive_required: 'knowledge.instill.refusal.archive',
  knowledge_import_archive_version_mismatch: 'knowledge.instill.refusal.archive',
  knowledge_import_unknown_item: 'knowledge.instill.refusal.items',
  knowledge_import_item_ineligible: 'knowledge.instill.refusal.items',
  knowledge_import_confirmation_conflict: 'knowledge.instill.refusal.conflict',
  knowledge_import_inflight_reopen_operation: 'knowledge.instill.refusal.inflight',
  knowledge_import_not_found: 'knowledge.instill.refusal.notFound',
};

export function refusalKey(code: string): MessageKey {
  if (REFUSAL[code]) return REFUSAL[code];
  if (code.startsWith('knowledge_import_idle_incomplete_') || code.startsWith('knowledge_import_attribution_unknown_')) {
    return 'knowledge.instill.refusal.idleUnknown';
  }
  return 'knowledge.instill.refusal.generic';
}

/** Local refusals (InstillLedgerError codes). */
export const LEDGER: Record<string, MessageKey> = {
  ledger_unavailable: 'knowledge.instill.ledger.unavailable',
  ledger_write_failed: 'knowledge.instill.ledger.writeFailed',
  unresolved_attempt: 'knowledge.instill.ledger.unresolved',
  recovery_refused: 'knowledge.instill.ledger.recoveryRefused',
};

/** Busy blocker kinds from the S3 exhaustive census. */
export const BLOCKER: Record<string, MessageKey> = {
  task: 'knowledge.instill.blocker.task',
  thread_invocation: 'knowledge.instill.blocker.thread',
  schedule: 'knowledge.instill.blocker.schedule',
  work_hour: 'knowledge.instill.blocker.workHour',
  dream: 'knowledge.instill.blocker.dream',
  job: 'knowledge.instill.blocker.job',
};

/** Per-item result reasons. */
export const ITEM_REASON: Record<string, MessageKey> = {
  durable_existing_mapping: 'knowledge.instill.reason.existing',
  reconciled_mapping: 'knowledge.instill.reason.reconciled',
  knowledge_import_incomplete_intent_retry_original_operation: 'knowledge.instill.reason.otherOperation',
  uncertainty_hold_reconcile_reserved_id: 'knowledge.instill.reason.held',
  reserved_identity_conflict: 'knowledge.instill.reason.identityConflict',
  failed_before_write: 'knowledge.instill.reason.notWritten',
};

export const STATE_KEY: Record<string, MessageKey> = {
  Queued: 'knowledge.instill.state.queued',
  Processing: 'knowledge.instill.state.processing',
  Succeeded: 'knowledge.instill.state.succeeded',
  'Partially failed': 'knowledge.instill.state.partial',
  Failed: 'knowledge.instill.state.failed',
};

export const PHASE_KEY: Record<string, MessageKey> = {
  confirmed: 'knowledge.instill.phase.confirmed',
  writing: 'knowledge.instill.phase.writing',
  finished: 'knowledge.instill.phase.finished',
};
