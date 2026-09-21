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
  allocateArtifactName,
  attachmentContentType,
  createSelectionIdFactory,
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

interface ReflectionTrigger {
  /** If present and the dialog represents a single-agent recipient start, show a Reflection button. */
  recipients: string[];
}

interface Props {
  open: boolean;
  onClose: () => void;
  prefill?: Prefill;
  /** Called with the new thread_id on success. */
  onCreated: (threadId: string) => void;
  /** Agents list used for @-mention autocomplete in the body. */
  agents?: AgentSummary[];
  /** When set, show a Reflection quick-start button for single-recipient starts. */
  reflection?: ReflectionTrigger;
}

export function NewThreadDialog({ open, onClose, prefill, onCreated, agents = [], reflection }: Props): JSX.Element {
  const slug = useOrgSlug();
  const compose = useComposeThread();
  const [subject, setSubject] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [body, setBody] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // True during the upload phase, so attach/send/remove disable from the first
  // click (compose.isPending only flips after the uploads finish).
  const [uploading, setUploading] = useState(false);

  // Synchronous in-flight latch — prevents duplicate submits before
  // React Query's isPending state propagates (double-click, Enter+Send).
  const submittingRef = useRef(false);

  // Stable, non-metadata chip identity (two identical Files stay distinct).
  const selectionIdFactory = useRef<(() => string) | null>(null);
  if (selectionIdFactory.current === null) {
    selectionIdFactory.current = createSelectionIdFactory('nsel');
  }
  const nextSelectionId = selectionIdFactory.current;

  // Retained per-selection upload results/names so a failed attempt re-uploads
  // only the selections without a ref, and never regenerates a retained name.
  const attachmentRefsRef = useRef<Map<string, ThreadAttachmentRef>>(new Map());
  const attachmentNamesRef = useRef<Map<string, string>>(new Map());
  const allocatedNamesRef = useRef<Set<string>>(new Set());
  // Dialog generation: a completion from a closed/reopened dialog must not
  // close, navigate, clear or consume the reopened dialog's state.
  const dialogGenRef = useRef(0);

  const idBase = useId();
  const subjectId = `${idBase}-subject`;
  const recipientsId = `${idBase}-recipients`;
  const bodyId = `${idBase}-body`;

  useEffect(() => {
    // Every open/close/prefill transition invalidates in-flight work. Bumping
    // BEFORE the `!open` early return means a close that is never reopened also
    // abandons the submission, so a late completion cannot close, navigate,
    // reset or consume a departed dialog's state.
    dialogGenRef.current += 1;
    submittingRef.current = false;
    setUploading(false);
    if (!open) return;
    attachmentRefsRef.current.clear();
    attachmentNamesRef.current.clear();
    setSubject(prefill?.subject ?? '');
    setRecipientsRaw(prefill?.recipients?.join(', ') ?? '');
    setBody(prefill?.body ?? '');
    setPendingAttachments([]);
    setErrorMsg(null);
  }, [open, prefill]);

  // Full unmount must also abandon any in-flight submission.
  useEffect(() => () => { dialogGenRef.current += 1; }, []);

  const removeAttachment = useCallback((id: string) => {
    attachmentRefsRef.current.delete(id);
    attachmentNamesRef.current.delete(id);
    setPendingAttachments((current) => current.filter((attachment) => attachment.id !== id));
  }, []);

  const submit = useCallback(async () => {
    // Guard against double-submit (double-click, Enter+Send race).
    // The ref is synchronous — no React render needed to block re-entry.
    if (submittingRef.current) return;
    submittingRef.current = true;
    const generation = dialogGenRef.current;
    const isCurrent = () => dialogGenRef.current === generation;

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
    const capturedSlug = slug;
    setUploading(true);
    let failedUpload: PendingAttachment | null = null;
    try {
      const refs: ThreadAttachmentRef[] = [];
      const reserved = new Set<string>(attachmentNamesRef.current.values());
      for (const pending of pendingAttachments) {
        failedUpload = pending;
        let ref = attachmentRefsRef.current.get(pending.id);
        if (!ref) {
          let artifactName = attachmentNamesRef.current.get(pending.id);
          if (!artifactName) {
            artifactName = allocateArtifactName(
              'thread-draft',
              pending.file,
              reserved,
              allocatedNamesRef.current,
            );
            if (isCurrent()) attachmentNamesRef.current.set(pending.id, artifactName);
          }
          allocatedNamesRef.current.add(artifactName);
          const uploaded = await artifactsApi.uploadArtifact(capturedSlug, {
            file: pending.file,
            name: artifactName,
            agent: 'founder',
          });
          ref = {
            artifact_name: uploaded.name,
            display_name: pending.file.name,
            content_type: attachmentContentType(pending.file),
          };
          if (isCurrent()) attachmentRefsRef.current.set(pending.id, ref);
          reserved.add(uploaded.name);
        }
        refs.push(ref);
      }
      failedUpload = null;
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
        // Capture the destination org so an org switch during the upload cannot
        // retarget the compose to the new org (stripped before the request body).
        destination: { slug: capturedSlug },
      } as Parameters<typeof compose.mutateAsync>[0]);
      if (!isCurrent()) return;
      attachmentRefsRef.current.clear();
      attachmentNamesRef.current.clear();
      onCreated(result.thread_id);
      setPendingAttachments([]);
      onClose();
      // submittingRef remains true — dialog closes on success, so no
      // further re-entry is possible.
    } catch (err) {
      if (isCurrent()) {
        const label = failedUpload ? `${failedUpload.file.name}: ` : '';
        setErrorMsg(
          err instanceof ApiError
            ? label + describeError(err.code, `HTTP ${err.status}`)
            : label + String(err),
        );
        submittingRef.current = false;
      }
    } finally {
      if (isCurrent()) setUploading(false);
    }
  }, [subject, recipientsRaw, body, pendingAttachments, prefill, compose, slug, onCreated, onClose]);

  const handleReflection = useCallback(async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    const generation = dialogGenRef.current;
    const isCurrent = () => dialogGenRef.current === generation;
    setErrorMsg(null);

    const agentName = reflection?.recipients?.[0];
    if (!agentName) {
      setErrorMsg('Reflection requires a single agent recipient.');
      submittingRef.current = false;
      return;
    }
    const capturedSlug = slug;

    try {
      const result = await compose.mutateAsync({
        subject: `Reflection - ${agentName}`,
        recipients: [agentName],
        body_markdown:
          `Run self-reflection (hr:reflection) on your recent work and post your opening reflection report.`,
        destination: { slug: capturedSlug },
      } as Parameters<typeof compose.mutateAsync>[0]);
      if (!isCurrent()) return;
      onCreated(result.thread_id);
      setPendingAttachments([]);
      onClose();
    } catch (err) {
      if (isCurrent()) {
        setErrorMsg(
          err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
        );
        submittingRef.current = false;
      }
    }
  }, [reflection, compose, onCreated, onClose, slug]);

  // Upload phase + compose phase both disable the dialog's controls.
  const inFlight = uploading || compose.isPending;
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
              disabled={inFlight}
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
                disabled={inFlight}
                onChange={(event) => {
                  const files = Array.from(event.currentTarget.files ?? []).slice(
                    0,
                    MAX_THREAD_ATTACHMENTS,
                  );
                  setPendingAttachments((current) => [
                    ...current,
                    ...files.map((file) => ({
                      id: nextSelectionId(),
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
                    onClick={() => removeAttachment(item.id)}
                    disabled={inFlight}
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
          {reflection ? (
            <Button
              variant="secondary"
              onClick={handleReflection}
              disabled={inFlight}
            >
              {inFlight ? 'Sending…' : 'Reflection'}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={inFlight}>
            {inFlight ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
