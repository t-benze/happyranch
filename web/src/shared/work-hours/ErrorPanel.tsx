/**
 * Blocking error panel — surfaces server 422 errors (spec §5.1).
 * Shared by Work Hours surfaces and Settings ▸ Organization operating controls.
 */
export function ErrorPanel({ errors }: { errors: string[] }): JSX.Element {
  return (
    <div
      role="alert"
      className="border-tier-red bg-feedback-danger/10 text-tier-red mb-4 rounded border p-3 text-sm"
    >
      <p className="font-medium">Save rejected — the config was not written.</p>
      <ul className="mt-1 list-disc pl-5">
        {errors.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
      </ul>
    </div>
  );
}
