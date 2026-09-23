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
import { classifyAddOrgError, renderAddOrgError, type AddOrgError } from '@/lib/addOrgError';

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

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
