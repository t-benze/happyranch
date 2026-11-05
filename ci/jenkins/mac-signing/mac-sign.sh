#!/bin/bash -p
# Adapted from accepted TASK14131 script SHA256 f65b0e9833e246c2a4617ac8250388728ae239b436874f03b204d3c5f1eb6780.
# Jenkins private stage only; Bash 3.2. No app code is executed.
set +x
set -euo pipefail
unset BASH_ENV ENV PERL5OPT PERL5LIB CDPATH GLOBIGNORE
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
umask 077
INPUT_SHA=85b96655a1d072328872fa8797e16ad0087e0e05aa30b8dda2a8bc78b60ac243
INPUT_SIZE=49089611
LAYOUT_SHA=75ae7de3fb835fc88112f8cf188340dcc7ca54a6094eacede5fa4232e648064a
IDENTITY=C9C7371D6FBFDB86BB847CC1DE181EAF2E3A4BD9
PUBLIC_NAME='Developer ID Application: Shenzhen Danze Internet Information Service Co., Ltd (LS2C558RKS)'
TEAM=LS2C558RKS
PROFILE=HappyRanch-notary
BUNDLE=ai.happyranch.HappyRanchApp
SOURCE=21c63d8c1ec520764945d7b0bafb4bfe668ab81d
SOURCE_TREE=b2af3a4c8e0cb1c6b58796030085dfb31680b374
fail() { printf 'STOP: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Internal native stages (only the checked run-stage.sh context may invoke):
  /bin/bash mac-sign.sh preflight ORIGINAL.zip
  /bin/bash mac-sign.sh sign ORIGINAL.zip NEW_PRIVATE_RUN_DIRECTORY
  /bin/bash mac-sign.sh submit RUN_DIRECTORY
  /bin/bash mac-sign.sh status RUN_DIRECTORY [--recover-id ACTUAL_APPLE_UUID]
  /bin/bash mac-sign.sh finalize RUN_DIRECTORY --log-reviewed LOG_SHA256
  /bin/bash mac-sign.sh test RUN_DIRECTORY
Preflight is read-only. Sign creates a new directory. Submit uploads ONCE.
Status never submits. Finalize requires Accepted and your inspection of that log.
Test checks the final re-extracted app; Finder/GUI/install steps are in README.
No sudo, secret arguments, credential setup, app execution or security bypass.
EOF
}
[ "$#" -gt 0 ] || { usage >&2; exit 2; }
STAGE=$1; shift
case "$STAGE" in
    help|--help|-h) [ "$#" -eq 0 ] || exit 2; usage; exit 0 ;;
    preflight|submit|test) [ "$#" -eq 1 ] || { usage >&2; exit 2; } ;;
    sign) [ "$#" -eq 2 ] || { usage >&2; exit 2; } ;;
    status) [ "$#" -eq 1 ] || { [ "$#" -eq 3 ] && [ "$2" = --recover-id ]; } || { usage >&2; exit 2; } ;;
    finalize) [ "$#" -eq 3 ] && [ "$2" = --log-reviewed ] || { usage >&2; exit 2; } ;;
    *) usage >&2; exit 2 ;;
esac
HERE=$(cd "$(dirname "$0")" && pwd -P)
[ -n "${HR_STAGE_DIR:-}" ] && [ "${ACTION:-}" = "$STAGE" ] || fail 'Missing checked stage context.'
/usr/bin/perl "$HERE/bounded-run.pl" --gate
[ "$(uname -s)" = Darwin ] || fail 'macOS required; no work performed.'
[ "$(uname -m)" = arm64 ] || fail 'Native arm64 shell required; do not run through Rosetta.'
[ "$(id -u)" -ne 0 ] || fail 'Use your own login account, without sudo.'
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
HERE=$(cd "$(dirname "$0")" && pwd -P)
INVENTORY="$HERE/code-inventory.tsv"
sha() { /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
[ "$(sha "$INVENTORY")" = 16ee1fd168286ca9bbfe0ed7ea683ed98df0d8470b7916ad57de7b0907538af3 ] || fail 'Package inventory mismatch.'
# No aliases, dot components, hardlinked input/state files or writable shared run.
plain_path() {
    local p=$1 part
    case "$p" in /*) ;; *) fail 'Use an absolute path.' ;; esac
    case "$p" in *$'\n'*|*$'\r'*|*$'\t'*|*/../*|*/./*|*//*|*/..|*/.|*/) fail 'Use a plain absolute path.' ;; esac
    part=$p
    while [ "$part" != / ]; do
        [ ! -L "$part" ] || fail "Symlink path refused: $part"
        part=${part%/*}; [ -n "$part" ] || part=/
    done
}
owned_file() {
    plain_path "$1"
    [ -f "$1" ] && [ -r "$1" ] || fail "Missing readable file: $1"
    [ "$(stat -f '%u:%l' "$1")" = "$(id -u):1" ] || fail "File must be yours with one link: $1"
}
private_dir() {
    plain_path "$1"
    [ -d "$1" ] && [ "$(stat -f '%u:%Lp' "$1")" = "$(id -u):700" ] || fail "Need an existing owner-only mode0700 directory: $1"
}
plist_value() { /usr/bin/plutil -extract "$2" raw -o - "$1"; }
uuid_ok() { [[ $1 =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
check_plist() {
    local f=$1
    [ "$(plist_value "$f" CFBundleIdentifier)" = "$BUNDLE" ]
    [ "$(plist_value "$f" CFBundleExecutable)" = HappyRanch ]
    [ "$(plist_value "$f" CFBundlePackageType)" = APPL ]
    [ "$(plist_value "$f" CFBundleShortVersionString)" = 1.0.1 ]
    [ "$(plist_value "$f" CFBundleVersion)" = 1.0.1 ]
    [ "$(plist_value "$f" LSMinimumSystemVersion)" = 14.0 ]
    [ "$(plist_value "$f" GitCommitSHA)" = "$SOURCE" ]
    [ "$(plist_value "$f" GitTreeSHA)" = "$SOURCE_TREE" ]
    [ "$(plist_value "$f" ReleaseArchitecture)" = arm64 ]
}
header() { /usr/bin/od -An -v -tx1 | /usr/bin/awk 'NR==1 {gsub(/ /, ""); print}'; }
check_header() {
    local got=$1 typ=$2
    [ "$got" = "cffaedfe0c000001000000000${typ}000000" ] || fail 'Mach-O architecture/type mismatch.'
}
preflight() {
    local zip=$1 kind typ ident rel got
    owned_file "$zip"
    [ "$(stat -f %z "$zip")" = "$INPUT_SIZE" ] || fail 'Unsigned ZIP size mismatch.'
    [ "$(sha "$zip")" = "$INPUT_SHA" ] || fail 'Unsigned ZIP hash mismatch.'
    /usr/bin/unzip -tq "$zip"
    got=$(/usr/bin/unzip -Z1 "$zip" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
    [ "$got" = "$LAYOUT_SHA" ] || fail 'Exact ZIP member layout mismatch.'
    # Hash authenticates all members/modes, including no symlinks or extra code.
    while IFS=$'\t' read -r kind typ ident rel; do
        got=$(/usr/bin/unzip -p "$zip" "HappyRanch.app/$rel" | header)
        check_header "$got" "$typ"
        printf '%s arm64 Mach-O type%s\n' "$rel" "$typ"
    done < "$INVENTORY"
    # plutil reads the original member on stdin; no extraction or temp file.
    local key expected actual
    for key in CFBundleIdentifier CFBundleExecutable CFBundlePackageType CFBundleShortVersionString CFBundleVersion LSMinimumSystemVersion GitCommitSHA GitTreeSHA ReleaseArchitecture; do
        case "$key" in
            CFBundleIdentifier) expected=$BUNDLE ;; CFBundleExecutable) expected=HappyRanch ;;
            CFBundlePackageType) expected=APPL ;; CFBundleShortVersionString|CFBundleVersion) expected=1.0.1 ;;
            LSMinimumSystemVersion) expected=14.0 ;; GitCommitSHA) expected=$SOURCE ;;
            GitTreeSHA) expected=$SOURCE_TREE ;; ReleaseArchitecture) expected=arm64 ;;
        esac
        actual=$(/usr/bin/unzip -p "$zip" HappyRanch.app/Contents/Info.plist | /usr/bin/plutil -extract "$key" raw -o - -)
        [ "$actual" = "$expected" ] || fail "Archived plist mismatch: $key"
    done
    /usr/bin/sw_vers
    /usr/bin/xcodebuild -version
    [ -x /usr/bin/codesign ] || fail 'Missing executable stock codesign.'
    [ -x /usr/bin/ditto ] || fail 'Missing stock ditto.'
    /usr/bin/xcrun --find otool
    /usr/bin/xcrun notarytool --version
    /usr/bin/xcrun --find stapler
    [ -x /usr/bin/syspolicy_check ] && [ -x /usr/sbin/spctl ] || fail 'Missing current Gatekeeper tools.'
    owned_file "$KEYCHAIN"
    local identities
    identities=$(/usr/bin/security find-identity -v -p codesigning "$KEYCHAIN")
    printf '%s\n' "$identities" | /usr/bin/grep -F "$IDENTITY \"$PUBLIC_NAME\"" || fail 'Exact valid signing identity/name/team absent from owner login Keychain.'
    printf 'READ-ONLY PREFLIGHT complete. Existing notary profile is not queried here. No app executed.\n'
}
# Every native command in a mutating stage retains stdout, stderr and real exit.
capture() {
    local label=$1 rc; shift
    /usr/bin/perl "$HERE/bounded-run.pl" --gate || return 1
    /usr/bin/perl "$HERE/bounded-run.pl" --command "console-$label" 5 8192 "$LOG/console-$label" -- /usr/bin/stat -f %u /dev/console || return 1
    [ "$(cat "$LOG/console-$label.out")" = "$UID" ] || return 1
    local seconds=120 cap=262144
    [ "$label" != submit ] || seconds=300
    [ "$label" != log ] || cap=1048576
    case "$label" in
        sign-*|submit|info|log|history|staple)
            /usr/bin/perl "$HERE/bounded-run.pl" --command "lock-$label" 180 8192 "$LOG/lock-$label" -- /usr/bin/xcrun swift "$HERE/keychain-state.swift" "$KEYCHAIN" || return 1 ;;
    esac
    if /usr/bin/perl "$HERE/bounded-run.pl" --command "$label" "$seconds" "$cap" "$LOG/$label" -- "$@"; then rc=0; else rc=$?; fi
    printf '%s exit=%s (details: %s)\n' "$label" "$rc" "$LOG"
    return "$rc"
}
# All file bytes and modes; data mode permits only the 11 code files and exact
# outer signing/ticket paths to differ. All notices, README and plist stay exact.
manifest() (
    cd "$1"
    local mode=${2:-all} p rel
    [ -z "$(find . ! -type f ! -type d -print)" ] || fail 'Unexpected link/special file in app.'
    find . -print | sort | while IFS= read -r p; do
        rel=${p#./}
        if [ "$mode" = data ]; then
            case "$rel" in Contents/_CodeSignature|Contents/_CodeSignature/CodeResources|Contents/CodeResources) continue ;; esac
            if /usr/bin/awk -F '\t' -v p="$rel" '$4==p {found=1} END {exit !found}' "$INVENTORY"; then continue; fi
        fi
        if [ -d "$p" ]; then printf 'D %s %s\n' "$(stat -f %Lp "$p")" "$p"
        else printf 'F %s %s %s\n' "$(stat -f %Lp "$p")" "$(sha "$p")" "$p"; fi
    done
)
check_data() {
    manifest "$1" data > "$LOG/data.actual"
    /usr/bin/cmp "$RUN/baseline-data.txt" "$LOG/data.actual" || fail 'Original non-code bytes/modes changed (including notices/plist/README).'
}
verify_code() {
    local app=$1 verify_parent=$LOG kind typ ident rel target i=0 got
    local LOG
    LOG=$(mktemp -d "$verify_parent/verify.XXXXXX")
    check_plist "$app/Contents/Info.plist"
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$app/$rel"
        [ -f "$target" ] && [ -x "$target" ] || fail "Missing code: $rel"
        got=$(header < "$target"); check_header "$got" "$typ"
        [ "$kind" != main ] || target=$app
        capture "verify-$i" /usr/bin/codesign --verify --strict --verbose=2 "$target"
        capture "metadata-$i" /usr/bin/codesign -d --verbose=4 "$target"
        /usr/bin/grep -Fx "Identifier=$ident" "$LOG/metadata-$i.err"
        /usr/bin/grep -Fx "TeamIdentifier=$TEAM" "$LOG/metadata-$i.err"
        /usr/bin/grep -Fx "Authority=$PUBLIC_NAME" "$LOG/metadata-$i.err"
        /usr/bin/grep -E '^Timestamp=.+$' "$LOG/metadata-$i.err"
        if /usr/bin/grep -Ei '^Timestamp=(none|not set|unavailable)$' "$LOG/metadata-$i.err"; then fail 'Missing secure timestamp.'; fi
        if [ "$kind" != lib ]; then /usr/bin/grep -E '^CodeDirectory .*flags=.*runtime' "$LOG/metadata-$i.err"; fi
        capture "entitlements-$i" /usr/bin/codesign -d --entitlements :- "$target"
        if /usr/bin/grep -q '<key>' "$LOG/entitlements-$i.out"; then fail "Unexpected entitlements: $rel"; fi
    done < "$INVENTORY"
    capture verify-outer /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
    check_data "$app"
}
load_run() {
    RUN=$1; private_dir "$RUN"
    owned_file "$RUN/package.sha256"
    [ "$(cat "$RUN/package.sha256")" = "$(sha "$HERE/mac-sign.sh") $(sha "$INVENTORY")" ] || fail 'Use the identical script and inventory that created this run.'
    owned_file "$RUN/sign.complete"
    [ "$(plist_value "$RUN/sign.complete" source)" = "$HR_SOURCE" ]
    [ "$(plist_value "$RUN/sign.complete" action)" = sign ]
    preflight_input="$RUN/original.zip"
    owned_file "$preflight_input"
    [ "$(sha "$preflight_input")" = "$INPUT_SHA" ] || fail 'Retained original ZIP changed.'
    [ "$(manifest "$RUN/baseline/HappyRanch.app" | /usr/bin/shasum -a 256 | awk '{print $1}')" = "$(cat "$RUN/baseline.sha256")" ] || fail 'Extracted baseline changed.'
    APP="$RUN/work/HappyRanch.app"
}
check_signed() {
    manifest "$APP" > "$LOG/signed.actual"
    /usr/bin/cmp "$RUN/signed-files.txt" "$LOG/signed.actual" || fail 'Work app changed since signing.'
}
check_submission() {
    owned_file "$RUN/submission/artifact-name.txt"
    ARTIFACT=$(cat "$RUN/submission/artifact-name.txt")
    [[ $ARTIFACT =~ ^HappyRanch-1\.0\.1-submission-[0-9A-Fa-f-]{36}\.zip$ ]] || fail 'Bad submission artifact name.'
    SUBZIP="$RUN/submission/$ARTIFACT"
    owned_file "$SUBZIP"
    SUBSHA=$(cat "$RUN/submission/artifact.sha256")
    [ "$(sha "$SUBZIP")" = "$SUBSHA" ] || fail 'Submission ZIP hash changed.'
    [ "$(stat -f %z "$SUBZIP")" = "$(cat "$RUN/submission/artifact.size")" ] || fail 'Submission ZIP size changed.'
}
check_accepted() {
    local snap=$1 id=$2
    /usr/bin/perl "$HERE/bounded-run.pl" --associated "$snap/info.json" "$snap/log.json" "$id" "$ARTIFACT" "$SUBSHA"
    [ "$(lower "$(plist_value "$snap/info.json" id)")" = "$(lower "$id")" ]
    [ "$(plist_value "$snap/info.json" name)" = "$ARTIFACT" ]
    [ "$(plist_value "$snap/info.json" status)" = Accepted ] || fail 'Apple info is not Accepted.'
    [ "$(lower "$(plist_value "$snap/log.json" jobId)")" = "$(lower "$id")" ]
    [ "$(plist_value "$snap/log.json" status)" = Accepted ] || fail 'Apple log is not Accepted.'
    [ "$(lower "$(plist_value "$snap/log.json" sha256)")" = "$SUBSHA" ] || fail 'Apple log does not match submitted ZIP hash.'
}
case "$STAGE" in
preflight) preflight "$1" ;;
sign)
    preflight "$1"
    RUN=$2; plain_path "$RUN"; private_dir "$(dirname "$RUN")"
    [ ! -e "$RUN" ] || fail 'Run directory already exists; preserve it, do not reuse.'
    mkdir "$RUN"
    LOG="$RUN/sign-logs"; mkdir "$LOG" "$RUN/baseline" "$RUN/work"
    printf '%s %s\n' "$(sha "$HERE/mac-sign.sh")" "$(sha "$INVENTORY")" > "$RUN/package.sha256"
    capture retain-unsigned /bin/cp -p "$1" "$RUN/original.zip"
    [ "$(sha "$RUN/original.zip")" = "$INPUT_SHA" ]
    capture extract-baseline /usr/bin/ditto -x -k "$RUN/original.zip" "$RUN/baseline"
    manifest "$RUN/baseline/HappyRanch.app" | shasum -a 256 | awk '{print $1}' > "$RUN/baseline.sha256"
    manifest "$RUN/baseline/HappyRanch.app" data > "$RUN/baseline-data.txt"
    capture copy-app /usr/bin/ditto "$RUN/baseline/HappyRanch.app" "$RUN/work/HappyRanch.app"
    manifest "$RUN/baseline/HappyRanch.app" > "$LOG/baseline-files.txt"
    manifest "$RUN/work/HappyRanch.app" > "$LOG/copy-files.txt"
    cmp "$LOG/baseline-files.txt" "$LOG/copy-files.txt"
    APP="$RUN/work/HappyRanch.app"; i=0
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$APP/$rel"
        capture "original-metadata-$i" /usr/bin/codesign -d --verbose=4 "$target"
        capture "original-entitlements-$i" /usr/bin/codesign -d --entitlements :- "$target"
        if grep -q '<key>' "$LOG/original-entitlements-$i.out"; then fail 'Unexpected original entitlements; return for review.'; fi
        capture "dependencies-$i" /usr/bin/otool -L "$target"
    done < "$INVENTORY"
    i=0
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$APP/$rel"
        case "$kind" in
            lib) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --identifier "$ident" "$target" ;;
            exec) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --options runtime --identifier "$ident" "$target" ;;
            main) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --options runtime "$APP" ;;
        esac
    done < "$INVENTORY"
    verify_code "$APP"
    manifest "$APP" > "$RUN/signed-files.txt"
    /usr/bin/perl "$HERE/bounded-run.pl" --save "$RUN/sign.ready" 'Signed and verified; parent has not published completion.'
    printf 'Signed run: %s\nNext, separately: submit RUN_DIRECTORY\n' "$RUN"
    ;;
submit)
    load_run "$1"
    # mkdir is also the durable one-attempt gate; never remove/reuse it.
    mkdir "$RUN/submission" || fail 'Submission already prepared/attempted. Use status/recovery; NEVER re-upload automatically.'
    LOG="$RUN/submission"; check_signed; verify_code "$APP"
    ARTIFACT="HappyRanch-1.0.1-submission-$(lower "$(/usr/bin/uuidgen)").zip"
    printf '%s\n' "$ARTIFACT" > "$LOG/artifact-name.txt"
    SUBZIP="$LOG/$ARTIFACT"
    capture archive /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$SUBZIP"
    mkdir "$LOG/reextracted"
    capture archive-extract /usr/bin/ditto -x -k "$SUBZIP" "$LOG/reextracted"
    manifest "$LOG/reextracted/HappyRanch.app" > "$LOG/archive-files.txt"
    cmp "$RUN/signed-files.txt" "$LOG/archive-files.txt"
    sha "$SUBZIP" > "$LOG/artifact.sha256"
    stat -f %z "$SUBZIP" > "$LOG/artifact.size"
    printf 'Artifact SHA256 %s; size %s\n' "$(cat "$LOG/artifact.sha256")" "$(cat "$LOG/artifact.size")"
    date -u '+attempt-started %Y-%m-%dT%H:%M:%SZ; never automatically repeat' > "$LOG/ATTEMPTED"
    # Flush the exact attempt/name/size/hash before the sole Apple submit call.
    /usr/bin/perl "$HERE/bounded-run.pl" --seal-attempt "$LOG"
    rc=0
    capture submit /usr/bin/xcrun notarytool submit "$SUBZIP" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --output-format json || rc=$?
    # Even a failed command may have returned a genuine ID. Preserve it first.
    id=$(/usr/bin/plutil -extract id raw -o - "$LOG/submit.out" 2>/dev/null) || id=''
    if uuid_ok "$id"; then /usr/bin/perl "$HERE/bounded-run.pl" --save "$LOG/id.txt" "$(lower "$id")"; fi
    [ "$rc" -eq 0 ] || fail "Submit exit=$rc; inspect retained response and use status/history reconciliation, never repeat submit."
    uuid_ok "$id" || fail 'Submission outcome ambiguous: no valid returned ID. Reconcile history; do not upload again.'
    printf 'Saved actual submission ID: %s\nUse status separately; upload is not acceptance.\n' "$id"
    ;;
status)
    load_run "$1"; check_submission
    [ -f "$RUN/submission/ATTEMPTED" ] || fail 'No submission attempt recorded.'
    if [ "$#" -eq 3 ]; then
        [ ! -e "$RUN/submission/id.txt" ] || fail 'An ID is already saved; recovery cannot replace it.'
        id=$3
    else owned_file "$RUN/submission/id.txt"; id=$(cat "$RUN/submission/id.txt"); fi
    uuid_ok "$id" || fail 'Need an actual Apple UUID, never an invented placeholder.'
    LOG=$(mktemp -d "$RUN/status.XXXXXX")
    printf 'unconfirmed\n' > "$RUN/accepted-status.txt"
    rc=0
    capture info /usr/bin/xcrun notarytool info "$id" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --output-format json || rc=$?
    /usr/bin/perl "$HERE/bounded-run.pl" --observe-info "$LOG/info" "$(lower "$id")" "$ARTIFACT"
    [ "$rc" -eq 0 ] || fail 'Info command incomplete; retain observation and original command receipt.'
    cp "$LOG/info.out" "$LOG/info.json"
    [ "$(lower "$(plist_value "$LOG/info.json" id)")" = "$(lower "$id")" ]
    [ "$(plist_value "$LOG/info.json" name)" = "$ARTIFACT" ] || fail 'Apple submission name mismatch; do not adopt this ID.'
    printf 'Apple status: %s\n' "$(plist_value "$LOG/info.json" status)"
    capture log /usr/bin/xcrun notarytool log "$id" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN"
    /bin/cp "$LOG/log.out" "$LOG/log.json"
    # Recovery: full real history + info/name + log/jobId/hash BEFORE adoption.
    if [ "$#" -eq 3 ]; then
        capture history /usr/bin/xcrun notarytool history --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --output-format json
        /usr/bin/perl "$HERE/bounded-run.pl" --recover "$LOG/history.out" "$LOG/info.json" "$LOG/log.json" "$id" "$ARTIFACT" "$SUBSHA"
        /usr/bin/perl "$HERE/bounded-run.pl" --save "$RUN/submission/id.txt" "$(lower "$id")"
    fi
    check_accepted "$LOG" "$id"
    printf '%s\n' "${LOG##*/}" > "$RUN/accepted-status.txt"
    printf 'Accepted for exact SHA256 %s. Read the FULL log (including warnings):\n%s/log.json\nLog SHA256: %s\n' "$SUBSHA" "$LOG" "$(sha "$LOG/log.json")"
    ;;
finalize)
    load_run "$1"; check_submission
    snap=$(cat "$RUN/accepted-status.txt")
    [[ $snap =~ ^status\.[A-Za-z0-9]{6}$ ]] || fail 'Bad status directory.'
    snap="$RUN/$snap"; private_dir "$snap"
    id=$(cat "$RUN/submission/id.txt"); uuid_ok "$id" || fail 'Bad saved ID.'
    check_accepted "$snap" "$id"
    [ "$3" = "$(sha "$snap/log.json")" ] || fail 'Pass the exact log hash only AFTER inspecting its full contents and warnings.'
    /usr/bin/perl "$HERE/bounded-run.pl" --review-log "$snap/log.json" "$id"

    mkdir "$RUN/final" || fail 'Finalization already exists/interrupted; preserve it and investigate.'
    LOG="$RUN/final"; check_signed; verify_code "$APP"
    # Staple another copy: the exact pre-stapling signed work app stays retained.
    capture copy-final /usr/bin/ditto "$APP" "$LOG/HappyRanch.app"
    manifest "$LOG/HappyRanch.app" > "$LOG/before-staple.txt"
    cmp "$RUN/signed-files.txt" "$LOG/before-staple.txt"
    capture staple /usr/bin/xcrun stapler staple "$LOG/HappyRanch.app"
    capture ticket /usr/bin/xcrun stapler validate "$LOG/HappyRanch.app"
    manifest "$LOG/HappyRanch.app" | awk '$NF != "./Contents/CodeResources"' > "$LOG/after-staple-nonticket.txt"
    awk '$NF != "./Contents/CodeResources"' "$LOG/before-staple.txt" > "$LOG/before-staple-nonticket.txt"
    cmp "$LOG/before-staple-nonticket.txt" "$LOG/after-staple-nonticket.txt" || fail 'Stapling changed bytes/modes beyond the app ticket.'
    verify_code "$LOG/HappyRanch.app"
    FINALZIP="$LOG/HappyRanch-1.0.1-1.0.1-arm64-signed-notarized-stapled.zip"
    capture archive /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$LOG/HappyRanch.app" "$FINALZIP"
    mkdir "$LOG/reextracted"
    capture reextract /usr/bin/ditto -x -k "$FINALZIP" "$LOG/reextracted"
    verify_code "$LOG/reextracted/HappyRanch.app"
    capture reextracted-ticket /usr/bin/xcrun stapler validate "$LOG/reextracted/HappyRanch.app"
    capture syspolicy /usr/bin/syspolicy_check distribution "$LOG/reextracted/HappyRanch.app"
    capture gatekeeper /usr/sbin/spctl --assess --type execute --verbose=4 "$LOG/reextracted/HappyRanch.app"
    manifest "$LOG/HappyRanch.app" > "$LOG/stapled-files.txt"
    manifest "$LOG/reextracted/HappyRanch.app" > "$LOG/final-files.txt"
    cmp "$LOG/stapled-files.txt" "$LOG/final-files.txt"
    sha "$FINALZIP" > "$LOG/final.sha256"
    stat -f %z "$FINALZIP" > "$LOG/final.size"
    printf 'ID %s\nsubmission-sha256 %s\nreviewed-log-sha256 %s\n' "$id" "$SUBSHA" "$3" > "$LOG/association.txt"
    /usr/bin/perl "$HERE/bounded-run.pl" --save "$LOG/ready" 'Final verified; parent has not published completion.'
    printf 'FINAL ZIP: %s\nNEW size: %s\nNEW SHA256: %s\n' "$FINALZIP" "$(cat "$LOG/final.size")" "$(cat "$LOG/final.sha256")"
    ;;
test)
    load_run "$1"
    owned_file "$RUN/final/complete"
    [ "$(plist_value "$RUN/final/complete" source)" = "$HR_SOURCE" ]
    [ "$(plist_value "$RUN/final/complete" action)" = finalize ]
    FINALZIP="$RUN/final/HappyRanch-1.0.1-1.0.1-arm64-signed-notarized-stapled.zip"
    [ "$(sha "$FINALZIP")" = "$(cat "$RUN/final/final.sha256")" ] || fail 'Final ZIP changed.'
    [ "$(stat -f %z "$FINALZIP")" = "$(cat "$RUN/final/final.size")" ] || fail 'Final size changed.'
    LOG=$(mktemp -d "$RUN/test.XXXXXX")
    mkdir "$LOG/reextracted"
    capture reextract /usr/bin/ditto -x -k "$FINALZIP" "$LOG/reextracted"
    manifest "$LOG/reextracted/HappyRanch.app" > "$LOG/final.actual"
    cmp "$RUN/final/final-files.txt" "$LOG/final.actual"
    verify_code "$LOG/reextracted/HappyRanch.app"
    capture ticket /usr/bin/xcrun stapler validate "$LOG/reextracted/HappyRanch.app"
    capture syspolicy /usr/bin/syspolicy_check distribution "$LOG/reextracted/HappyRanch.app"
    capture gatekeeper /usr/sbin/spctl --assess --type execute --verbose=4 "$LOG/reextracted/HappyRanch.app"
    printf 'Native command checks complete. Finder/GUI/install and clean-download venue remain separate README steps; macOS14 NOT RUN.\n'
    ;;
esac
