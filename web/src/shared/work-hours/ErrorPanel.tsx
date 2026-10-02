/**
 * Blocking error panel — surfaces server 422 errors (spec §5.1).
 * Shared by Work Hours surfaces and Settings ▸ Organization operating controls.
 * The heading is catalog copy; the server error strings render verbatim.
 */
import { useTranslation } from '@/hooks/i18n';

export function ErrorPanel({ errors }: { errors: string[] }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="border-tier-red bg-feedback-danger/10 text-tier-red mb-4 rounded border p-3 text-sm"
    >
      <p className="font-medium">{t('workHours.dialog.saveRejected')}</p>
      <ul className="mt-1 list-disc pl-5">
        {errors.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
      </ul>
    </div>
  );
}
