import { useEffect, useId, useState } from 'react';
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
import { ApiError } from '@/lib/api';
import { useComposeThread } from '@/hooks/threads';
import { describeError } from './strings';

interface Prefill {
  subject?: string;
  recipients?: string[];
  body?: string;
  forwarded_from_id?: string;
  forwarded_from_kind?: 'thread' | 'talk';
}

interface Props {
  open: boolean;
  onClose: () => void;
  prefill?: Prefill;
  /** Called with the new thread_id on success. */
  onCreated: (threadId: string) => void;
}

export function NewThreadDialog({ open, onClose, prefill, onCreated }: Props): JSX.Element {
  const compose = useComposeThread();
  const [subject, setSubject] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [body, setBody] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const idBase = useId();
  const subjectId = `${idBase}-subject`;
  const recipientsId = `${idBase}-recipients`;
  const bodyId = `${idBase}-body`;

  useEffect(() => {
    if (!open) return;
    setSubject(prefill?.subject ?? '');
    setRecipientsRaw(prefill?.recipients?.join(', ') ?? '');
    setBody(prefill?.body ?? '');
    setErrorMsg(null);
  }, [open, prefill]);

  const submit = async () => {
    setErrorMsg(null);
    const recipients = recipientsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (!subject.trim() || !recipients.length || !body.trim()) {
      setErrorMsg('Subject, recipients, and body are all required.');
      return;
    }
    try {
      const result = await compose.mutateAsync({
        subject: subject.trim(),
        recipients,
        body_markdown: body.trim(),
        ...(prefill?.forwarded_from_id
          ? {
              forwarded_from_id: prefill.forwarded_from_id,
              forwarded_from_kind: prefill.forwarded_from_kind,
            }
          : {}),
      });
      onCreated(result.thread_id);
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
      );
    }
  };

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
            <input
              id={recipientsId}
              type="text"
              value={recipientsRaw}
              onChange={(e) => setRecipientsRaw(e.target.value)}
              placeholder="agent_a, agent_b"
              className="input"
            />
          </FormField>
          <FormField label="Body (Markdown)" htmlFor={bodyId}>
            <textarea
              id={bodyId}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={6}
              className="input resize-y"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
          </FormField>
          {errorMsg && <p className="text-feedback-danger text-xs">{errorMsg}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={compose.isPending}>
            {compose.isPending ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
