/** Owner return and selected safe reads; server enforces the proof lane. */
import { request } from './client';
import type { DocumentRevisionCommand, DocumentRevisionProof, DocumentRevisionReceipt } from './types';

export const startDocumentRevision = (slug: string, instanceId: string, body: DocumentRevisionCommand): Promise<DocumentRevisionReceipt> =>
  request(`/orgs/${slug}/workflows/instances/${encodeURIComponent(instanceId)}/revisions`, { method: 'POST', body });

export const getDocumentRevision = (slug: string, revisionOperationId: string, proof: DocumentRevisionProof): Promise<DocumentRevisionReceipt> =>
  request(`/orgs/${slug}/workflows/revisions/${encodeURIComponent(revisionOperationId)}`, { params: proof });
