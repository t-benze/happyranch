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

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddOrgDialog({ open, onOpenChange }: Props): JSX.Element {
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
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
    onError: (err: unknown) => {
      const e = err as { code?: string; status?: number; message?: string };
      if (e.code === 'no_active_runtime') {
        setServerError(t('org.add.error.noActiveRuntime'));
      } else if (e.code === 'org_dir_has_data') {
        setServerError(t('org.add.error.dirHasData', { slug }));
      } else if (e.code === 'org_exists' || e.code === 'org_dir_exists' || e.status === 409) {
        setServerError(t('org.add.error.exists', { slug }));
      } else if (e.code === 'invalid_slug') {
        setServerError(t('org.add.error.invalidSlug'));
      } else {
        // Preserve any exact daemon-supplied detail; only the generic
        // app-owned fallback translates.
        setServerError(e.message ?? t('org.add.error.generic'));
      }
    },
  });

  const valid = SLUG_RE.test(slug);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
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
          {serverError && (
            <p className="text-tier-red text-sm">{serverError}</p>
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
