# Mac release handbook

Use this from a checkout of `t-benze/happyranch` on the founder's Mac mini.
CI builds an **unsigned inner app ZIP and build receipt**. The local agent signs
a copy, submits it once to Apple, then staples and verifies another copy. That
produces a **different signed/notarized/stapled ZIP with a new SHA256**.
Signing is not publication. Only the founder performs the publication steps below;
the local agent never uploads or publishes.

The app embeds pinned **Sparkle 2.10.1**. The updater starts only when the
bundle's `SUPublicEDKey` is canonical base64 of exactly 32 bytes. Optional build
argument `--sparkle-public-key PUBLIC_KEY` retains strict validation. With a
valid key the build writes `SUEnableAutomaticChecks=YES` and
`SUAutomaticallyUpdate=YES` (silent check/download); without one it omits the
key, writes both false, leaves the updater unstarted and disables Check for
Updates. The feed stays `https://app.happyranch.ai/mac/appcast.xml`. Only the
founder supplies the public key; no private key or generation is part of this build.

Relaunch requests always enter the app's postpone gate, including Sparkle's
scheduled/impatient prompt. The app consumes only the new read-only
`update_busy` aggregate on `GET /api/v1/health`. The former host admission gauge
counts attempt capacity and misses durable jobs and invocation retry gaps.
The new field covers logical/native owners, queues and indexed nonterminal
producer rows, including retained running jobs with unknown native liveness.
An old daemon, missing/invalid data or an unavailable producer stays BUSY.
See the [health contract](../agent-guides/web-and-cli.md#update-readiness-health-field).

An idle snapshot only presents Restart now / Later, with an explicit warning
that work starting before the stop will be interrupted. Update when they
finish polls every five seconds and re-presents this confirmation at idle;
it never auto-stops. Restart now performs a fresh read immediately before
TERM and returns to the busy prompt if busy/unknown. Update now and stop them
also re-reads and permits stopping known busy work by explicit consent.
Unavailable status disables immediate update. Either authorized stop must
confirm the same daemon exited before invoking the held Sparkle block.
Stop incomplete keeps the old app alive and never releases it. External
daemons and CLIENT role pass through without signaling a daemon.

Install on quit keeps the existing Quit/stop path; Sparkle installs after app
exit. A daemon surviving Quit's bounded TERM/KILL remains possible: the app
still exits and next-launch discovery treats the survivor as external. The
read-only observation is not an admission fence; the residual click-to-TERM
window is covered by the user's explicit warning/consent. Native acceptance
must cover that limitation and the prompt, wait, explicit-now and failed-stop
paths. Startup/abort/download failures are logged in Diagnostics → Updates,
without an app-owned alert for background checks. A one-time notice for
Downloads/disk-image/translocated launches asks the user to move to Applications.
See the [behavior matrix and native command](../../app/mac/README.md#internal-candidate-build).

**Native verification is manager-owned and NOT RUN on Linux.** At the exact PR
head, on the authorized Mac run
`swift test --package-path app/mac --filter 'UpdateGateTests|UpdateConfigurationTests'`.
The existing `happyranch-mac-native-probe` `packaged` suite builds the app via
`build-app.sh`/`swift build`, installs and drives it. After steps 1–7 pass and
the daemon stops, the required `packaged.update_tests` result runs that filtered
Swift test command with the supervisor's existing toolchain and scratch helpers.
The numbered steps and step-8 outer EXIT collector remain unchanged, preserving
build/packaged evidence on a test failure. Tests have at most 600 seconds within
the shared 4800-second suite deadline and 1 MiB output, under the existing
90-minute / 256 MiB-file job envelope. The report retains argv, exit, byte count,
SHA-256, a 2 KiB tail and bounded per-test results. Exit 0, a nonzero test run and
at least one executed-and-passed child in each suite type are required. The
command uses `--verbose --no-parallel` to preserve Swift Testing child ownership;
Swift Testing and XCTest skipped, cancelled or failed test/suite results fail
acceptance. Timeout, overflow, absent results and missing toolchain fail the suite.
Its `supervisor` filter and Jenkins configuration remain unchanged. Linux portable harness checks do not execute Swift.
Signed N→N+1, daemon data continuity, install on quit, scheduled prompts and
signature refusal still require the founder's signed builds and manager's
budgeted native venue. Build numbers must strictly increase for every published
archive; skipped-version upgrades remain additive and manual downgrade is unsupported.

The PR3 signing package accepts per-build public pins for a reviewed,
Sparkle-bearing artifact from the current build path. Both the short version and
`CFBundleVersion` must match the builder’s exact rule:
`(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)` (three numeric components,
no leading zeros). N+1 must carry a strictly higher three-component build than N;
Sparkle uses its standard version comparator for dotted numeric versions.
The offline [feed-entry tool](#step-4--sign-the-final-archive-and-prepare-the-feed-founder-only)
enforces that monotonic check. The signing package signs neither an appcast nor
an update archive with EdDSA; the founder uses Sparkle's tools on the final ZIP.
The unsigned build embeds `Contents/Frameworks/Sparkle.framework`, preserves
its eight known relative links, and removes both XPC services and their alias.
`@executable_path/../Frameworks` resolves from `Contents/MacOS/HappyRanch` to
that directory. Sparkle's full release LICENSE, including bsdiff/bspatch,
sais-lite, portable Ed25519 and SUSignatureVerifier terms, ships under the
`swift:Sparkle` notice owner. See the [build details](../../app/mac/README.md#internal-candidate-build).

Native packaged acceptance remains a separately budgeted packaged run after
independent review and QA: observe the framework in the bundle, all eight links
resolving, XPCServices absent, successful app launch (updater inert without a public key),
all packaged steps passing, and archive notices passing. Swift compilation,
runtime loading and native launch cannot be verified by the Linux script tests.

THR-360 C7 bundles the separate managed connector helper at
`Contents/Helpers/happyranch-tsnet-sidecar` (Darwin/arm64, Go1.23.12, cgo off).
Its Darwin THIRD_PARTY_NOTICES and dependency inventory are separate originals
in the app licenses manifest. Both existing signing scripts include a helper
present in the retained baseline before outer-app signing, with the existing
identity, runtime option and no new entitlements. PR3 binds each reviewed new
artifact through the per-build pin procedure below. No C7 native build, signing,
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

### Select an existing reviewed build

Reuse an existing reviewed Sparkle build when its receipt and independently
expected ZIP hash agree. The historical accepted 1.0.1 artifact predates Sparkle
and its public key; this package deliberately refuses it. Preserve its original
tools and receipts if resuming that historical run.

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
reviewed original; do not rebuild it to work around missing evidence.

Read `build-receipt.json` locally. Its actual fields are `role`
(`unsigned-inner-zip`), `filename`, `size`, `sha256`, `version`, `build`, `arch`,
`source_sha`, `source_tree`, `run_id`, `app_inventory_sha256`, `manifest_notices`,
`available`, `approved_url`, `signing`, `ordinary_reader_execution`.
`run_id` is the packaging script's generated UUID, **not** the GitHub run ID.
Check filename, size, version/build, arm64 and source/tree against the reviewed
request and expected receipt; retain the notices/diagnostic evidence. Unsigned
receipts report `available: false`, `approved_url: null`, signing and ordinary
reader execution `UNEXECUTED`; those are not publication or runtime acceptance.

Set ZIP and RECEIPT to the selected **inner** file and its build receipt:

```sh
set +x
ZIP="/absolute/selected/HappyRanch-VERSION-BUILD-arm64.zip"
RECEIPT="/absolute/selected/build-receipt.json"
/usr/bin/stat -f '%z %N' "$ZIP"
/usr/bin/shasum -a 256 "$ZIP"
```

Compare the size and SHA256 with the receipt **and the independently expected
hash**, not merely another file from the same download.
Mismatch, absent fields or uncertain provenance: stop and preserve the file;
never overwrite it or silently substitute another attempt.

## Step 3 — Sign locally, one separate stage at a time

Prepare one private tools directory from the independently reviewed tooling
commit. The tools can sign build N and build N+1 with separate pin files and run
directories; a different build does not require editing the script or inventory.
Keep the identical tools for all stages of an individual run.

```sh
REPO="$(pwd -P)"
REVIEWED_TOOL_SHA=FULL_REVIEWED_TOOLING_COMMIT
PKG="$HOME/HappyRanch-signing/tools-sparkle-pr3"
RUN="$HOME/HappyRanch-signing/build-N"
PINS="$HOME/HappyRanch-signing/build-N.pins"
```

Use plain absolute paths without symlink ancestors, dot components or control
characters. Inputs must be owner-owned files with one hard link. The signing
parent and run directories must be mode0700. If the parent is absent, create it
once with `/bin/mkdir -m 700 "$HOME/HappyRanch-signing"`; inspect an existing parent
instead of overwriting it. PKG, PINS and RUN must be new for these commands.
Run the preparation block in Bash, with tracing off:

```sh
set +x
set -eC
set -o pipefail
umask 077
/bin/mkdir -m 700 "$PKG"
git -C "$REPO" show "$REVIEWED_TOOL_SHA:app/mac/scripts/mac-sign.sh" > "$PKG/mac-sign.sh"
git -C "$REPO" show "$REVIEWED_TOOL_SHA:ci/jenkins/mac-signing/code-inventory.tsv" > "$PKG/code-inventory.tsv"
/usr/bin/shasum -a 256 "$PKG/mac-sign.sh" "$PKG/code-inventory.tsv"
```

Compare those hashes with the independently reviewed commit’s file bytes. The
script itself pins the inventory hash. `package.sha256` binds the script and
inventory again across all resumed stages. The owner script and Jenkins script
have identical pin, member, plist, Mach-O, inventory and manifest logic. Jenkins
retains its privileged Bash/environment cleanup, live stage/console/Keychain
checks, bounded captures, parent-owned completion markers, durable attempt
sealing, recovery association and fresh final re-extraction. Use the owner copy
for this procedure; copying it over the Jenkins copy would remove those guards.
Jenkins stays disabled; source delivery authorizes no installation or execution.

Create the following **eight-line, ordered, LF-terminated** public pin file. The
first seven values come from the reviewed build receipt except `LAYOUT_SHA`:
the current receipt has no ZIP-member-layout digest, so derive it using the exact
`unzip -Z1` byte stream of the independently hash-verified inner ZIP. This is
**not** the receipt’s `app_inventory_sha256`, which describes app contents.
`SUPUBLIC_ED_KEY` is the founder-supplied public key used for that build, checked
against the archived plist before signing. Never obtain it from a private key.

```sh
VERSION=$(/usr/bin/plutil -extract version raw -o - "$RECEIPT")
BUILD=$(/usr/bin/plutil -extract build raw -o - "$RECEIPT")
INPUT_SIZE=$(/usr/bin/plutil -extract size raw -o - "$RECEIPT")
INPUT_SHA=$(/usr/bin/plutil -extract sha256 raw -o - "$RECEIPT")
LAYOUT_SHA=$(/usr/bin/unzip -Z1 "$ZIP" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
SOURCE=$(/usr/bin/plutil -extract source_sha raw -o - "$RECEIPT")
SOURCE_TREE=$(/usr/bin/plutil -extract source_tree raw -o - "$RECEIPT")
SUPUBLIC_ED_KEY='FOUNDER_SUPPLIED_CANONICAL_PUBLIC_KEY'
printf 'VERSION=%s\nBUILD=%s\nINPUT_SIZE=%s\nINPUT_SHA=%s\nLAYOUT_SHA=%s\nSOURCE=%s\nSOURCE_TREE=%s\nSUPUBLIC_ED_KEY=%s\n' \
  "$VERSION" "$BUILD" "$INPUT_SIZE" "$INPUT_SHA" "$LAYOUT_SHA" "$SOURCE" "$SOURCE_TREE" "$SUPUBLIC_ED_KEY" > "$PINS"
/bin/chmod 600 "$PINS"
/bin/bash "$PKG/mac-sign.sh" pins "$PINS"
```

Require exit0. No comments, quotes, expansions, whitespace, duplicate/unknown
fields, blank lines or extra lines are permitted. Versions follow the builder’s
three-component rule above; size is a positive decimal of at most 12 digits
without leading zeros; SHA256 values are 64 lowercase hex digits; commit/tree
are 40 lowercase hex digits; the public key is canonical base64 of exactly
32 bytes. The pin file is at most4096 bytes, private mode0600 or stricter,
owner-owned, regular, single-link and without symlink ancestry. Parsing never
sources shell text or accepts ambient pin values. `pins` performs only public
data validation and needs no signing configuration, Keychain or native tools.

Restore PKG/ZIP/PINS/RUN variables in every new shell. Preserve partial state on
failure; never silently reuse or replace a run. Signing retains `build.pins` and
`pins.sha256` inside RUN; subsequent stages use only that hash-bound retained
copy, even if the founder’s original pin file changes.

### Preflight, then sign

```sh
/bin/bash "$PKG/mac-sign.sh" preflight "$PINS" "$ZIP"
```

Require exit0 and `READ-ONLY PREFLIGHT complete.` It checks ZIP size/hash/CRC,
member layout, the exact eight framework links, absence of XPC/Installer/Downloader,
plist/source/public-key/feed pins, arm64 Mach-O types, tools and exact
valid public identity. It neither queries the notary profile nor exercises
private-key access, and launches no app.

RUN must be absent. With the owner's local instruction to sign:

```sh
/bin/bash "$PKG/mac-sign.sh" sign "$PINS" "$ZIP" "$RUN"
```

Require exit0, `Signed run:` and `sign.complete`. Receipts retain `original.zip`,
`build.pins`, `pins.sha256`, `baseline/`, `baseline-data.txt`, `baseline.sha256`, `package.sha256`,
`work/HappyRanch.app`, `signed-files.txt`, `sign-logs/`.
It signs Sparkle Autoupdate, Updater.app, then Sparkle.framework before the existing
nested libraries/frozen executables, optional retained sidecar and the outer app.
This follows [Sparkle’s signing order](https://sparkle-project.org/documentation/sandboxing/#code-signing).
The three Sparkle targets and executable/app targets retain Hardened Runtime and
timestamps, without entitlements or deep signing. Universal Sparkle members must
contain a valid arm64 slice; HappyRanch’s existing code retains thin arm64 checks.
Only inventoried Mach-O bytes and exact bundle signature paths can change; all
eight symlink paths and literal targets remain identical in baseline, work,
submission and final ZIP. Extra, escaping, absolute or replaced links refuse.
Strict nested/outer verification checks identifiers,
team/authority, timestamps/runtime and original non-code bytes/modes, including
notices, README and plist. Failures retain the partial run: stop, never re-sign
it or automatically start another run.

### Repeat for N+1

After retaining all N receipts and its final ZIP, select N+1’s separately reviewed
receipt/inner ZIP and trusted public key. Set `RUN` to a new `build-N-plus-1`
directory and `PINS` to a new `build-N-plus-1.pins`, then repeat pin preparation,
preflight, sign, submit, status, full-log review, finalize and test with the same
PKG. N+1’s `BUILD` must be strictly higher than N’s three-component build;
the feed-entry tool enforces this against every item, across all channels.
The signing script deliberately performs no cross-run comparison.
Each final ZIP is under its own RUN/final directory, with its own size/hash and
Apple association. Founder-only certificate/notarization custody stays unchanged;
the EdDSA private key remains founder-only and is never read by these scripts.
For each final ZIP, continue with founder-only archive signing and feed preparation
below. Complete the first signing/notarization/stapling round for N before the
separate N+1 round. Keep both final ZIPs, public pins and receipts. The native
done-test needs both signed builds; no portable tooling test proves N→N+1.

### Submit ONCE, then observe status

Only after successful signing and the owner's local instruction to upload:

```sh
/bin/bash "$PKG/mac-sign.sh" submit "$RUN"
```

The exclusive `submission/` directory is the one-attempt gate. The script
rechecks signed bytes, archives and re-extracts
`HappyRanch-<VERSION>-<BUILD>-submission-<generated-UUID>.zip`, records `artifact-name.txt`,
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

Output: `$RUN/final/HappyRanch-<VERSION>-<BUILD>-arm64-signed-notarized-stapled.zip`.
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

## Step 4 — Sign the final archive and prepare the feed (founder only)

The order for every release is **codesign → notarize → staple → final ZIP →
sign_update → feed-entry tool → upload that ZIP to GitHub Releases → publish
appcast.xml**. Never sign the unsigned or notarization-submission ZIP for Sparkle.
Never change or re-ZIP the final artifact after signing it. A re-spin gets a new,
strictly higher build; never reuse a published build number, including one from
an internal entry. Keep the complete feed history; deleting old items or using
bootstrap again would discard the evidence used by the monotonic check.

### One-time EdDSA key custody

The founder alone obtains the official **Sparkle 2.10.1** release tools, retains
the selected release archive/provenance, and sets `SPARKLE_TOOLS` to its absolute
`bin` directory. This is the same version pinned by `app/mac/Package.swift` and
`Package.resolved`; do not silently substitute a newer tool. Run these commands
in the founder's private Mac Terminal with tracing off, never in agent jobs:

```sh
set +x
SPARKLE_TOOLS="/absolute/Sparkle-2.10.1/bin"
"$SPARKLE_TOOLS/generate_keys" --account happyranch
"$SPARKLE_TOOLS/generate_keys" --account happyranch -p
```

The private signing seed stays in the founder's login Keychain. Give agents only
the printed **public** key for the build's `--sparkle-public-key` and public pin
`SUPUBLIC_ED_KEY`. Do not regenerate a key for each release. Before shipping N,
the founder exports an offline backup directly to a private encrypted removable
volume (replace the path with a new file on that volume):

```sh
umask 077
"$SPARKLE_TOOLS/generate_keys" --account happyranch -x /Volumes/PRIVATE_OFFLINE/happyranch-eddsa-backup
```

Disconnect and secure the backup offline. Never put it in a checkout, shared
artifact, CI variable, log or chat; agents never inspect or handle it. Recovery
is founder-only using `generate_keys --account happyranch -f PRIVATE_BACKUP_PATH`
in a private Terminal; confirm the restored public key matches the shipped pin.
The [pinned generate_keys source](https://github.com/sparkle-project/Sparkle/blob/2.10.1/generate_keys/main.swift)
defines the account, public lookup and export/import options. Apple certificate
and notarization credential custody also remain founder-only.

### Sign, then independently verify with Sparkle

Restore `RUN`, `VERSION`, `BUILD`, `SUPUBLIC_ED_KEY`, `REPO` and `SPARKLE_TOOLS`
for the selected build. Require the preceding final/test stages to pass first:

```sh
FINAL_ZIP="$RUN/final/HappyRanch-$VERSION-$BUILD-arm64-signed-notarized-stapled.zip"
"$SPARKLE_TOOLS/sign_update" --account happyranch "$FINAL_ZIP"
```

Copy the printed `sparkle:edSignature` and `length` into these **public** variables,
then require exit0 from verification before feed publication:

```sh
ED_SIGNATURE='SIGNATURE_PRINTED_BY_SIGN_UPDATE'
ED_LENGTH='LENGTH_PRINTED_BY_SIGN_UPDATE'
"$SPARKLE_TOOLS/sign_update" --account happyranch --verify "$FINAL_ZIP" "$ED_SIGNATURE"
```

`--verify` **exists in 2.10.1**: the
[pinned sign_update source](https://github.com/sparkle-project/Sparkle/blob/2.10.1/sign_update/main.swift)
declares the flag, takes the archive followed by its signature, obtains the
selected Keychain account's public key and calls `ed25519_verify`. Confirm that
account's public key (the `generate_keys -p` output) matches `SUPUBLIC_ED_KEY`.
Keychain access is confined to the founder's tools/session, including verification.

**The Python feed-entry tool checks signature shape only, not Ed25519 authenticity.**
It has no crypto dependency and cannot detect a same-length archive substitution
or a canonical but wrong signature. Portable tests use random 64-byte placeholder
signatures and synthetic ZIPs; they prove no signing or stapling. Sparkle's
verification above is mandatory. The separately budgeted native done-test must
also demonstrate that a tampered archive is refused before installation.

### Create a new local feed

Retain an exact local copy of the currently published complete UTF-8 RSS 2.0 feed,
including internal entries. The tool does no network I/O, fetch, upload or publish.
It checks the ZIP byte count and archived `HappyRanch.app/Contents/Info.plist`
against the version/build arguments, fixed feed and public-key pin, then prepends
one entry while preserving every existing byte and item order. Builds use the
same three-component grammar as `verify-release.sh` and PR3 pins and compare
numerically (`1.0.10 > 1.0.9`), against **every** existing item.

Choose a new output filename in an owner-controlled directory. It must not
already exist or refer to the input; publication never modifies that input.
Malformed XML/version history, duplicate versions, reused/lower builds, invalid
signature shape, length/plist/key mismatch or a non-GitHub-Releases HTTPS URL
refuse with a named diagnostic and no new output. The successful write uses a
temporary sibling and atomic rename. It does not prove notarization or native
acceptance; those come from the retained signing receipts and native test.

```sh
BASE_FEED="/absolute/release/appcast-before-$BUILD.xml"
NEW_FEED="/absolute/release/appcast-$BUILD.xml"
DOWNLOAD_URL="https://github.com/t-benze/happyranch/releases/download/v$BUILD/$(basename "$FINAL_ZIP")"
python3 -B "$REPO/app/mac/scripts/appcast_entry.py" \
  --input "$BASE_FEED" --output "$NEW_FEED" --archive "$FINAL_ZIP" \
  --version "$VERSION" --build "$BUILD" --public-key "$SUPUBLIC_ED_KEY" \
  --signature "$ED_SIGNATURE" --length "$ED_LENGTH" --url "$DOWNLOAD_URL"
```

For the **very first feed only**, replace `--input "$BASE_FEED"` with `--bootstrap`;
never bootstrap to bypass existing history. Retain that initial build as the
baseline when preparing N+1. Generated items contain version, short version,
minimum macOS `14.0.0`, hardware `arm64`, RFC2822 pubDate and the archive enclosure.

### Internal staging and public promotion

Add `--channel internal` to emit `<sparkle:channel>internal</sparkle:channel>`.
**Shipped builds have no `allowedChannels` opt-in, so internal entries are
invisible to every shipped build.** This is a safe staging slot, not an end-to-end
update-test channel; no Swift changes or debug channel opt-in are introduced.

To move the exact staged entry to public, rerun the same command **without
`--channel`**, using the retained **pre-staging** `BASE_FEED` and a new output
filename. Keep the identical ZIP, build, signature and length. Do not use the
feed already containing that internal build: the tool correctly refuses reuse.
This changes visibility of one archive, never assigns its build to new bytes.
Do not discard any other published item; if the feed advanced after staging,
reconcile the history and prepare a new higher build rather than overwriting
newer releases with the old baseline.

## Step 5 — Upload, then publish (founder only)

After signature verification and the applicable reviewed/native acceptance,
the founder uploads the exact `FINAL_ZIP` to the selected GitHub Release asset
URL. Verify its downloaded bytes/length/SHA256 against the retained final ZIP;
then publish the reviewed `NEW_FEED` at
`https://app.happyranch.ai/mac/appcast.xml`. Publishing the public entry offers
the update. Never publish the feed before its archive is available. Hosting,
credentials, upload and feed deployment stay founder-owned; this tool implements
no publishing command. Retain both feed snapshots, signing/verification exits,
final ZIP hash/length, exact asset URL and publication evidence.

For N/N+1 acceptance, establish the signed/keyed N baseline, then repeat the
entire founder signing round for a separately reviewed, higher N+1. Build its
entry against N's retained complete feed. The manager's authorized native venue
must prove N→N+1 from the real feed, waiting while sessions run, explicit update,
install on quit, restarted daemon/data continuity and tampered-archive refusal.
These remain **NOT RUN** until actually observed; portable tooling and a valid
signature do not substitute for them.

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
> it locally for the reviewed Sparkle build and its exact per-build pins. Reuse my matching
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

### Managed bridge health record (THR-327 A3a)

The Go-only `tsnet_health_json(timeoutMs)` export returns compact v1 JSON for a
running managed engine, selecting only its pinned home peer (empty placeholder
until a managed dial pins it). Free it with `tsnet_free_string`; NULL means fixed
`Managed health unavailable`. The whole call is capped at 10 seconds (a positive
smaller timeout wins; nonpositive uses the cap), including serialized-call waiting.
One Runner per successful up caches DERP-map-only probes for ten minutes; one
3-second disco ping may run at a time. First successful export is `join`, later
ones are `on_demand`. Engine cancellation/close rejects outstanding results.
The Swift cadence/button and Diagnostics entry are described in A3b below.
The existing fifteen-export release inspector does not yet assert this new symbol;
A3b's native build/linkage proof must include it. This slice does not build the app.

### Managed connection health display (THR-327 A3b)

The managed connection form supplements the existing state with a line such as
“Connected · direct (LAN) · 12 ms” or “Connected · relayed via hk · 84 ms · UDP
blocked”. Missing/invalid/unsupported records hide the line. **Check connection**
runs an on-demand refresh and stays disabled during the request. Automatic
refresh runs every 60 seconds only while the form's hosting NSWindow is visible,
unoccluded, non-minimized and its SwiftUI scene is active. Disappearance or loss
of eligibility cancels the cadence; a bounded request already in flight may
finish. There is no background probe. Calls run off the main actor, bounded to
10 seconds, and preserve the Go export's `join` then `on_demand` trigger contract.

Diagnostics shows and bundles `tsnet_health`, the full latest successful cached
v1 JSON record as a redacted string, or explicit `unavailable`. No probe is
started during export. Check the record's `at` timestamp for cache age, including
after disconnect. Native visual/cadence acceptance and Swift tests require a
macOS venue; the unsigned build workflow compiles but does not run Swift tests.
