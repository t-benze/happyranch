/** Mirror of runtime/daemon/routes/workflow_templates.py. */
import { request } from './client';
import type { WorkflowDocumentReviewDefinition } from './types';

export interface WorkflowTemplatePublisher {
  principal_kind: 'agent' | 'human';
  principal_id: string;
  proof_kind: 'task_session' | 'founder_bearer';
  task_id?: string;
  session_id?: string;
}

export interface WorkflowTemplateVersion {
  identity_id: string;
  version_id: string;
  namespace: string;
  template_name: string;
  version: number;
  definition_bytes_base64: string;
  definition_json: string;
  definition_digest: string;
  compiler_pin: string;
  validator_pin: string;
  source_pin: string;
  publisher: WorkflowTemplatePublisher;
  published_at: string;
}

export interface PublishWorkflowTemplateInput<Definition = unknown> {
  team_slug: string;
  operation_key: string;
  template_name: string;
  expected_current_version: number;
  definition: Definition;
}

export type PublishDocumentWorkflowTemplateInput = PublishWorkflowTemplateInput<WorkflowDocumentReviewDefinition>;

export const publishWorkflowTemplate = (
  slug: string,
  body: PublishWorkflowTemplateInput,
): Promise<WorkflowTemplateVersion> =>
  request(`/orgs/${slug}/workflows/templates/publish`, { method: 'POST', body });

export const listWorkflowTemplates = (
  slug: string,
  teamSlug: string,
): Promise<{ templates: WorkflowTemplateVersion[] }> =>
  request(`/orgs/${slug}/workflows/templates`, { params: { team_slug: teamSlug } });

export const getWorkflowTemplate = (
  slug: string,
  teamSlug: string,
  templateName: string,
  version: number,
): Promise<WorkflowTemplateVersion> =>
  request(
    `/orgs/${slug}/workflows/templates/${encodeURIComponent(teamSlug)}/${encodeURIComponent(templateName)}/${version}`,
  );
