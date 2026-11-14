# Mac release handbook

Use this from a checkout of `t-benze/happyranch` on the founder's Mac mini.
CI builds an **unsigned inner app ZIP and build receipt**. The local agent signs
a copy, submits it once to Apple, then staples and verifies another copy. That
produces a **different signed/notarized/stapled ZIP with a new SHA256**.
Signing is not publication. Never create a public release or upload here.

THR-360 C7 bundles the separate managed connector helper at
`Contents/Helpers/happyranch-tsnet-sidecar` (Darwin/arm64, Go1.23.12, cgo off).
Its Darwin THIRD_PARTY_NOTICES and dependency inventory are separate originals
in the app licenses manifest. Both existing signing scripts include a helper
present in the retained baseline before outer-app signing, with the existing
identity, runtime option and no new entitlements. Their accepted older ZIP/
source/layout pins remain unchanged: a new app still needs the separate
authorized artifact-binding/release procedure. No C7 native build, signing,
notarization, release or packaged-app acceptance is claimed.

## Prerequisites

Use your ordinary owner login and its `$HOME` in a native arm64 shell,
not root or Rosetta. Run the commands in a Bash shell with tracing off; restore
the documented path variables when starting a new shell. Owner-reported tools are
**macOS 26.5 (25F71) / Xcode 26.5
(17F42)**; preflight records the actual versions. Have `gh` authenticated to
`t-benze/happyranch`, stock Mac tools and these existing Keychain entries:

| Item | Value |
| --- | --- |
| Team ID | `HAPPYRANCH_SIGN_TEAM_ID` (`<TEAM_ID>`) |
| Public codesign identity selector | `HAPPYRANCH_SIGN_IDENTITY_SHA1` (`<40_HEX_SHA1>`) |
| Identity name | `HAPPYRANCH_SIGN_IDENTITY_NAME` (`Developer ID Application: <LEGAL_NAME> (<TEAM_ID>)`) |
| notarytool profile | `HAPPYRANCH_NOTARY_PROFILE` (`<EXISTING_NOTARY_PROFILE>`) |
| Login keychain | `$HOME/Library/Keychains/login.keychain-db` |

The selector is public, not a private key. Do not repeat credential setup.
The owner handles OS/Keychain prompts directly, never by pasting secrets into
chat. Unexpected prompts, locked Keychain or denied access: stop for the owner;
do not choose persistent Always Allow or change policy.

### One-time private signing configuration

Keep these values outside every checkout and public report. In the owner's
Terminal, find the existing valid identity's SHA1 and full name (the final
parenthesized suffix supplies its Team ID):

```sh
set +x
/usr/bin/security find-identity -v -p codesigning "$HOME/Library/Keychains/login.keychain-db"
```

Ask the owner for the **existing** notarytool Keychain profile name; this file
holds its name only, never a password or private key. Do not create a replacement
profile or repeat credential setup. Replace every placeholder in the following
literal template locally before running it. If the file already exists, inspect
and update it privately instead of overwriting it with this block:

```sh
(
  set -eC
  umask 077
  /bin/mkdir -p "$HOME/.config/happyranch"
  cat > "$HOME/.config/happyranch/mac-signing.env" <<'SIGNING_CONFIG'
HAPPYRANCH_SIGN_IDENTITY_SHA1=<40_HEX_SHA1>
HAPPYRANCH_SIGN_IDENTITY_NAME="Developer ID Application: <LEGAL_NAME> (<TEAM_ID>)"
HAPPYRANCH_SIGN_TEAM_ID=<TEAM_ID>
HAPPYRANCH_NOTARY_PROFILE="<EXISTING_NOTARY_PROFILE>"
SIGNING_CONFIG
  /bin/chmod 600 "$HOME/.config/happyranch/mac-signing.env"
)
```

Both signing scripts and the owner-session wrapper require this private regular
file, owned by the current user with mode 600 or stricter (400 is accepted;
symlinks and group/world access are refused). The file is the sole source of
all four required values: individual environment values are ignored. Direct
invocations can override its path with `HAPPYRANCH_MAC_SIGNING_ENV`; restore that
variable in every new shell if used. The owner session and Jenkins stage preserve
this path selector through environment cleanup. The login Keychain always
derives from `$HOME`.

The parser accepts only those four `KEY=VALUE` keys, once each. Blank lines and
whole-line `#` comments are allowed; surrounding spaces/tabs and one pair of
single or double quotes are optional. Values are literal: no shell expansion,
escape processing or inline comments. Never `source` this file. Missing/empty,
unknown or duplicate keys, malformed quotes/control characters, a SHA1 other
than 40 hex characters, a Team ID other than 10 uppercase alphanumerics or an
identity name without the matching `(TEAM_ID)` suffix stop the scripts with
setup guidance. SHA1 letter case is normalized for the existing identity check.
Keep the same configuration throughout a retained signing run. This setup does
not query Apple, unlock a Keychain or change credential policy.

The separate existing Jenkins native probe has reviewed source
for scratch-only uv 0.12.21/Node 24.19.0 LTS/Go 1.23.12 provisioning and 90min/256MiB limits
(THR-346 msg86); see the [probe contract](../../ci/jenkins/mac-native-probe/README.md).
This changes no signing-host prerequisites or release route. Build23 at PR1485
head `5b66b30` proved source-toolchain readiness: uv0.12.21, Node24.19.0/npm,
Go1.23.12, Swift6.3.2, Xcode26.5 and Python3. Build19's first-Go curl28 remains
historical failure evidence; packaged execution, signing and host login remain
unverified. The separately authorized `SUITE=packaged` records those runtime
observations without granting signing or release authority. Source
diagnostics retain bounded curl exit/stderr/timing metrics per official origin;
ordered Go fallback shares the same hash pin and unchanged 90s/artifact, 240s
total budget. Each attempt uses remaining time with small fallback reserves,
resuming the same pinned partial. A bad hash deletes it and refuses extraction;
deadline-kill diagnostics retain file byte deltas/elapsed time and available
curl metrics. A non-gating
executor version/exit-only auth observation precedes downloads on the fixed host
PATH with isolated HOME; host-stored login and a real session remain unverified.
No proxy, host/job change or new credential workaround is authorized. Preflight requires those
exact versions at scratch paths, with private caches and GOTOOLCHAIN=local.
The freezer hash-installs the complete build-only editable backend closure
before sync; runtime manifests/uv.lock are unchanged. Go 1.23.12 preserves the
conservative gVisor runtime/ABI line; newer maintained compatibility remains
unestablished.
No packaged app is built or run by that preflight.

The packaged observation now requires a healthy backend with tree-kill capability
immediately after health, before starting a session. Its retained
`packaged.backend_selection` identifies the chosen backend and bounded, redacted
candidate refusals from the daemon log. A fallback refusal still stops the daemon
and runs the final census/EXIT cleanup. Census rejection now preserves bounded exact
rows/counts/line indices/reasons under `census_error` (up to 16 rows, 512 escaped
bytes each; six ps fields, no argv/environment, no redaction). Full escaped-row
lengths/SHA256 survive prefix truncation. Finalization empties later row copies
with `report_truncated: true` to fit the serialized report in 58,000 bytes,
protecting the first bounded exact row per distinct payload digest independently
of report dict order, plus indices/reasons/totals and every UNKNOWN snapshot.
This includes late helper failures; report 60,000-byte and collector
64-KiB caps remain unchanged, with room for the failure envelope/EXIT join.
Apple-documented
`?` states retain present-process identity; malformed rows keep UNKNOWN. A
command's exit/bytes/hash/tail survives sampling and final census failure;
step1 still fails and preserves its build result. `build_output` contains
planned scratch paths only; only `retained_product` proves a verified workspace
ZIP surviving scratch removal. Initial census failure records step1 FAIL.
See the [packaged evidence contract](../../ci/jenkins/mac-native-probe/README.md#packaged-app-observation).
Portable fixtures are synthetic; the actual build37 rejected row and native
format/teardown behavior remain unverified.
Inspect the state/start-identity-aware
failure, post-cancel and post-stop survivor snapshots; UNKNOWN or survivors fail.
Daemon-descendant PID/start identities and proven private groups are captured
before failure/pre-quit snapshots and daemon termination even when the session
marker is missing or invalid. Unobservable ancestry stays UNKNOWN through shutdown.
Only an ancestry-proven session-private PGID is subject to the session-group
absence assertion. The final native run remains separately authorized; source
tests do not prove macOS backend selection or packaged cleanup.

## Step 1 — Build in CI, only when a new build is needed

The shipped build path is `.github/workflows/build-mac-app.yml` (Build macOS App).
It uses **`macos-15`, Xcode 16, Python 3.12, Node 24, Go 1.23.12**; this is
separate from the local signing Mac's tools. Version and build each require
three numeric components. Supply the full independently reviewed commit and
its full tree SHA. Replace the uppercase placeholders before executing:

```sh
gh workflow run build-mac-app.yml -R t-benze/happyranch \
  -f version=VERSION -f build=BUILD \
  -f source_sha=FULL_REVIEWED_COMMIT -f source_tree=FULL_TREE_SHA
gh run list -R t-benze/happyranch --workflow build-mac-app.yml --limit 10 \
  --json databaseId,attempt,status,conclusion,createdAt,url,headSha
```

Select the actual dispatched run by URL/time, record its run ID and attempt,
and inspect its inputs/logs for the requested source/tree. The workflow definition
ref and the candidate checkout are distinct; list output's `headSha` alone is
not proof of the input source. Replace RUN_ID with that actual ID:

```sh
gh run watch RUN_ID -R t-benze/happyranch --exit-status
```

Require a successful conclusion and successful candidate upload. The job limit
is 95 minutes, build step 45. Do not blindly dispatch again after interruption.
Success uploads **`HappyRanch-unsigned-<run_id>-<run_attempt>`**, containing one
inner `HappyRanch-<version>-<build>-arm64.zip`, `build-receipt.json` and diagnostic
JSON files. A diagnostics-only artifact is not a build output.

No Jenkins app-build job is installed: the earlier build-only job was a
**proposal**, unavailable for use. The Jenkins signing job stays disabled.

### Accepted 1.0.1: reuse it

The founder already holds the accepted original in private THR-104 attachments.
If it is present and matches the following size/hash, **no new build or download
is needed**. Native build25 was GitHub run `39774953034`, attempt `1`;
“build25” is an execution label, not the app's build field.

| Field | Accepted value |
| --- | --- |
| Filename | `HappyRanch-1.0.1-1.0.1-arm64.zip` |
| Version / build / arch | `1.0.1` / `1.0.1` / `arm64` |
| Bytes | `49089611` |
| SHA256 | `85b96655a1d072328872fa8797e16ad0087e0e05aa30b8dda2a8bc78b60ac243` |
| Source commit | `21c63d8c1ec520764945d7b0bafb4bfe668ab81d` |
| Source tree | `b2af3a4c8e0cb1c6b58796030085dfb31680b374` |

## Step 2 — Retrieve and verify the inner app ZIP

For a CI download, replace RUN_ID, ATTEMPT and the destination with the selected
run/attempt and a **new empty owner-controlled directory**, never a directory
holding another attempt:

```sh
gh run download RUN_ID -R t-benze/happyranch \
  -n HappyRanch-unsigned-RUN_ID-ATTEMPT -D /absolute/new-empty-directory
```

The Actions artifact download is an **outer wrapper ZIP**; `gh run download`
extracts that wrapper. Sign only the **inner app ZIP**, never the wrapper.
If the artifact expired/is unavailable, stop for the founder to supply the
accepted original; do not rebuild it to work around missing evidence.

Read `build-receipt.json` locally. Its actual fields are `role`
(`unsigned-inner-zip`), `filename`, `size`, `sha256`, `version`, `build`, `arch`,
`source_sha`, `source_tree`, `run_id`, `app_inventory_sha256`, `manifest_notices`,
`available`, `approved_url`, `signing`, `ordinary_reader_execution`.
`run_id` is the packaging script's generated UUID, **not** the GitHub run ID.
Check filename, size, version/build, arm64 and source/tree against the reviewed
request and expected receipt; retain the notices/diagnostic evidence. Unsigned
receipts report `available: false`, `approved_url: null`, signing and ordinary
reader execution `UNEXECUTED`; those are not publication or runtime acceptance.

Set ZIP to the selected **inner** file (the accepted existing download is shown):

```sh
set +x
ZIP="$HOME/Downloads/HappyRanch-1.0.1-1.0.1-arm64.zip"
/usr/bin/stat -f '%z %N' "$ZIP"
/usr/bin/shasum -a 256 "$ZIP"
```

Compare the size and SHA256 with the receipt **and the independently expected
hash**, not merely another file from the same download. For accepted 1.0.1 use
the table above even when reusing the founder's original without its CI receipt.
Mismatch, absent fields or uncertain provenance: stop and preserve the file;
never overwrite it or silently substitute another attempt.

## Step 3 — Sign locally, one separate stage at a time

**The committed `app/mac/scripts/mac-sign.sh` is pinned to the accepted 1.0.1
artifact: `INPUT_SHA`, size, layout, version/build 1.0.1 and source/tree above. A
new CI build with a different version or hash will be refused. Signing it needs
a separately reviewed script update. Do not edit the pinned hash or other pins.**

### Prepare the unchanged script and its required inventory

Start at the repository root. Use plain absolute paths, without symlink
ancestors or dot components. Inputs must be owner-owned files with one hard
link. The script needs the exact inventory below beside it; it is embedded here
so no separate owner package or instructions are needed.

```sh
REPO="$(pwd -P)"
PKG="$HOME/HappyRanch-signing/tools-1.0.1"
RUN="$HOME/HappyRanch-signing/first-1.0.1"
```

Inspect any existing signing parent: it must be an ordinary owner-owned mode0700
directory with plain ancestors. **Only if absent**, create it once:

```sh
/bin/mkdir -m 700 "$HOME/HappyRanch-signing"
```

If PKG already exists, verify its two hashes below and reuse only matching bytes;
do not run the preparation block over it. For an absent PKG, run this block in
Bash with tracing off. It creates a private tools copy and writes literal tab
separators; preserve the tabs. Any failure means stop, retaining partial files.

```sh
set +x
set -eC
/bin/mkdir -m 700 "$PKG"
/bin/cp -p "$REPO/app/mac/scripts/mac-sign.sh" "$PKG/mac-sign.sh"
cat > "$PKG/code-inventory.tsv" <<'INVENTORY'
lib	6	ai.happyranch.HappyRanchApp.nested.01	Contents/Resources/daemon/_internal/libpython3.12.dylib
lib	8	ai.happyranch.HappyRanchApp.nested.02	Contents/Resources/daemon/_internal/websockets/speedups.cpython-312-darwin.so
lib	8	ai.happyranch.HappyRanchApp.nested.03	Contents/Resources/daemon/_internal/httptools/parser/url_parser.cpython-312-darwin.so
lib	8	ai.happyranch.HappyRanchApp.nested.04	Contents/Resources/daemon/_internal/httptools/parser/parser.cpython-312-darwin.so
lib	8	ai.happyranch.HappyRanchApp.nested.05	Contents/Resources/daemon/_internal/uvloop/loop.cpython-312-darwin.so
lib	8	ai.happyranch.HappyRanchApp.nested.06	Contents/Resources/daemon/_internal/yaml/_yaml.cpython-312-darwin.so
lib	6	ai.happyranch.HappyRanchApp.nested.07	Contents/Resources/daemon/_internal/pydantic_core/_pydantic_core.cpython-312-darwin.so
lib	6	ai.happyranch.HappyRanchApp.nested.08	Contents/Resources/daemon/_internal/watchfiles/_rust_notify.cpython-312-darwin.so
exec	2	ai.happyranch.HappyRanchApp.nested.09	Contents/Resources/daemon/happyranch-daemon
exec	2	ai.happyranch.HappyRanchApp.nested.10	Contents/Resources/daemon/happyranch
main	2	ai.happyranch.HappyRanchApp	Contents/MacOS/HappyRanch
INVENTORY
```

Verify both files before any stage:

```sh
/usr/bin/shasum -a 256 "$PKG/mac-sign.sh" "$PKG/code-inventory.tsv"
```

Require script `5f5359a0998f1601b0ff314c9dc9d921a5f68fd26cd4913cb17e312a91cecdef`
and inventory `16ee1fd168286ca9bbfe0ed7ea683ed98df0d8470b7916ad57de7b0907538af3`.
Keep these identical tools for every resumed stage. Restore REPO/PKG/ZIP/RUN
variables in each new shell and inspect retained state before proceeding.

### Preflight, then sign

```sh
/bin/bash "$PKG/mac-sign.sh" preflight "$ZIP"
```

Require exit0 and `READ-ONLY PREFLIGHT complete.` It checks ZIP size/hash/CRC,
member layout, plist/source pins, eleven arm64 Mach-O types, tools and exact
valid public identity. It neither queries the notary profile nor exercises
private-key access, and launches no app.

RUN must be absent. With the owner's local instruction to sign:

```sh
/bin/bash "$PKG/mac-sign.sh" sign "$ZIP" "$RUN"
```

Require exit0, `Signed run:` and `sign.complete`. Receipts retain `original.zip`,
`baseline/`, `baseline-data.txt`, `baseline.sha256`, `package.sha256`,
`work/HappyRanch.app`, `signed-files.txt`, `sign-logs/`.
It signs eight libraries/bundles, two frozen executables, then the app/main;
timestamps all targets, hardens executables, adds no exception entitlements and
does no deep signing. Strict nested/outer verification checks identifiers,
team/authority, timestamps/runtime and original non-code bytes/modes, including
notices, README and plist. Failures retain the partial run: stop, never re-sign
it or automatically start another run.

### Submit ONCE, then observe status

Only after successful signing and the owner's local instruction to upload:

```sh
/bin/bash "$PKG/mac-sign.sh" submit "$RUN"
```

The exclusive `submission/` directory is the one-attempt gate. The script
rechecks signed bytes, archives and re-extracts
`HappyRanch-1.0.1-submission-<generated-UUID>.zip`, records `artifact-name.txt`,
`artifact.sha256`, `artifact.size`, then writes/syncs `ATTEMPTED` before the
single Apple call. Require exit0 and `Saved actual submission ID:`;
`submission/id.txt` is Apple's real UUID, not the filename UUID.
Retain `submit.out`, `submit.err`, `submit.exit` even on failure. Nonzero,
interruption, ambiguous result or missing ID: **STOP and ask the founder**;
never repeat submit. A real returned ID can be saved even when submit fails.

```sh
/bin/bash "$PKG/mac-sign.sh" status "$RUN"
```

Status only observes Apple and writes local receipts. Each `status.XXXXXX/`
holds `info.out/.err/.exit`, `info.json` and, when available,
`log.json`, `log.out/.err/.exit`. `accepted-status.txt` starts `unconfirmed`.
Exit0 / `Accepted for exact SHA256` requires matching info ID/name/Accepted,
log jobId/Accepted and the exact submitted ZIP SHA256; then that file selects
the successful snapshot. Upload success alone is not acceptance.

Pending (`In Progress`) permits **read-only status checks only**, no finalize
or resubmit. The script can exit nonzero while pending because its log or
Accepted result is unavailable; inspect saved info. Allow at most three explicit
observations in a local session: initial, then owner-chosen observations around
5 and 15 minutes. No hidden loop/background waiting; return PENDING with ID/hash
for a later same-run session. Invalid, unknown/missing status, tool failure or
ID/name/hash mismatch: stop for the founder.

For a missing ID, only after founder reconciliation of actual Apple history
and the unique submitted filename/time, use the actual reconciled UUID:

```sh
/bin/bash "$PKG/mac-sign.sh" status "$RUN" --recover-id ACTUAL_APPLE_UUID
```

This cannot replace an existing ID and still requires matching Apple evidence.
If no genuine ID can be reconciled, remain stopped. Missing history is never
permission to submit again.

### Read the full Accepted log, finalize (includes stapling), then test

Read the **entire printed `status.XXXXXX/log.json`**, including warnings; record
each warning and its disposition, or `none`. Ask the founder about unexplained
warnings. Hashing a log is not reading it. Only then replace LOG_SHA256 with its
actual printed hash:

```sh
/bin/bash "$PKG/mac-sign.sh" finalize "$RUN" --log-reviewed LOG_SHA256
```

There is **no separate staple stage**. Finalize rechecks Accepted and the log
hash, copies the signed app into `final/`, staples that app copy, validates its
ticket, permits only the ticket file to differ, rechecks signatures/non-code
bytes, makes a new ZIP and verifies its re-extraction, ticket,
`syspolicy_check distribution` and Gatekeeper. Original/submission ZIPs stay
unchanged. Require exit0 and `FINAL ZIP:`, `NEW size:`, `NEW SHA256:`.

Output: `$RUN/final/HappyRanch-1.0.1-1.0.1-arm64-signed-notarized-stapled.zip`.
Receipts: `final.sha256`, `final.size`, `association.txt` (Apple ID, submitted
hash, reviewed log hash), `complete`, `staple.exit`, `ticket.exit`,
`reextracted-ticket.exit`, `syspolicy.exit`, `gatekeeper.exit` under `final/`;
nested signature checks are in `verify.*/`. Require actual check exits0 and a
final hash different from the unsigned and submitted hashes.

```sh
/bin/bash "$PKG/mac-sign.sh" test "$RUN"
```

Require exit0 / `Native command checks complete.` Fresh `test.XXXXXX/` retains
signature, ticket, syspolicy and Gatekeeper checks. This rechecks the final ZIP
and retained extracted app **without launching it**. Any failure remains failed.

### Resume rules

| Retained state | Next action |
| --- | --- |
| RUN exists without `sign.complete` | Preserve; stop for founder reconciliation. |
| Signed, no `submission/` | Same-run submit once, with local owner instruction. |
| `submission/` exists, even without `ATTEMPTED` | Never remove/retry; reconcile interrupted preparation. |
| `ATTEMPTED` with saved ID | Same-run status only until Accepted. |
| Ambiguous submit / missing ID | Stop; founder-approved read-only reconciliation only. |
| Accepted, full log reviewed, no `final/` | Finalize with that log's hash. |
| `final/` exists without `complete` | Stop; no rerun or manual stapling over partial output. |
| `final/complete` exists | Test may be repeated; no re-sign/submit/finalize. |

## Hard rules for the local agent

- Never print, copy or export the private key, notary password or credentials;
  never put secrets in CLI arguments or shell traces. Keep logs local.
- No Keychain/ACL/partition-list/trust/unlock-policy changes, sudo, credential
  creation, automatic entitlements, Gatekeeper override, quarantine stripping
  or insertion, or trust-cache resets.
- Exactly **one Apple notary submission per artifact**. No `--wait`, polling
  loop, blind resubmit, state deletion or fresh signing copy to hide uncertainty.
- Pending means status only. Ambiguous submit, missing ID, Invalid/unknown or
  hash mismatch means stop and ask the founder; only matched Accepted can advance.
- Never publish publicly. No Jenkins setup, remote signing, unrelated jobs,
  daemon/live-org/provider operations or replacement of an existing installation.

Finder/install, first and subsequent GUI launch/Quit, and a normal quarantined
browser download of the **final** ZIP require a separately owner-authorized safe
venue without live org data, saved provider credentials or an existing runtime.
Prefer an independent clean Mac; record actual download/quarantine provenance
without altering it. If unavailable, mark those cases **NOT RUN**. Command
checks on the signing Mac do not prove GUI/clean-download behavior. macOS 14
remains **NOT RUN** unless actually tested there; 26.5 success does not prove it.

## What to post back to THR-104

Give the founder this compact report to post, unless explicitly authorized to
send it. Include failures and omissions; no secrets, raw personal logs or unrelated
Apple history:

```text
Version/build/arch; actual macOS/Xcode; public identity selector and Team:
Stages run and actual exits; any failure or uncertainty:
Apple submission ID and final status (or NOT SUBMITTED / UNKNOWN / PENDING):
Submitted ZIP size/SHA256; ATTEMPTED retained:
Full Accepted log path/SHA256; warnings and disposition:
Final ZIP path, size and NEW SHA256 (or NOT PRODUCED):
Signature / staple / ticket / syspolicy / Gatekeeper check exits:
Notices and original non-code preservation receipts:
Finder/install, GUI first/subsequent launch/Quit, clean download: results or NOT RUN
macOS 14: NOT RUN unless actually tested; next action:
No publication performed.
```

## Ready-to-paste local instruction

> Read `docs/runbooks/mac-release-handbook.md` in `t-benze/happyranch` and follow
> it locally for the accepted `HappyRanch-1.0.1-1.0.1-arm64.zip`. Reuse my matching
> original; skip building/downloading it. Preserve any existing signing state,
> sign and submit once using my existing Keychain, and return the report for
> THR-104. Stop for me at an OS prompt, ambiguity or non-Accepted result.

Packaged resource verification retains29 fixed paths/modes and reads expected
size/hash from the build's committed Git SHA/tree. Source identity and resource
reads and the build Git archive export ignore local Git object replacements.
Standalone resource/basic/ZIP inspection requires the corresponding Git objects
(`--source` can select that repository); the build and archive transitions
constrain the expected identity.
A verification failure retains the complete bounded resource error list in the
packaged probe artifact before scratch removal. See the
[packaged observation contract](../../ci/jenkins/mac-native-probe/README.md#packaged-app-observation).

For the founder's Option A manual agent session, use the exact shipping ZIP
named by `probe.json.packaged.retained_product.path`, retained inside the Mac
mini's existing Jenkins workspace under `retained/<SOURCE_SHA>-<run-id>/`.
Compare its byte size and SHA256 with that record and adjacent `identity.json`
before extraction, then compare the recorded app-executable, daemon and CLI
SHA256 values. These ZIP bytes passed the ordinary strict shipping verifier;
they are unsigned internal builds, not public releases. Scratch cleanup leaves
this workspace copy intact, including after a later runtime-probe failure.
Only two ZIPs are retained; a future build may evict the older one. The ZIP is
not a Jenkins archived artifact. If the recorded copy is gone, a rebuild is a
different product identity and does not satisfy the same-app manual check.

## Managed connector sidecar source target (C6a)

The managed Go transport now supports a CGO-disabled Darwin/arm64 cross-build
with Go 1.23.12. See [the sidecar README](../../app/linux/tsnet-sidecar/README.md)
for its explicit readiness-file/watchdog flags, compile-only commands and separate
Darwin dependency inventory/notices. This target is not bundled by the app yet.
Python launchd handoff/installation is C6b; frozen entry/bundling and native
packaged-app acceptance remain pending. No Swift HomeConnector change, signing,
Jenkins change or native execution is part of C6a.
