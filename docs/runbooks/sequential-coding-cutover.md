# Sequential Coding cutover and backout — operator draft

**U9a SOURCE ONLY; public cutover not implemented.** The unmounted selector and
old-fresh refusal interface do not change admission. Public sequential fresh
start remains behind `check_venue_start`; old start@2 still has its original
Explicit/native gates. U9b must mount both changes in one merge unit. This
runbook grants no live operation. Use the [U9 spec](../superpowers/specs/2026-11-12-sequential-coding-u9.md)
for the exact compatibility table, caller inventory and implementation remainder.

## Install and setup sequence

1. Obtain separate authority naming the actual runtime/org/source and cooperative
   maintenance window. Preserve original owners, settings/save keys, unknown
   effects, backups and current native/job/preparation exclusions. Deployment,
   stop/restart and live installation/admission are separately authorized
   operations; source review/merge and a health response supply none of them.
2. Preserve historical Coding initialization and PR1262 org/profile/startup
   ownership ordering. Its v1 builtin and old Explicit settings neither install
   nor enable the new sequential family. Do not run the historical host setup
   command expecting sequential installation or convert its settings.
3. Under the separately authorized offline procedure, use the existing
   [sequential storage runbook](sequential-workflow-storage.md), including its
   complete readers, checkpoint/backup/native exclusions and uncertainty rules:

   ```sh
   python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --check
   python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --backup /absolute/backup/happyranch.db --backup-sha256 VERIFIED_SHA256
   ```

   Check exit3 means install-needed, not success. Exit0 means compatible storage
   (or observed installation), not definition registration, admission, loaded
   identity or execution acceptance. Exit1 refuses; exit4 preserves uncertainty.
   Never delete a sidecar or reset ownership to make installation pass.
4. **Pending U9b:** use its reviewed non-venue registration path for the fixed
   Coding v2 resource and compatible ABI. U9a deliberately supplies no public
   registration/admission command. Do not substitute direct SQL, old release
   import, the disposable `setup_sequential_venue.py` publisher, marker creation
   or a configuration flag. An absent definition/catalog remains unavailable.
5. The historical `scripts/setup_coding.py` handoff now opens
   `/orgs/ORG/workflows/setup` directly. That view edits repo-free defaults only
   for stored sequential definitions. Save explicitly with a new operation;
   first save is Disabled, later Save preserves admission and allocates no work.
   Preserve original key/body after uncertainty and use only Check original save.
   The pointer at `/orgs/ORG/settings/coding` retains the OLD read-only save
   recovery; never translate/re-key/resend those repo-bearing commands.
6. **Pending U9b and separate operating authority:** Allow/Disable future starts
   only through its supported admission owner. Saving defaults does not enable.
   Verify the loaded selector/engine identity separately from disk source,
   storage and settings. Fresh new storage/engine refusal must create no legacy
   fallback. Do not enable ordinary use before required execution acceptance.
7. After the atomic source attachment and separately authorized installation,
   the genuine ordinary executable manager's own unchanged sequential
   `workflow_start` callback is the only public Coding initiation. Preserve
   original limits/key/body and source host/outer settlement before first visit.
   There is no Founder/browser Start action. Genuine Coding scratch remains a
   separate Founder-reserved operation; V3 scripted proof is not that scratch.

## Backout and drain

- **Before extension installation:** use the separately authorized source
  deployment/backout owner; U9a introduces no stored state. Do not interpret an
  inert helper or reverted source as evidence about already admitted work.
- **After extension installation, even empty:** retain compatible schema and
  ownership@2 readers. An old-code-only downgrade is unsupported. Never drop
  tables, erase marks, rewrite pins/settings/events or reset source/carrier rows.
- **After atomic cutover:** close NEW admission using its supported separately
  authorized settings owner; continue admitted old/new callbacks, settlement,
  cancellation and LOCAL Final under their original pins/current authority.
  Keep the old fresh-start fence. Backout must not reopen old start@2 or fall
  back into bespoke Coding progression. U9b must supply that admission action
  before public attachment; it is unavailable in U9a.
- Defaults/admission edits never reassign or re-pin admitted work. Use supported
  cancellation for business stop; Stop success/PID absence does not settle an
  unknown native owner or remote effect. Original intent/receipt uncertainty
  (including command=None) allows no reissue or effect recovery.
- A database restore is separate operational recovery and cannot undo external
  effects. Preserve complete original receipts/keys and denied versus unverified
  causes; reconcile through their actual owners, without automatic retry.

Record exact installed/loaded versions, storage and settings identities, original
operations, commands/exits and what was NOT RUN. Bounded V3 proof retains its
original window ending 2026-11-23T08:48:38Z; nothing here extends it or authorizes
live changes. Independent review/QA/guarded merge remain source gates, separate
from installation and genuine execution acceptance.
