import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Paperclip, X } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { FormField } from '@/design-system/patterns/FormField';
import { MentionTextarea } from '@/design-system/patterns/MentionTextarea';
import { RecipientsInput } from '@/design-system/patterns/RecipientsInput';
import { artifacts as artifactsApi, ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import {
  MAX_THREAD_ATTACHMENTS,
  REMOVE_ATTACHMENT_LABEL,
  attachmentContentType,
  safeArtifactName,
} from '@/lib/threadAttachments';
import { useComposeThread } from '@/hooks/threads';
import { describeError } from '@/lib/threadErrors';
import type { AgentSummary } from '@/lib/api/types';
import type { PendingAttachment } from '@/design-system/patterns/Composer';
import type { ThreadAttachmentRef } from '@/lib/api/types';

interface Prefill {
  subject?: string;
  recipients?: string[];
  body?: string;
  forwarded_from_id?: string;
  forwarded_from_kind?: 'thread';
}

interface Props {
  open: boolean;
  onClose: () => void;
  prefill?: Prefill;
  /** Called with the new thread_id on success. */
  onCreated: (threadId: string) => void;
  /** Agents list used for @-mention autocomplete in the body. */
  agents?: AgentSummary[];
}

export function NewThreadDialog({ open, onClose, prefill, onCreated, agents = [] }: Props): JSX.Element {
  const slug = useOrgSlug();
  const compose = useComposeThread();
  const [subject, setSubject] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [body, setBody] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Synchronous in-flight latch — prevents duplicate submits before
  // React Query's isPending state propagates (double-click, Enter+Send).
  const submittingRef = useRef(false);

  const idBase = useId();
  const subjectId = `${idBase}-subject`;
  const recipientsId = `${idBase}-recipients`;
  const bodyId = `${idBase}-body`;

  useEffect(() => {
    if (!open) return;
    submittingRef.current = false;
    setSubject(prefill?.subject ?? '');
    setRecipientsRaw(prefill?.recipients?.join(', ') ?? '');
    setBody(prefill?.body ?? '');
    setPendingAttachments([]);
    setErrorMsg(null);
  }, [open, prefill]);

  const submit = useCallback(async () => {
    // Guard against double-submit (double-click, Enter+Send race).
    // The ref is synchronous — no React render needed to block re-entry.
    if (submittingRef.current) return;
    submittingRef.current = true;

    setErrorMsg(null);
    const recipients = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (!subject.trim() || !recipients.length || (!body.trim() && !pendingAttachments.length)) {
      setErrorMsg('Subject, recipients, and a body or attachment are required.');
      submittingRef.current = false;
      return;
    }
    try {
      const refs: ThreadAttachmentRef[] = [];
      const generatedNames = new Map<string, number>();
      for (const pending of pendingAttachments) {
        let artifactName = safeArtifactName('thread-draft', pending.file);
        const count = (generatedNames.get(artifactName) ?? 0) + 1;
        generatedNames.set(artifactName, count);
        if (count > 1) {
          artifactName = safeArtifactName('thread-draft', pending.file, count);
        }
        const uploaded = await artifactsApi.uploadArtifact(slug, {
          file: pending.file,
          name: artifactName,
          agent: 'founder',
        });
        refs.push({
          artifact_name: uploaded.name,
          display_name: pending.file.name,
          content_type: attachmentContentType(pending.file),
        });
      }
      const result = await compose.mutateAsync({
        subject: subject.trim(),
        recipients,
        body_markdown: body.trim(),
        ...(refs.length ? { attachments: refs } : {}),
        ...(prefill?.forwarded_from_id
          ? {
              forwarded_from_id: prefill.forwarded_from_id,
              forwarded_from_kind: prefill.forwarded_from_kind,
            }
          : {}),
      });
      onCreated(result.thread_id);
      setPendingAttachments([]);
      onClose();
      // submittingRef remains true — dialog closes on success, so no
      // further re-entry is possible.
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
      );
      submittingRef.current = false;
    }
  }, [subject, recipientsRaw, body, pendingAttachments, prefill, compose, slug, onCreated, onClose]);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{prefill?.forwarded_from_id ? 'Forward thread' : 'New thread'}</DialogTitle>
          <DialogDescription className="sr-only">
            Compose a new thread with subject, recipients, and body.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField label="Subject" htmlFor={subjectId}>
            <input
              id={subjectId}
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="input"
              autoFocus
            />
          </FormField>
          <FormField
            label="Recipients (comma-separated agent names)"
            htmlFor={recipientsId}
          >
            <RecipientsInput
              id={recipientsId}
              value={recipientsRaw}
              onChange={setRecipientsRaw}
              agents={agents}
              placeholder="agent_a, agent_b"
            />
          </FormField>
          <FormField label="Body (Markdown)" htmlFor={bodyId}>
            <MentionTextarea
              id={bodyId}
              value={body}
              onChange={setBody}
              agents={agents}
              onSubmit={() => { if (!submittingRef.current) submit(); }}
              disabled={submittingRef.current || compose.isPending}
              rows={6}
            />
          </FormField>
          <FormField label="Attachments" htmlFor={`${idBase}-attachments`}>
            <label className="border-border-subtle bg-surface text-caption hover:bg-surface-hover inline-flex w-fit cursor-pointer items-center gap-2 rounded-md border px-2 py-1">
              <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Attach files</span>
              <input
                id={`${idBase}-attachments`}
                aria-label="Attach files"
                type="file"
                multiple
                className="sr-only"
                disabled={compose.isPending}
                onChange={(event) => {
                  const files = Array.from(event.currentTarget.files ?? []).slice(
                    0,
                    MAX_THREAD_ATTACHMENTS,
                  );
                  setPendingAttachments((current) => [
                    ...current,
                    ...files.map((file) => ({
                      id: `${file.name}-${file.size}-${file.lastModified}`,
                      file,
                    })),
                  ].slice(0, MAX_THREAD_ATTACHMENTS));
                  event.currentTarget.value = '';
                }}
              />
            </label>
          </FormField>
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {pendingAttachments.map((item) => (
                <span
                  key={item.id}
                  className="border-border-subtle bg-surface-raised text-caption inline-flex max-w-full items-center gap-2 rounded-md border px-2 py-1"
                >
                  <span className="max-w-64 truncate">{item.file.name}</span>
                  <button
                    type="button"
                    className="text-text-muted hover:text-text"
                    aria-label={REMOVE_ATTACHMENT_LABEL}
                    onClick={() =>
                      setPendingAttachments((current) =>
                        current.filter((attachment) => attachment.id !== item.id),
                      )
                    }
                    disabled={compose.isPending}
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {errorMsg && <p className="text-feedback-danger text-xs">{errorMsg}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={submittingRef.current || compose.isPending}>
            {submittingRef.current || compose.isPending ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
