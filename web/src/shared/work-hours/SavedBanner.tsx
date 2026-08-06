/**
 * Pending-tick success banner (spec §5.1).
 * Shared by Work Hours surfaces and Settings ▸ Organization operating controls.
 */
export function SavedBanner({ message }: { message: string }): JSX.Element {
  return (
    <div
      role="status"
      className="border-tier-green bg-feedback-success/10 text-tier-green mb-4 rounded border p-3 text-sm"
    >
      {message}
    </div>
  );
}
