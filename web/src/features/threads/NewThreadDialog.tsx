import { useEffect, useState } from 'react';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/Button';
import { ApiError } from '@/lib/api';
import { useOrgSlug } from '@/lib/orgSlug';
import { useComposeThread } from './hooks';
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
  const slug = useOrgSlug();
  const compose = useComposeThread(slug);
  const [subject, setSubject] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [body, setBody] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
    <Modal title={prefill?.forwarded_from_id ? 'Forward thread' : 'New thread'} open={open} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Subject">
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="input"
            autoFocus
          />
        </Field>
        <Field label="Recipients (comma-separated agent names)">
          <input
            type="text"
            value={recipientsRaw}
            onChange={(e) => setRecipientsRaw(e.target.value)}
            placeholder="agent_a, agent_b"
            className="input"
          />
        </Field>
        <Field label="Body (Markdown)">
          <textarea
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
        </Field>
        {errorMsg && <p className="text-xs text-tier-red">{errorMsg}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={compose.isPending}>
            {compose.isPending ? 'Sending…' : 'Send'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="text-fg-muted">{label}</span>
      {children}
    </label>
  );
}
