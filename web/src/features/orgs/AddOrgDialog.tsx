/**
 * Add Org dialog — retained for its existing standalone callers.
 *
 * Slug-only form, posts to POST /api/v1/orgs. On success the orgs list
 * query is invalidated and the user navigates to `/orgs/<new>/threads`.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { orgs as orgsApi } from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

/**
 * Product-owned error identity plus the parameters captured at submit time.
 *
 * W2a review R1: the handler must NOT store a locale-rendered string. The
 * descriptor keeps the *identity* (and the exact submitted slug) so the copy is
 * re-translated on every render and follows a locale switch without
 * resubmission. A genuinely external daemon diagnostic (an unrecognized
 * non-empty message) is retained verbatim as `raw`.
 */
type AddOrgError =
  | { kind: 'noActiveRuntime' }
  | { kind: 'dirHasData'; slug: string }
  | { kind: 'exists'; slug: string }
  | { kind: 'invalidSlug' }
  | { kind: 'generic' }
  | { kind: 'raw'; message: string };

function classifyAddOrgError(
  err: unknown,
  submittedSlug: string,
): AddOrgError {
  const e = err as { code?: string; status?: number; message?: string };
  if (e.code === 'no_active_runtime') return { kind: 'noActiveRuntime' };
  if (e.code === 'org_dir_has_data') return { kind: 'dirHasData', slug: submittedSlug };
  if (e.code === 'org_exists' || e.code === 'org_dir_exists' || e.status === 409) {
    return { kind: 'exists', slug: submittedSlug };
  }
  if (e.code === 'invalid_slug') return { kind: 'invalidSlug' };
  // Preserve any exact daemon-supplied detail verbatim; only a missing message
  // falls back to the app-owned generic copy.
  const message = typeof e.message === 'string' ? e.message : '';
  if (message.trim().length > 0) return { kind: 'raw', message };
  return { kind: 'generic' };
}

type Translator = (key: MessageKey, params?: MessageParams) => string;

function renderAddOrgError(error: AddOrgError, t: Translator): string {
  switch (error.kind) {
    case 'noActiveRuntime':
      return t('org.add.error.noActiveRuntime');
    case 'dirHasData':
      return t('org.add.error.dirHasData', { slug: error.slug });
    case 'exists':
      return t('org.add.error.exists', { slug: error.slug });
    case 'invalidSlug':
      return t('org.add.error.invalidSlug');
    case 'generic':
      return t('org.add.error.generic');
    case 'raw':
      return error.message;
  }
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddOrgDialog({ open, onOpenChange }: Props): JSX.Element {
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<AddOrgError | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const create = useMutation({
    mutationFn: (body: { slug: string }) => orgsApi.createOrg(body),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['orgs'] });
      onOpenChange(false);
      navigate(`/orgs/${resp.slug}/threads`);
    },
    onError: (err: unknown, variables) => {
      // Use the mutation variables, not the (possibly edited) live `slug`.
      setServerError(classifyAddOrgError(err, variables.slug));
    },
  });

  const valid = SLUG_RE.test(slug);
  const errorText = serverError ? renderAddOrgError(serverError, t) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{t('org.add.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="org-slug">{t('org.add.slugLabel')}</Label>
          <Input
            id="org-slug"
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setServerError(null);
            }}
            placeholder={t('org.add.slugPlaceholder')}
            autoFocus
          />
          <p className="text-fg-muted text-xs">{t('org.add.slugHint')}</p>
          {errorText && (
            <p className="text-tier-red text-sm">{errorText}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            disabled={!valid || create.isPending}
            onClick={() => create.mutate({ slug })}
          >
            {create.isPending ? t('org.add.creating') : t('org.add.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
