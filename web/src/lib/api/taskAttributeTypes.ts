/** Wire mirrors of task_attribute_models.py. Revisions are per task/field. */
export type AttributeScalar = number | string | boolean;
export type AttributePresence = { present: false } | { present: true; value: AttributeScalar };
export interface AttributeDefinition {
  definition_id: string;
  version: number;
  key: string;
  type: 'numeric' | 'string' | 'boolean';
  name: string;
  description: string | null;
  constraints: Record<string, number | boolean>;
  created_at: string;
  recorded_at: string;
  first_used_at: string | null;
  archived_at: string | null;
  archived: boolean;
}
export interface AttributeEditability {
  can_edit: boolean;
  refusal_reason: { code: string } | null;
}
export interface AttributeDefinitionEditability {
  can_create: boolean;
  can_edit_metadata: boolean;
  can_edit_structure: boolean;
  can_archive: boolean;
  refusal_reason: { code: string } | null;
}
export interface AttributeDefinitionRead {
  definition: AttributeDefinition;
  editability: AttributeDefinitionEditability;
}
export interface AttributeDefinitionsRead {
  definitions: AttributeDefinition[];
  editability: AttributeDefinitionEditability;
}
export interface AttributeEvent {
  event_id: number;
  definition_id: string;
  definition_version: number;
  definition: AttributeDefinition;
  revision: number;
  actor_id: string;
  actor_kind: string;
  actor: { canonical_id: string; kind: string; addressable_name: string | null };
  at: string;
  source: 'admission' | 'edit' | 'clear';
  old: AttributePresence;
  new: AttributePresence;
  note?: string;
}
export interface AttributeHistory {
  history_state: 'complete' | 'incomplete' | 'unavailable';
  events: AttributeEvent[];
}
export interface TaskAttributeRead {
  definition: AttributeDefinition;
  current: AttributePresence & { revision: number; definition_version?: number; updated_at?: string };
  initial: { state: 'unknown' } | { state: 'unset' } | {
    state: 'set'; value: AttributeScalar; definition_version: number; definition: AttributeDefinition;
  };
  admission: { state: 'unavailable' } | { state: 'recorded'; actor_id: string; actor_kind: string; at: string };
  provenance: 'supplied_at_admission' | 'not_supplied_before_dispatch' | 'definition_added_later' | 'timing_unknown';
  history: AttributeHistory;
  editability: AttributeEditability;
}
export interface TaskAttributesRead {
  task_id: string;
  editability: AttributeEditability;
  attributes: TaskAttributeRead[];
}
export type TaskAttributeHistoryRead = AttributeHistory & { task_id: string };
export type AttributeChange = {
  definition_id: string; expected_version: number; expected_revision: number; note?: string;
} & ({ op: 'set'; value: AttributeScalar } | { op: 'clear' });
export type AttributeDefinitionFields = Pick<AttributeDefinition, 'key' | 'type' | 'name'> &
  Partial<Pick<AttributeDefinition, 'description' | 'constraints'>>;
/** ApiError.detail retains this envelope; mutations require deliberate conflict handling. */
export interface AttributeErrorDetail {
  code: string;
  errors?: Record<string, string[]>;
  current?: AttributeDefinitionRead | TaskAttributesRead;
  conflicts?: { definition_id: string; expected_revision: number; current_revision: number }[];
}
