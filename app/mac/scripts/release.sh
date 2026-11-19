#!/bin/bash
# Founder-run Bash 3.2 orchestrator. Existing tools retain all native guards.
set +x
set -euo pipefail
umask 077
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../../.." && pwd -P)
STEP=arguments RUN='' VERSION='' BUILD='' KEY='' TOOLS='' INPUT='' CHANNEL=''
FEED_OUT='' PUBLISH_CMD='' RECOVER='' BOOTSTRAP=false DRY=false
fail() { printf 'STOP [%s]: %s\nResume with the identical command and --run-dir %s; preserve all retained state.\n' "$STEP" "$*" "${RUN:-ABSOLUTE_PATH}" >&2; exit 1; }
usage() {
    cat <<'EOF'
Founder only, in a clean reviewed checkout on native arm64 macOS:
  bash app/mac/scripts/release.sh --version 1.1.0 --build 1.1.0 --sparkle-public-key PUBLIC_KEY --sparkle-tools /absolute/Sparkle-2.10.1/bin --run-dir "$HOME/HappyRanch-signing/N" --bootstrap --feed-out /absolute/host/mac/appcast.xml
  bash app/mac/scripts/release.sh --version 1.1.1 --build 1.1.1 --sparkle-public-key PUBLIC_KEY --sparkle-tools /absolute/Sparkle-2.10.1/bin --run-dir "$HOME/HappyRanch-signing/N-plus-1" --input "$HOME/HappyRanch-signing/N/appcast.xml" --feed-out /absolute/host/mac/appcast.xml
Required: --version, --build, --sparkle-public-key, --sparkle-tools, --run-dir,
          exactly one of --bootstrap (first-ever feed ONLY) or --input PREVIOUS_FEED.
Optional: --channel internal, --feed-out PATH, --publish-cmd 'COMMAND', --dry-run,
          --recover-id ACTUAL_APPLE_UUID (only after founder history reconciliation).
Source commit/tree are resolved from this checkout, then pinned for every resume.
RUN's existing parent must be owned mode0700, outside the checkout. RUN is private.
The existing signing env file is read ONLY by mac-sign.sh; never source it.
Sparkle tools must be the reviewed 2.10.1 tools; private key stays in Keychain.
One Apple status observation per invocation; pending stops. Rerun at owner-chosen
times (at most three observations in a local session, around 0, 5, 15 minutes).
An interactive terminal must type REVIEWED <log SHA256> before finalization.
Publishing requires y/Y (default N). COMMAND receives the feed as $1 and
HAPPYRANCH_RELEASE_FEED; use existing auth, never put secrets in COMMAND.
No publisher: retain RUN/appcast.xml locally. A declined publish can be resumed.
Dry-run writes nothing and invokes no build/signing/network/Keychain tools.
Future receipt/signature/log values are printed as explicit runtime expressions.
Interrupted build/sign/finalize/publish state is preserved for reconciliation;
never remove attempt markers to retry an uncertain external effect.
EOF
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --bootstrap) BOOTSTRAP=true; shift; continue ;;
        --dry-run) DRY=true; shift; continue ;;
    esac
    [ "$#" -ge 2 ] || fail "Missing value for $1"
    case "$1" in
        --version) VERSION=$2 ;; --build) BUILD=$2 ;;
        --sparkle-public-key) KEY=$2 ;; --sparkle-tools) TOOLS=$2 ;;
        --run-dir) RUN=$2 ;; --input) INPUT=$2 ;; --channel) CHANNEL=$2 ;;
        --feed-out) FEED_OUT=$2 ;; --publish-cmd) PUBLISH_CMD=$2 ;;
        --recover-id) RECOVER=$2 ;; *) fail "Unknown option: $1" ;;
    esac
    shift 2
done
[ -n "$VERSION" ] && [ -n "$BUILD" ] && [ -n "$KEY" ] && [ -n "$TOOLS" ] && [ -n "$RUN" ] || fail 'Missing required arguments; see --help.'
if $BOOTSTRAP; then [ -z "$INPUT" ] || fail '--bootstrap and --input are exclusive.'
else [ -n "$INPUT" ] || fail 'Use --input, or --bootstrap only for the first-ever feed.'; fi
[ -z "$CHANNEL" ] || [ "$CHANNEL" = internal ] || fail 'Only --channel internal is supported.'

# Local data only. No signing configuration, credentials or keys are inspected.
python3 -I -B - "$RUN" "$REPO" "$TOOLS" "$INPUT" "$FEED_OUT" "$VERSION" "$BUILD" "$KEY" "$RECOVER" <<'PY' || fail 'Invalid release arguments or private paths.'
import base64, os, re, stat, sys
from pathlib import Path
run, repo, tools, previous, feed, version, build, key, recover = sys.argv[1:]
for value in (version, build):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise SystemExit('Expected three-component version/build without leading zeros')
raw = base64.b64decode(key, validate=True)
if len(raw) != 32 or base64.b64encode(raw).decode() != key:
    raise SystemExit('Expected canonical 32-byte Sparkle PUBLIC key')
if recover and not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', recover):
    raise SystemExit('Recovery requires an actual reconciled Apple UUID')
for value in (run, tools, previous, feed):
    if not value:
        continue
    p = Path(value)
    if not p.is_absolute() or str(p) != value or '..' in p.parts or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SystemExit('Use plain absolute paths without dot components or controls')
    for part in (p, *p.parents):
        if part.is_symlink():
            raise SystemExit('Symlink path refused: ' + str(part))
r, source = Path(run), Path(repo)
if r == source or r in source.parents or source in r.parents:
    raise SystemExit('Run directory must be disjoint from the checkout')
for p in (r if r.exists() else r.parent,):
    s = p.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise SystemExit('Run directory/parent must be owned mode0700: ' + str(p))
if feed and (Path(feed) == r or r in Path(feed).parents or Path(feed) == source or source in Path(feed).parents):
    raise SystemExit('Feed destination must be outside the run directory and checkout')
PY
SHA=$(git --no-replace-objects -C "$REPO" rev-parse HEAD)
TREE=$(git --no-replace-objects -C "$REPO" rev-parse 'HEAD^{tree}')
ORIGINALS="$RUN/originals" OUTPUT="$RUN/build" SIGNING="$RUN/signing" PKG="$RUN/tools"
ZIP="$OUTPUT/HappyRanch-$VERSION-$BUILD-arm64.zip" RECEIPT="$OUTPUT/build-receipt.json"
PINS="$RUN/build.pins" FINAL="$SIGNING/final/HappyRanch-$VERSION-$BUILD-arm64-signed-notarized-stapled.zip"
FEED="$RUN/appcast.xml" TAG="v$BUILD" ASSET=${FINAL##*/}
URL="https://github.com/t-benze/happyranch/releases/download/$TAG/$ASSET"
feed_args=(--bootstrap)
$BOOTSTRAP || feed_args=(--input "$INPUT")
channel_args=()
[ -z "$CHANNEL" ] || channel_args=(--channel "$CHANNEL")
cmd() { printf '  '; printf '%q ' "$@"; printf '\n'; }
if $DRY; then
    printf 'Dry-run: complete fresh-run order; no commands below are executed.\n'
    cmd mkdir -m 700 "$RUN"
    cmd mkdir "$PKG"
    cmd cp "$HERE/mac-sign.sh" "$PKG/mac-sign.sh"
    cmd cp "$REPO/ci/jenkins/mac-signing/code-inventory.tsv" "$PKG/code-inventory.tsv"
    cmd python3 -B "$HERE/prepare_web_originals.py" --source "$REPO" --destination "$ORIGINALS"
    cmd bash "$HERE/build-app.sh" --version "$VERSION" --build "$BUILD" --source-sha "$SHA" --source-tree "$TREE" --arch arm64 --web-tool-originals "$ORIGINALS" --output-dir "$OUTPUT" --sparkle-public-key "$KEY"
    cmd python3 -I -B - "$RECEIPT" "$ZIP" "$PINS" "$KEY" "$VERSION" "$BUILD" "$SHA" "$TREE"
    printf '  # Verify receipt size/SHA256/source/version; write exact 8-line pins, LAYOUT_SHA = unzip -Z1 | shasum -a 256.\n'
    cmd bash "$PKG/mac-sign.sh" pins "$PINS"
    cmd bash "$PKG/mac-sign.sh" preflight "$PINS" "$ZIP"
    cmd bash "$PKG/mac-sign.sh" sign "$PINS" "$ZIP" "$SIGNING"
    cmd bash "$PKG/mac-sign.sh" submit "$SIGNING"
    if [ -n "$RECOVER" ]; then cmd bash "$PKG/mac-sign.sh" status "$SIGNING" --recover-id "$RECOVER"
    else cmd bash "$PKG/mac-sign.sh" status "$SIGNING"; fi
    printf '  # Print entire Accepted snapshot log.json; type REVIEWED <sha256> in terminal.\n'
    cmd bash "$PKG/mac-sign.sh" finalize "$SIGNING" --log-reviewed "\$(shasum -a 256 \"\$ACCEPTED_LOG\" | awk '{print \$1}')"
    cmd bash "$PKG/mac-sign.sh" test "$SIGNING"
    cmd "$TOOLS/sign_update" --account happyranch "$FINAL"
    cmd "$TOOLS/sign_update" --account happyranch --verify "$FINAL" "\$ED_SIGNATURE (from retained sign_update output)"
    cmd python3 -B "$HERE/appcast_entry.py" "${feed_args[@]}" --output "$FEED" --archive "$FINAL" --version "$VERSION" --build "$BUILD" --public-key "$KEY" --signature "\$ED_SIGNATURE" --length "\$ED_LENGTH" --url "$URL" ${channel_args[@]+"${channel_args[@]}"}
    cmd gh release create "$TAG" "$FINAL" --repo t-benze/happyranch --target "$SHA" --title "HappyRanch $VERSION ($BUILD)" --notes "HappyRanch macOS $VERSION ($BUILD)"
    cmd gh release download "$TAG" --repo t-benze/happyranch --pattern "$ASSET" --dir "$RUN/download.RUNTIME"
    cmd cmp "$FINAL" "$RUN/download.RUNTIME/$ASSET"
    printf '  # Compare downloaded byte length AND SHA256; only then prompt Publish feed? [y/N].\n'
    [ -z "$FEED_OUT" ] || cmd cp "$FEED" "$FEED_OUT"
    [ -z "$PUBLISH_CMD" ] || cmd bash -c "set +x; $PUBLISH_CMD" release-publisher "$FEED"
    exit 0
fi
STEP=source
[ -z "$(git --no-replace-objects -C "$REPO" status --porcelain --untracked-files=all)" ] || fail 'Use a clean reviewed checkout.'
[ -x "$TOOLS/sign_update" ] || fail 'Missing Sparkle 2.10.1 sign_update.'
[ -d "$RUN" ] || mkdir -m 700 "$RUN"
mkdir "$RUN/.lock" 2>/dev/null || fail 'Run is locked. Reconcile the prior process before removing only the empty .lock directory.'
trap 'rc=$?; rmdir "$RUN/.lock"; if [ "$rc" -ne 0 ]; then printf "STOP [%s] exit=%s; rerun the identical command with --run-dir %s. Never remove attempt markers.\n" "$STEP" "$rc" "$RUN" >&2; fi' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

hash() { shasum -a 256 "$1" | awk '{print $1}'; }
# Each completion binds the stable public files/directories it consumed/produced.
# Verify ALL prior checkpoints before any further external effect on every run.
checkpoint() {
    python3 -I -B - "$RUN" "$@" <<'PY'
import hashlib, json, os, stat, sys
from pathlib import Path
root, action, *args = sys.argv[1:]
def fingerprint(value):
    p = Path(value)
    for part in (p, *p.parents):
        if part.is_symlink():
            raise SystemExit('Checkpoint symlink refused: ' + str(part))
    s = p.stat()
    if s.st_uid != os.getuid():
        raise SystemExit('Checkpoint not owned: ' + str(p))
    if stat.S_ISDIR(s.st_mode):
        return {str(q.relative_to(p)): fingerprint(str(q)) for q in sorted(p.iterdir())}
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise SystemExit('Checkpoint requires single-link regular file: ' + str(p))
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return [s.st_size, h.hexdigest()]
if action == 'verify':
    for marker in sorted(Path(root).glob('*.done')):
        for value, expected in json.loads(marker.read_text()).items():
            if fingerprint(value) != expected:
                raise SystemExit('Checkpoint hash changed: ' + value + ' (' + marker.name + ')')
else:
    name, *paths = args
    result = {p: fingerprint(p) for p in paths}
    pending = Path(root) / (name + '.pending')
    with pending.open('x') as f:
        json.dump(result, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(pending, Path(root) / (name + '.done'))
PY
}
STEP=resume-validation
CONFIG=$(printf '%s\0' "$VERSION" "$BUILD" "$KEY" "$TOOLS" "$INPUT" "$BOOTSTRAP" "$CHANNEL" "$FEED_OUT" "$PUBLISH_CMD" "$SHA" "$TREE" "${HAPPYRANCH_MAC_SIGNING_ENV:-}" "$(hash "$0")" | shasum -a 256 | awk '{print $1}')
if [ -e "$RUN/config.sha256" ]; then
    [ "$(cat "$RUN/config.sha256")" = "$CONFIG" ] || fail 'Arguments/source/wrapper changed. Restore the original inputs; never reuse this run for different bytes.'
else printf '%s\n' "$CONFIG" > "$RUN/config.sha256"; fi
checkpoint verify
if [ ! -f "$RUN/tools.done" ]; then
    STEP=tools
    [ ! -e "$PKG" ] || fail 'Interrupted tool copy; reconcile preserved tools.'
    mkdir "$PKG"
    cp "$HERE/mac-sign.sh" "$PKG/mac-sign.sh"
    cp "$REPO/ci/jenkins/mac-signing/code-inventory.tsv" "$PKG/code-inventory.tsv"
    checkpoint save tools "$PKG" "$HERE/build-app.sh" "$HERE/prepare_web_originals.py" "$HERE/appcast_entry.py" "$HERE/verify-release.sh" "$TOOLS/sign_update"
fi
STEP=originals
if [ ! -f "$RUN/originals.done" ]; then
    [ ! -e "$ORIGINALS" ] || fail 'Interrupted originals preparation; reconcile before continuing.'
    python3 -B "$HERE/prepare_web_originals.py" --source "$REPO" --destination "$ORIGINALS"
    checkpoint save originals "$ORIGINALS"
fi
STEP=build
if [ ! -f "$RUN/build.done" ]; then
    if [ ! -e "$OUTPUT" ]; then
        rc=0
        bash "$HERE/build-app.sh" --version "$VERSION" --build "$BUILD" --source-sha "$SHA" --source-tree "$TREE" --arch arm64 --web-tool-originals "$ORIGINALS" --output-dir "$OUTPUT" --sparkle-public-key "$KEY" || rc=$?
        printf '%s\n' "$rc" > "$RUN/build.exit"
    fi
    [ -f "$RUN/build.exit" ] && [ "$(cat "$RUN/build.exit")" = 0 ] || fail 'Interrupted/failed build; preserve diagnostics and reconcile.'
    # pins is completed below only after receipt bytes and identities agree.
fi
STEP=receipt-and-pins
if [ ! -f "$RUN/build.done" ]; then
    [ ! -e "$PINS" ] || fail 'Interrupted pin creation; reconcile preserved pins.'
    python3 -I -B - "$RECEIPT" "$ZIP" "$PINS" "$KEY" "$VERSION" "$BUILD" "$SHA" "$TREE" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
receipt, archive, pins, key, version, build, source, tree = sys.argv[1:]
r = json.loads(Path(receipt).read_text())
for name, expected in [('version', version), ('build', build), ('source_sha', source), ('source_tree', tree)]:
    if r[name] != expected:
        raise SystemExit('Receipt identity mismatch: ' + name)
p = Path(archive)
h = hashlib.sha256()
with p.open('rb') as stream:
    for data in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(data)
sha = h.hexdigest()
if type(r['size']) is not int or p.stat().st_size != r['size'] or sha != r['sha256']:
    raise SystemExit('Unsigned ZIP size/SHA256 differs from build-receipt.json')
layout = hashlib.sha256(subprocess.check_output(['unzip', '-Z1', archive])).hexdigest()
values = [version, build, str(r['size']), sha, layout, source, tree, key]
names = ['VERSION', 'BUILD', 'INPUT_SIZE', 'INPUT_SHA', 'LAYOUT_SHA', 'SOURCE', 'SOURCE_TREE', 'SUPUBLIC_ED_KEY']
with Path(pins).open('x') as f:
    f.write(''.join(n + '=' + v + '\n' for n, v in zip(names, values)))
PY
    bash "$PKG/mac-sign.sh" pins "$PINS"
    checkpoint save build "$RECEIPT" "$ZIP" "$PINS" "$RUN/build.exit"
fi
STEP=preflight
if [ ! -f "$RUN/preflight.done" ]; then
    bash "$PKG/mac-sign.sh" preflight "$PINS" "$ZIP"
    checkpoint save preflight "$PINS" "$ZIP"
fi
STEP=sign
if [ ! -f "$RUN/sign.done" ]; then
    if [ ! -e "$SIGNING" ]; then bash "$PKG/mac-sign.sh" sign "$PINS" "$ZIP" "$SIGNING"; fi
    [ -f "$SIGNING/sign.complete" ] || fail 'Interrupted signing. Reconcile; never re-sign this run.'
    checkpoint save sign "$PINS" "$ZIP" "$SIGNING/sign.complete" "$SIGNING/original.zip" "$SIGNING/signed-files.txt"
fi
STEP=submit
if [ ! -e "$SIGNING/submission" ] && [ ! -e "$RUN/submit.intent" ]; then
    # Reserve BEFORE calling the one-attempt native guard, even across a crash.
    (set -C; printf '%s\n' "$(hash "$ZIP")" > "$RUN/submit.intent")
    sync
    bash "$PKG/mac-sign.sh" submit "$SIGNING"
fi
[ -f "$SIGNING/submission/ATTEMPTED" ] || fail 'Submission preparation is interrupted/uncertain. Reconcile; NEVER repeat submit.'
if [ ! -f "$SIGNING/submission/id.txt" ] && [ -z "$RECOVER" ]; then
    fail 'No saved Apple ID. Reconcile actual history; rerun with --recover-id ACTUAL_APPLE_UUID. NEVER repeat submit.'
fi
if [ ! -f "$RUN/submit.done" ]; then
    SUBMISSION=$(cat "$SIGNING/submission/artifact-name.txt")
    [[ $SUBMISSION != */* ]] && [ -n "$SUBMISSION" ] || fail 'Unsafe submission filename; reconcile.'
    checkpoint save submit "$ZIP" "$SIGNING/submission/$SUBMISSION" "$SIGNING/submission/artifact-name.txt" "$SIGNING/submission/artifact.sha256" "$SIGNING/submission/artifact.size" "$SIGNING/submission/ATTEMPTED"
fi
STEP=status
if [ ! -f "$RUN/status.done" ]; then
    status_args=()
    [ -f "$SIGNING/submission/id.txt" ] || status_args=(--recover-id "$RECOVER")
    # ONE observation, never a hidden polling loop (handbook maximum is three).
    bash "$PKG/mac-sign.sh" status "$SIGNING" ${status_args[@]+"${status_args[@]}"} || fail 'Apple status not accepted or observation failed. Inspect retained info/log; pending may resume later with this run. No submission is repeated.'
    SNAP=$(cat "$SIGNING/accepted-status.txt")
    [[ $SNAP =~ ^status\.[A-Za-z0-9]{6}$ ]] || fail 'Invalid Accepted snapshot; reconcile.'
    checkpoint save status "$SIGNING/accepted-status.txt" "$SIGNING/$SNAP/info.json" "$SIGNING/$SNAP/log.json" "$SIGNING/submission/id.txt"
fi
SNAP=$(cat "$SIGNING/accepted-status.txt")
LOG="$SIGNING/$SNAP/log.json" LOG_SHA=$(hash "$SIGNING/$SNAP/log.json")
STEP=log-review
if [ ! -f "$RUN/log-review.done" ]; then
    cat "$LOG"
    printf '\nReview ALL warnings; stop for unexplained warnings. To confirm review, type REVIEWED %s: ' "$LOG_SHA" > /dev/tty
    IFS= read -r answer < /dev/tty || fail 'Interactive review required.'
    [ "$answer" = "REVIEWED $LOG_SHA" ] || fail 'Log review was not confirmed.'
    checkpoint save log-review "$LOG"
fi
STEP=finalize
if [ ! -f "$RUN/finalize.done" ]; then
    if [ ! -e "$SIGNING/final" ]; then bash "$PKG/mac-sign.sh" finalize "$SIGNING" --log-reviewed "$LOG_SHA"; fi
    [ -f "$SIGNING/final/complete" ] || fail 'Interrupted finalization; reconcile, never re-ZIP over partial output.'
    [ "$(hash "$FINAL")" = "$(cat "$SIGNING/final/final.sha256")" ] || fail 'Final ZIP hash mismatch.'
    checkpoint save finalize "$LOG" "$FINAL" "$SIGNING/final/complete" "$SIGNING/final/final.sha256" "$SIGNING/final/final.size"
fi
STEP='test'
if [ ! -f "$RUN/test.done" ]; then
    bash "$PKG/mac-sign.sh" test "$SIGNING"
    checkpoint save test "$FINAL"
fi
STEP=sparkle-sign
if [ ! -f "$RUN/sparkle-sign.done" ]; then
    checkpoint verify
    if [ ! -e "$RUN/sparkle.intent" ]; then
        (set -C; printf '%s\n' "$(hash "$FINAL")" > "$RUN/sparkle.intent")
        rc=0
        "$TOOLS/sign_update" --account happyranch "$FINAL" > "$RUN/sign-update.txt" || rc=$?
        printf '%s\n' "$rc" > "$RUN/sign-update.exit"
    fi
    [ "$(hash "$FINAL")" = "$(cat "$RUN/sparkle.intent")" ] || fail 'Final ZIP changed since Sparkle signing; never re-sign different bytes.'
    [ -f "$RUN/sign-update.exit" ] && [ "$(cat "$RUN/sign-update.exit")" = 0 ] || fail 'Sparkle signing failed/interrupted; reconcile, never automatically repeat.'
    [ -s "$RUN/sign-update.txt" ] || fail 'Interrupted Sparkle signing; reconcile, do not repeat automatically.'
    checkpoint save sparkle-sign "$FINAL" "$RUN/sign-update.txt" "$RUN/sign-update.exit"
fi
read -r ED_SIGNATURE ED_LENGTH < <(python3 -I -B - "$RUN/sign-update.txt" <<'PY'
import re, sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text()
m = re.fullmatch(r'\s*sparkle:edSignature="([A-Za-z0-9+/=]+)"\s+length="([1-9][0-9]*)"\s*', raw)
if not m:
    raise SystemExit('Unexpected sign_update output; preserve and reconcile (never eval it)')
print(*m.groups())
PY
) || fail 'Unexpected sign_update output; reconcile retained output, never eval or repeat it.'
STEP=sparkle-verify
if [ ! -f "$RUN/sparkle-verify.done" ]; then
    "$TOOLS/sign_update" --account happyranch --verify "$FINAL" "$ED_SIGNATURE"
    checkpoint save sparkle-verify "$FINAL" "$RUN/sign-update.txt"
fi
STEP=appcast
if [ ! -f "$RUN/appcast.done" ]; then
    [ ! -e "$FEED" ] || fail 'Interrupted feed creation; reconcile preserved feed.'
    python3 -B "$HERE/appcast_entry.py" "${feed_args[@]}" --output "$FEED" --archive "$FINAL" --version "$VERSION" --build "$BUILD" --public-key "$KEY" --signature "$ED_SIGNATURE" --length "$ED_LENGTH" --url "$URL" ${channel_args[@]+"${channel_args[@]}"}
    if $BOOTSTRAP; then checkpoint save appcast "$FINAL" "$RUN/sign-update.txt" "$FEED"
    else checkpoint save appcast "$FINAL" "$RUN/sign-update.txt" "$FEED" "$INPUT"; fi
fi
STEP=github-release
checkpoint verify
if [ ! -e "$RUN/github.intent" ]; then
    (set -C; printf '%s\n' "$(hash "$FINAL")" > "$RUN/github.intent")
    sync
    gh release create "$TAG" "$FINAL" --repo t-benze/happyranch --target "$SHA" --title "HappyRanch $VERSION ($BUILD)" --notes "HappyRanch macOS $VERSION ($BUILD)"
fi
# A lost create response is reconciled by download, NEVER by replacing an asset.
STEP=download-verification
DOWNLOAD=$(mktemp -d "$RUN/download.XXXXXX")
gh release download "$TAG" --repo t-benze/happyranch --pattern "$ASSET" --dir "$DOWNLOAD"
python3 -I -B - "$FINAL" "$DOWNLOAD/$ASSET" <<'PY'
import hashlib, sys
from pathlib import Path
def identity(value):
    p = Path(value)
    h = hashlib.sha256()
    with p.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return p.stat().st_size, h.hexdigest()
local, remote = map(identity, sys.argv[1:])
print('Final local size/SHA256:', *local)
print('Downloaded size/SHA256:', *remote)
if local != remote:
    raise SystemExit('Release asset mismatch: publishing REFUSED; preserve download and reconcile')
PY
checkpoint save github "$FINAL" "$DOWNLOAD/$ASSET"
STEP=publish
if [ -f "$RUN/publish.done" ]; then printf 'Feed already published for this run.\n'; exit 0; fi
if [ -z "$FEED_OUT$PUBLISH_CMD" ]; then printf 'Verified archive; feed retained at %s. No publisher configured.\n' "$FEED"; exit 0; fi
[ ! -e "$RUN/publish.intent" ] || fail 'Prior publication is uncertain. Reconcile the destination; never repeat an unknown publisher automatically.'
printf 'Archive verified. Publish feed %s? [y/N] ' "$FEED" > /dev/tty
IFS= read -r answer < /dev/tty || answer=''
case "$answer" in y|Y) ;; *) printf 'Publication skipped; feed retained at %s.\n' "$FEED"; exit 0 ;; esac
checkpoint verify
(set -C; printf '%s\n' "$(hash "$FEED")" > "$RUN/publish.intent")
sync
[ -z "$FEED_OUT" ] || cp "$FEED" "$FEED_OUT"
if [ -n "$PUBLISH_CMD" ]; then
    HAPPYRANCH_RELEASE_FEED="$FEED" bash -c "set +x; $PUBLISH_CMD" release-publisher "$FEED"
fi
if [ -n "$FEED_OUT" ]; then checkpoint save publish "$FEED" "$FINAL" "$FEED_OUT"
else checkpoint save publish "$FEED" "$FINAL"; fi
printf 'Published feed for %s; final archive %s.\n' "$TAG" "$URL"
