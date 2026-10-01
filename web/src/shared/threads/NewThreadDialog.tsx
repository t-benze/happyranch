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
import { artifacts as artifactsApi } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import {
  MAX_THREAD_ATTACHMENTS,
  allocateArtifactName,
  attachmentContentType,
  createSelectionIdFactory,
} from '@/lib/threadAttachments';
import { useComposeThread } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';
import { classifyThreadError, renderThreadError, type ThreadErrorView } from '@/lib/threadErrors';
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
  const { t } = useTranslation();
  const slug = useOrgSlug();
  const compose = useComposeThread();
  const [subject, setSubject] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [body, setBody] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  // Locale-neutral error descriptor (key/params or raw diagnostic), rendered
  // through `t` at render time — a locale switch re-translates it in place and
  // never resubmits. `t` is deliberately NOT a dependency of any callback/effect.
  const [errorView, setErrorView] = useState<ThreadErrorView | null>(null);
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
    // Every open/close/prefill/org transition invalidates in-flight work. Bumping
    // BEFORE the `!open` early return means a close that is never reopened also
    // abandons the submission, so a late completion cannot close, navigate,
    // reset or consume a departed dialog's state. Org (`slug`) is part of the
    // ownership key: an org switch during an upload must invalidate the
    // captured submission even when `open`/`prefill` are unchanged, so a late
    // alpha success cannot navigate the beta view back to alpha.
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
    setErrorView(null);
  }, [open, prefill, slug]);

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

    setErrorView(null);
    const recipients = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (!subject.trim() || !recipients.length || (!body.trim() && !pendingAttachments.length)) {
      setErrorView({ detail: { kind: 'mapped', key: 'threads.newThread.required' } });
      submittingRef.current = false;
      return;
    }
    const capturedSlug = slug;
    setUploading(true);
    let failedUpload: PendingAttachment | null = null;
    try {
      const refs: ThreadAttachmentRef[] = [];
      // Run-owned snapshot; every read below uses this copy. Selection ids
      // restart on a fresh dialog (`nsel-1`), so reading the shared maps after
      // an await could substitute a reopened dialog's selection (mirrors the
      // ThreadsPage repair).
      const runRefs = new Map(attachmentRefsRef.current);
      const runNames = new Map(attachmentNamesRef.current);
      const reserved = new Set(runNames.values());
      for (const pending of pendingAttachments) {
        failedUpload = pending;
        let ref = runRefs.get(pending.id);
        if (!ref) {
          let artifactName = runNames.get(pending.id);
          if (!artifactName) {
            artifactName = allocateArtifactName(
              'thread-draft',
              pending.file,
              reserved,
              allocatedNamesRef.current,
            );
          }
          runNames.set(pending.id, artifactName);
          if (isCurrent()) attachmentNamesRef.current.set(pending.id, artifactName);
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
          runRefs.set(pending.id, ref);
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
        // The file name is data (params), the "<name>: " prefix is product copy.
        setErrorView({
          ...(failedUpload
            ? { label: { key: 'threads.newThread.uploadFailedFor', params: { name: failedUpload.file.name } } }
            : {}),
          detail: classifyThreadError(err),
        });
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
    setErrorView(null);

    const agentName = reflection?.recipients?.[0];
    if (!agentName) {
      setErrorView({ detail: { kind: 'mapped', key: 'threads.newThread.reflectionRequired' } });
      submittingRef.current = false;
      return;
    }
    const capturedSlug = slug;

    setUploading(true);
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
        setErrorView({ detail: classifyThreadError(err) });
        submittingRef.current = false;
      }
    } finally {
      if (isCurrent()) setUploading(false);
    }
  }, [reflection, compose, onCreated, onClose, slug]);

  // The dialog's OWN run state owns pending presentation for the whole
  // upload+compose (and Reflection) lifecycle. The surviving mutation
  // observer's `compose.isPending` would leak a closed/reopened dialog's
  // pending state into the replacement dialog.
  const inFlight = uploading;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {prefill?.forwarded_from_id
              ? t('threads.newThread.forwardTitle')
              : t('threads.newThread.title')}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {t('threads.newThread.description')}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField label={t('threads.newThread.subjectLabel')} htmlFor={subjectId}>
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
            label={t('threads.newThread.recipientsLabel')}
            htmlFor={recipientsId}
          >
            <RecipientsInput
              id={recipientsId}
              value={recipientsRaw}
              onChange={setRecipientsRaw}
              agents={agents}
              placeholder="agent_a, agent_b"
              mentionListLabel={t('threads.newThread.mentionList')}
            />
          </FormField>
          <FormField label={t('threads.newThread.bodyLabel')} htmlFor={bodyId}>
            <MentionTextarea
              id={bodyId}
              value={body}
              onChange={setBody}
              agents={agents}
              mentionListLabel={t('threads.newThread.mentionList')}
              onSubmit={() => { if (!submittingRef.current) submit(); }}
              disabled={inFlight}
              rows={6}
            />
          </FormField>
          <FormField label={t('threads.newThread.attachmentsLabel')} htmlFor={`${idBase}-attachments`}>
            <label className="border-border-subtle bg-surface text-caption hover:bg-surface-hover inline-flex w-fit cursor-pointer items-center gap-2 rounded-md border px-2 py-1">
              <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t('threads.newThread.attachFiles')}</span>
              <input
                id={`${idBase}-attachments`}
                aria-label={t('threads.newThread.attachFiles')}
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
                    aria-label={t('threads.newThread.removeAttachment')}
                    onClick={() => removeAttachment(item.id)}
                    disabled={inFlight}
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {errorView !== null && (
            <p className="text-feedback-danger text-xs">{renderThreadError(errorView, t)}</p>
          )}
        </div>
        <DialogFooter>
          {reflection ? (
            <Button
              variant="secondary"
              onClick={handleReflection}
              disabled={inFlight}
            >
              {inFlight ? t('threads.newThread.sending') : t('threads.newThread.reflection')}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={submit} disabled={inFlight}>
            {inFlight ? t('threads.newThread.sending') : t('threads.newThread.send')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
