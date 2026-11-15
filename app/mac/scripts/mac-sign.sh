#!/bin/bash
# Owner-run only; macOS stock tools, Bash 3.2. No app code is executed here.
set +x
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
umask 077
BUNDLE=ai.happyranch.HappyRanchApp
fail() { printf 'STOP: %s\n' "$*" >&2; exit 1; }
# Public per-build data only. Never source/eval a pin file or accept ambient pins.
load_build_pins() {
    local parsed line key value
    parsed=$(/usr/bin/perl -MMIME::Base64=decode_base64,encode_base64 -e '
        use strict; use warnings; use Fcntl qw(:mode);
        sub refuse { die "STOP: Invalid build pins: $_[0]\n" }
        my $p=shift; defined($p) && $p =~ m{\A/[^\x00-\x1f\x7f]*\z} && $p !~ m{//|/(?:\.|\.\.)(?:/|$)|/$} or refuse("plain absolute path");
        my $q=""; for my $part (split "/",substr($p,1)) {
            $q.="/$part"; my @s=lstat($q); @s && !S_ISLNK($s[2]) or refuse("missing or symlink path");
        }
        my @s=lstat($p);
        S_ISREG($s[2]) && $s[3]==1 && $s[4]==$< && !($s[2]&0077) && $s[7]<=4096 or refuse("private owned single-link file <=4096 bytes");
        open(my $f,"<",$p) or refuse("read"); local $/; my $raw=<$f>; close($f) or refuse("close");
        my @keys=qw(VERSION BUILD INPUT_SIZE INPUT_SHA LAYOUT_SHA SOURCE SOURCE_TREE SUPUBLIC_ED_KEY);
        my @lines=split /\n/,$raw,-1; @lines==9 && pop(@lines) eq "" or refuse("exactly eight LF-terminated lines");
        for my $i (0..$#keys) {
            $lines[$i] =~ /\A\Q$keys[$i]\E=([^\s\x00-\x1f\x7f]+)\z/ or refuse("field/order/whitespace");
            my $v=$1;
            if ($i<2) { $v =~ /\A(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\z/ or refuse("three-component version/build"); }
            elsif ($i==2) { $v =~ /\A[1-9][0-9]{0,11}\z/ or refuse("ZIP size"); }
            elsif ($i<5) { $v =~ /\A[0-9a-f]{64}\z/ or refuse("SHA256"); }
            elsif ($i<7) { $v =~ /\A[0-9a-f]{40}\z/ or refuse("git SHA"); }
            else { length(decode_base64($v))==32 && encode_base64(decode_base64($v),"") eq $v or refuse("canonical 32-byte public key"); }
        }
        print $raw;
    ' "$1") || fail 'Build pin validation failed.'
    while IFS= read -r line; do
        key=${line%%=*}; value=${line#*=}
        case "$key" in
            VERSION) VERSION=$value ;; BUILD) BUILD=$value ;;
            INPUT_SIZE) INPUT_SIZE=$value ;; INPUT_SHA) INPUT_SHA=$value ;;
            LAYOUT_SHA) LAYOUT_SHA=$value ;; SOURCE) SOURCE=$value ;;
            SOURCE_TREE) SOURCE_TREE=$value ;; SUPUBLIC_ED_KEY) SUPUBLIC_ED_KEY=$value ;;
        esac
    done <<< "$parsed"
    BUILD_PINS=$1
}
public_key_ok() {
    /usr/bin/perl -MMIME::Base64=decode_base64,encode_base64 -e '
        my $v=shift; exit !(length(decode_base64($v))==32 && encode_base64(decode_base64($v),"") eq $v);
    ' "$1"
}
# Mirror sparkle_bundle.py LINKS exactly, for both ZIP and filesystem members.
# ZIP metadata is read before extraction; link contents come from stock unzip.
check_members() {
    /usr/bin/perl -e '
        use strict; use warnings; use Fcntl qw(:mode); use File::Find; use Cwd qw(abs_path);
        sub refuse { die "STOP: Bundle members refused: $_[0]\n" }
        my ($mode,$path)=@ARGV;
        my $fw="Contents/Frameworks/Sparkle.framework";
        my %links=("$fw/Versions/Current"=>"B",map {("$fw/$_"=>"Versions/Current/$_")} qw(Sparkle Resources Headers PrivateHeaders Modules Autoupdate Updater.app));
        my (%seen,%names);
        sub member {
            my ($rel,$typ,$target)=@_;
            $rel !~ /[\x00-\x1f\x7f\\]/ && $rel !~ m{\A/|(?:\A|/)(?:\.|\.\.)(?:/|\z)|//} or refuse("unsafe member path");
            !$names{$rel}++ or refuse("duplicate member");
            for my $link (keys %links) { index($rel,"$link/")!=0 or refuse("member below symlink"); }
            $rel !~ m{(?:\A|/)(?:XPCServices|[^/]*\.xpc|Installer|Downloader)(?:/|\z)} or refuse("XPC/Installer/Downloader present");
            if ($typ==0120000) {
                exists($links{$rel}) && $links{$rel} eq $target or refuse("symlink $rel");
                $seen{$rel}=1;
            } else {
                !exists($links{$rel}) or refuse("required link replaced by regular member: $rel");
                $typ==0040000 || $typ==0100000 or refuse("special member");
            }
        }
        if ($mode eq "tree") {
            -d $path && !-l $path or refuse("app root");
            find({no_chdir=>1,wanted=>sub {
                return if $File::Find::name eq $path;
                my $rel=substr($File::Find::name,length($path)+1);
                my @s=lstat($File::Find::name); @s or refuse("lstat");
                my $typ=$s[2]&0170000; my $target=$typ==0120000 ? readlink($File::Find::name) : "";
                member($rel,$typ,$target);
                if ($typ==0120000) {
                    my $resolved=abs_path($File::Find::name); my $root=abs_path("$path/$fw");
                    defined($resolved) && defined($root) && index($resolved,"$root/")==0 or refuse("escaping/dangling symlink");
                }
            }},$path);
        } elsif ($mode eq "zip") {
            open(my $f,"<",$path) or refuse("open ZIP"); binmode($f);
            my $size=-s $f; my $tail=$size<65557 ? $size : 65557;
            seek($f,-$tail,2) or refuse("ZIP seek"); read($f,my $end,$tail)==$tail or refuse("ZIP tail");
            my $at=rindex($end,"PK\x05\x06"); $at>=0 or refuse("ZIP end record");
            my ($disk,$cd_disk,$n_disk,$n,$bytes,$offset,$comment)=unpack("vvvvVVv",substr($end,$at+4,18));
            !$disk && !$cd_disk && $n_disk==$n && $n<65535 && $at+22+$comment==$tail && $offset+$bytes==$size-$tail+$at or refuse("ZIP directory bounds/ZIP64");
            seek($f,$offset,0) or refuse("ZIP directory seek");
            for (1..$n) {
                my $h; read($f,$h,46)==46 && substr($h,0,4) eq "PK\x01\x02" or refuse("ZIP header");
                my ($flags,$len,$extra,$comm,$external)= (unpack("v",substr($h,8,2)),unpack("vvv",substr($h,28,6)),unpack("V",substr($h,38,4)));
                !($flags&1) && $len>0 or refuse("encrypted/empty member");
                read($f,my $name,$len)==$len or refuse("ZIP name"); seek($f,$extra+$comm,1) or refuse("ZIP extra");
                my $typ=($external>>16)&0170000; $name =~ s{/$}{};
                my $rel;
                if ($name eq "HappyRanch.app") { $typ==0040000 or refuse("ZIP app root"); next; }
                elsif ($name =~ s{\AHappyRanch\.app/}{}) { $rel=$name; }
                elsif ($name eq "__MACOSX" || $name =~ m{\A__MACOSX/}) { $rel=$name; }
                else { refuse("ZIP outside app/resources"); }
                my $target="";
                if ($typ==0120000) {
                    # Only exact allowlisted names are passed to unzip (no glob).
                    exists($links{$rel}) or refuse("extra ZIP symlink");
                    open(my $u,"-|","/usr/bin/unzip","-p",$path,"HappyRanch.app/$rel") or refuse("read ZIP link");
                    local $/; $target=<$u>; close($u) or refuse("ZIP link read failed");
                }
                member($rel,$typ,$target);
            }
            tell($f)==$offset+$bytes or refuse("ZIP directory length"); close($f) or refuse("ZIP close");
        } else { refuse("check mode"); }
        keys(%seen)==keys(%links) or refuse("exact eight-link set required");
    ' "$1" "$2" || fail 'Bundle member validation failed.'
}
executable_rel() {
    case "$1" in
        bundle) printf '%s/Contents/MacOS/Updater\n' "$2" ;;
        framework) printf '%s/Versions/B/Sparkle\n' "$2" ;;
        *) printf '%s\n' "$2" ;;
    esac
}
# File-only configuration: ambient signing values never override this file.
# Keep this loader identical in the local and Jenkins signing scripts.
load_signing_config() {
    local config=${HAPPYRANCH_MAC_SIGNING_ENV:-"$HOME/.config/happyranch/mac-signing.env"}
    local setup='Follow the private signing configuration setup in docs/runbooks/mac-release-handbook.md.'
    local mode line key value seen='' number=0
    [ -f "$config" ] && [ ! -L "$config" ] && [ -O "$config" ] && [ -r "$config" ] || fail "HAPPYRANCH_MAC_SIGNING_ENV: need a readable regular file owned by the current user (no symlink). $setup"
    mode=$(stat -f '%Lp' "$config") || fail "HAPPYRANCH_MAC_SIGNING_ENV: cannot inspect file mode. $setup"
    [[ $mode =~ ^[0-7]{3,4}$ ]] || fail "HAPPYRANCH_MAC_SIGNING_ENV: invalid file mode. $setup"
    [ "$((8#$mode & ~0600))" -eq 0 ] || fail "HAPPYRANCH_MAC_SIGNING_ENV: require mode 600 or stricter; run chmod 600 on the private file. $setup"
    tr -d '\000' < "$config" | cmp -s "$config" - || fail "Bad NUL byte or unreadable HAPPYRANCH_MAC_SIGNING_ENV. $setup"
    IDENTITY=''; PUBLIC_NAME=''; TEAM=''; PROFILE=''
    while IFS= read -r line || [ -n "$line" ]; do
        number=$((number+1))
        # Trim spaces/tabs only; no expansions, escapes or inline comments.
        line="${line#"${line%%[!$' \t']*}"}"
        line="${line%"${line##*[!$' \t']}"}"
        case "$line" in ''|\#*) continue ;; esac
        case "$line" in *=*) ;; *) fail "HAPPYRANCH_MAC_SIGNING_ENV: expected KEY=VALUE at line $number. $setup" ;; esac
        key=${line%%=*}; value=${line#*=}
        key="${key%"${key##*[!$' \t']}"}"
        value="${value#"${value%%[!$' \t']*}"}"
        case "$key" in
            HAPPYRANCH_SIGN_IDENTITY_SHA1|HAPPYRANCH_SIGN_IDENTITY_NAME|HAPPYRANCH_SIGN_TEAM_ID|HAPPYRANCH_NOTARY_PROFILE) ;;
            *) fail "Unknown signing key $key at line $number. $setup" ;;
        esac
        case " $seen " in *" $key "*) fail "Duplicate signing key $key. $setup" ;; esac
        seen="$seen $key"
        case "$value" in
            \"*) [ "${#value}" -ge 2 ] && [ "${value%\"}" != "$value" ] || fail "Bad quotes for $key. $setup"; value=${value#\"}; value=${value%\"} ;;
            \'*) [ "${#value}" -ge 2 ] && [ "${value%\'}" != "$value" ] || fail "Bad quotes for $key. $setup"; value=${value#\'}; value=${value%\'} ;;
            *\"|*\') fail "Bad quotes for $key. $setup" ;;
        esac
        [[ ! $value =~ [[:cntrl:]] ]] || fail "Bad control character for $key. $setup"
        case "$key" in
            HAPPYRANCH_SIGN_IDENTITY_SHA1) IDENTITY=$value ;;
            HAPPYRANCH_SIGN_IDENTITY_NAME) PUBLIC_NAME=$value ;;
            HAPPYRANCH_SIGN_TEAM_ID) TEAM=$value ;;
            HAPPYRANCH_NOTARY_PROFILE) PROFILE=$value ;;
        esac
    done < "$config"
    [ -n "$IDENTITY" ] || fail "Missing or empty HAPPYRANCH_SIGN_IDENTITY_SHA1. $setup"
    [ -n "$PUBLIC_NAME" ] || fail "Missing or empty HAPPYRANCH_SIGN_IDENTITY_NAME. $setup"
    [ -n "$TEAM" ] || fail "Missing or empty HAPPYRANCH_SIGN_TEAM_ID. $setup"
    [ -n "$PROFILE" ] || fail "Missing or empty HAPPYRANCH_NOTARY_PROFILE. $setup"
    [[ $IDENTITY =~ ^[0-9A-Fa-f]{40}$ ]] || fail "Bad HAPPYRANCH_SIGN_IDENTITY_SHA1: need 40 hex characters. $setup"
    IDENTITY=$(printf '%s' "$IDENTITY" | tr '[:lower:]' '[:upper:]')
    [[ $TEAM =~ ^[A-Z0-9]{10}$ ]] || fail "Bad HAPPYRANCH_SIGN_TEAM_ID: need 10 uppercase alphanumerics. $setup"
    case "$PUBLIC_NAME" in *"($TEAM)") ;; *) fail "Bad HAPPYRANCH_SIGN_IDENTITY_NAME: must end with the configured HAPPYRANCH_SIGN_TEAM_ID in parentheses. $setup" ;; esac
}
usage() {
    cat <<'EOF'
Owner-run commands (absolute paths; run stages separately):
  /bin/bash mac-sign.sh pins BUILD.pins
  /bin/bash mac-sign.sh preflight BUILD.pins ORIGINAL.zip
  /bin/bash mac-sign.sh sign BUILD.pins ORIGINAL.zip NEW_PRIVATE_RUN_DIRECTORY
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
    pins|submit|test) [ "$#" -eq 1 ] || { usage >&2; exit 2; } ;;
    preflight) [ "$#" -eq 2 ] || { usage >&2; exit 2; } ;;
    sign) [ "$#" -eq 3 ] || { usage >&2; exit 2; } ;;
    status) [ "$#" -eq 1 ] || { [ "$#" -eq 3 ] && [ "$2" = --recover-id ]; } || { usage >&2; exit 2; } ;;
    finalize) [ "$#" -eq 3 ] && [ "$2" = --log-reviewed ] || { usage >&2; exit 2; } ;;
    *) usage >&2; exit 2 ;;
esac
[ "$STAGE" != pins ] || { load_build_pins "$1"; printf 'VERSION=%s\nBUILD=%s\nINPUT_SIZE=%s\nINPUT_SHA=%s\nLAYOUT_SHA=%s\nSOURCE=%s\nSOURCE_TREE=%s\nSUPUBLIC_ED_KEY=%s\n' "$VERSION" "$BUILD" "$INPUT_SIZE" "$INPUT_SHA" "$LAYOUT_SHA" "$SOURCE" "$SOURCE_TREE" "$SUPUBLIC_ED_KEY"; exit 0; }
load_signing_config
[ "$(uname -s)" = Darwin ] || fail 'macOS required; no work performed.'
[ "$(uname -m)" = arm64 ] || fail 'Native arm64 shell required; do not run through Rosetta.'
[ "$(id -u)" -ne 0 ] || fail 'Use your own login account, without sudo.'
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
HERE=$(cd "$(dirname "$0")" && pwd -P)
INVENTORY="$HERE/code-inventory.tsv"
sha() { /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
[ "$(sha "$INVENTORY")" = ccd3473987f0f425fe1e13261f8feb71fd43651f275221a04613c2aa59415d11 ] || fail 'Package inventory mismatch.'
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
    local f=$1 key
    [ "$(plist_value "$f" CFBundleIdentifier)" = "$BUNDLE" ]
    [ "$(plist_value "$f" CFBundleExecutable)" = HappyRanch ]
    [ "$(plist_value "$f" CFBundlePackageType)" = APPL ]
    [ "$(plist_value "$f" CFBundleShortVersionString)" = "$VERSION" ]
    [ "$(plist_value "$f" CFBundleVersion)" = "$BUILD" ]
    [ "$(plist_value "$f" LSMinimumSystemVersion)" = 14.0 ]
    [ "$(plist_value "$f" GitCommitSHA)" = "$SOURCE" ]
    [ "$(plist_value "$f" GitTreeSHA)" = "$SOURCE_TREE" ]
    [ "$(plist_value "$f" ReleaseArchitecture)" = arm64 ]
    key=$(plist_value "$f" SUPublicEDKey) || fail "Missing SUPublicEDKey."
    public_key_ok "$key" && [ "$key" = "$SUPUBLIC_ED_KEY" ] || fail "Invalid/mismatched SUPublicEDKey."
    [ "$(plist_value "$f" SUFeedURL)" = https://app.happyranch.ai/mac/appcast.xml ] || fail "Wrong Sparkle feed."
}
# Read every byte (also preserving upstream unzip/pipefail errors), retaining
# only the header and arm64 slice header. Sparkle ships universal Mach-O files.
check_macho() {
    /usr/bin/perl -e '
        use strict; use warnings;
        sub refuse { die "STOP: Mach-O architecture/type mismatch.\n" }
        sub take { my $n=shift; my $v=""; while(length($v)<$n) { my $r=read(STDIN,my $b,$n-length($v)); defined($r) && $r>0 or refuse(); $v.=$b; } return $v; }
        my ($typ,$kind)=@ARGV; my $head=take(4); my ($offset,$pos)=(0,4);
        if ($head eq "\xcf\xfa\xed\xfe") { $head.=take(12); $pos=16; }
        elsif (($kind eq "bundle" || $kind eq "framework" || $kind eq "sparkle") && ($head eq "\xca\xfe\xba\xbe" || $head eq "\xca\xfe\xba\xbf")) {
            my $wide=$head eq "\xca\xfe\xba\xbf"; my $n=unpack("N",take(4)); $n>0 && $n<=32 or refuse();
            my $table=take($n*($wide?32:20)); $pos=8+length($table); my @arm;
            for my $i (0..$n-1) {
                my $r=substr($table,$i*($wide?32:20),$wide?32:20);
                next unless unpack("N",$r)==0x100000c;
                my $off=$wide ? unpack("Q>",substr($r,8,8)) : unpack("N",substr($r,8,4));
                my $size=$wide ? unpack("Q>",substr($r,16,8)) : unpack("N",substr($r,12,4));
                $off>=$pos && $size>=32 or refuse(); push @arm,$off;
            }
            @arm==1 or refuse(); $offset=$arm[0]; $head="";
        } else { refuse(); }
        while (1) {
            my $n=read(STDIN,my $b,65536); defined($n) or refuse(); last unless $n;
            if (length($head)<16 && $pos+$n>$offset) {
                my $start=$offset>$pos ? $offset-$pos : 0;
                $head.=substr($b,$start,16-length($head));
            }
            $pos+=$n;
        }
        length($head)==16 && $head eq pack("V4",0xfeedfacf,0x100000c,0,$typ) or refuse();
    ' "$1" "$2" || fail 'Bundle member validation failed.'
}
preflight() {
    local zip=$1 kind typ ident rel got
    owned_file "$zip"
    [ "$(stat -f %z "$zip")" = "$INPUT_SIZE" ] || fail 'Unsigned ZIP size mismatch.'
    [ "$(sha "$zip")" = "$INPUT_SHA" ] || fail 'Unsigned ZIP hash mismatch.'
    /usr/bin/unzip -tq "$zip"
    got=$(/usr/bin/unzip -Z1 "$zip" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
    [ "$got" = "$LAYOUT_SHA" ] || fail 'Exact ZIP member layout mismatch.'
    check_members zip "$zip"
    # Hash authenticates all member bytes/modes; independently constrain links/code.
    while IFS=$'\t' read -r kind typ ident rel; do
        /usr/bin/unzip -p "$zip" "HappyRanch.app/$(executable_rel "$kind" "$rel")" | check_macho "$typ" "$kind"
        printf '%s arm64 Mach-O type%s\n' "$rel" "$typ"
    done < "$INVENTORY"
    # plutil reads the original member on stdin; no extraction or temp file.
    local key expected actual
    for key in CFBundleIdentifier CFBundleExecutable CFBundlePackageType CFBundleShortVersionString CFBundleVersion LSMinimumSystemVersion GitCommitSHA GitTreeSHA ReleaseArchitecture SUPublicEDKey SUFeedURL; do
        case "$key" in
            CFBundleIdentifier) expected=$BUNDLE ;; CFBundleExecutable) expected=HappyRanch ;;
            CFBundlePackageType) expected=APPL ;; CFBundleShortVersionString) expected=$VERSION ;; CFBundleVersion) expected=$BUILD ;;
            LSMinimumSystemVersion) expected=14.0 ;; GitCommitSHA) expected=$SOURCE ;;
            GitTreeSHA) expected=$SOURCE_TREE ;; ReleaseArchitecture) expected=arm64 ;;
            SUPublicEDKey) expected=$SUPUBLIC_ED_KEY ;; SUFeedURL) expected=https://app.happyranch.ai/mac/appcast.xml ;;
        esac
        actual=$(/usr/bin/unzip -p "$zip" HappyRanch.app/Contents/Info.plist | /usr/bin/plutil -extract "$key" raw -o - -) || fail "Missing archived plist field: $key"
        if [ "$key" = SUPublicEDKey ]; then public_key_ok "$actual" || fail "Invalid archived SUPublicEDKey."; fi
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
    if "$@" > "$LOG/$label.out" 2> "$LOG/$label.err"; then rc=0; else rc=$?; fi
    printf '%s\n' "$rc" > "$LOG/$label.exit"
    printf '%s exit=%s (details: %s)\n' "$label" "$rc" "$LOG"
    return "$rc"
}
# Keep the reviewed artifact's inventory/pins intact. New bundle copies
# include the sidecar before outer signing, using the same exec branch/identity.
# Membership comes from the retained baseline, so removal/addition cannot silently
# change which bytes signing is allowed to transform.
code_inventory() {
    local kind typ ident rel
    while IFS=$'\t' read -r kind typ ident rel; do
        if [ "$kind" = main ] && [ -f "$RUN/baseline/HappyRanch.app/Contents/Helpers/happyranch-tsnet-sidecar" ]; then
            printf 'exec\t2\tai.happyranch.HappyRanchApp.nested.11\tContents/Helpers/happyranch-tsnet-sidecar\n'
        fi
        printf '%s\t%s\t%s\t%s\n' "$kind" "$typ" "$ident" "$rel"
    done < "$INVENTORY"
}
# All file bytes and modes; data mode permits inventoried code and exact
# outer signing/ticket paths to differ. All notices, README and plist stay exact.
manifest() (
    cd "$1"
    local mode=${2:-all} p rel
    check_members tree .
    find . -print | sort | while IFS= read -r p; do
        rel=${p#./}
        if [ "$mode" = data ]; then
            case "$rel" in Contents/_CodeSignature|Contents/_CodeSignature/CodeResources|Contents/CodeResources|Contents/Frameworks/Sparkle.framework/Versions/B/_CodeSignature|Contents/Frameworks/Sparkle.framework/Versions/B/_CodeSignature/CodeResources|Contents/Frameworks/Sparkle.framework/Versions/B/Updater.app/Contents/_CodeSignature|Contents/Frameworks/Sparkle.framework/Versions/B/Updater.app/Contents/_CodeSignature/CodeResources) continue ;; esac
            if /usr/bin/awk -F '\t' -v p="$rel" '$4==p || ($1=="bundle" && $4"/Contents/MacOS/Updater"==p) || ($1=="framework" && $4"/Versions/B/Sparkle"==p) {found=1} END {exit !found}' <(code_inventory); then continue; fi
        fi
        if [ -L "$p" ]; then printf 'L %s %s\n' "$(readlink "$p")" "$p"
        elif [ -d "$p" ]; then printf 'D %s %s\n' "$(stat -f %Lp "$p")" "$p"
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
    check_members tree "$app"
    check_plist "$app/Contents/Info.plist"
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$app/$(executable_rel "$kind" "$rel")"
        [ -f "$target" ] && [ -x "$target" ] || fail "Missing code: $rel"
        check_macho "$typ" "$kind" < "$target"
        case "$kind" in main) target=$app ;; bundle|framework) target="$app/$rel" ;; esac
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
    done < <(code_inventory)
    capture verify-outer /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
    check_data "$app"
}
load_run() {
    RUN=$1; private_dir "$RUN"
    owned_file "$RUN/package.sha256"
    [ "$(cat "$RUN/package.sha256")" = "$(sha "$HERE/mac-sign.sh") $(sha "$INVENTORY")" ] || fail 'Use the identical script and inventory that created this run.'
    owned_file "$RUN/build.pins"; owned_file "$RUN/pins.sha256"
    [ "$(sha "$RUN/build.pins")" = "$(cat "$RUN/pins.sha256")" ] || fail "Retained build pins changed."
    load_build_pins "$RUN/build.pins"
    owned_file "$RUN/sign.complete"
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
    local prefix="HappyRanch-$VERSION-$BUILD-submission-" suffix
    suffix=${ARTIFACT#"$prefix"}; suffix=${suffix%.zip}
    [ "$ARTIFACT" = "$prefix$suffix.zip" ] && uuid_ok "$suffix" || fail 'Bad submission artifact name.'
    SUBZIP="$RUN/submission/$ARTIFACT"
    owned_file "$SUBZIP"
    SUBSHA=$(cat "$RUN/submission/artifact.sha256")
    [ "$(sha "$SUBZIP")" = "$SUBSHA" ] || fail 'Submission ZIP hash changed.'
    [ "$(stat -f %z "$SUBZIP")" = "$(cat "$RUN/submission/artifact.size")" ] || fail 'Submission ZIP size changed.'
}
check_accepted() {
    local snap=$1 id=$2
    [ "$(lower "$(plist_value "$snap/info.json" id)")" = "$(lower "$id")" ]
    [ "$(plist_value "$snap/info.json" name)" = "$ARTIFACT" ]
    [ "$(plist_value "$snap/info.json" status)" = Accepted ] || fail 'Apple info is not Accepted.'
    [ "$(lower "$(plist_value "$snap/log.json" jobId)")" = "$(lower "$id")" ]
    [ "$(plist_value "$snap/log.json" status)" = Accepted ] || fail 'Apple log is not Accepted.'
    [ "$(lower "$(plist_value "$snap/log.json" sha256)")" = "$SUBSHA" ] || fail 'Apple log does not match submitted ZIP hash.'
}
case "$STAGE" in
preflight) load_build_pins "$1"; preflight "$2" ;;
sign)
    load_build_pins "$1"
    preflight "$2"
    RUN=$3; plain_path "$RUN"; private_dir "$(dirname "$RUN")"
    [ ! -e "$RUN" ] || fail 'Run directory already exists; preserve it, do not reuse.'
    mkdir "$RUN"
    LOG="$RUN/sign-logs"; mkdir "$LOG" "$RUN/baseline" "$RUN/work"
    printf '%s %s\n' "$(sha "$HERE/mac-sign.sh")" "$(sha "$INVENTORY")" > "$RUN/package.sha256"
    /bin/cp -p "$BUILD_PINS" "$RUN/build.pins"
    sha "$RUN/build.pins" > "$RUN/pins.sha256"
    load_build_pins "$RUN/build.pins"
    capture retain-unsigned /bin/cp -p "$2" "$RUN/original.zip"
    [ "$(sha "$RUN/original.zip")" = "$INPUT_SHA" ]
    capture extract-baseline /usr/bin/ditto -x -k "$RUN/original.zip" "$RUN/baseline"
    manifest "$RUN/baseline/HappyRanch.app" | shasum -a 256 | awk '{print $1}' > "$RUN/baseline.sha256"
    manifest "$RUN/baseline/HappyRanch.app" data > "$RUN/baseline-data.txt"
    capture copy-app /usr/bin/ditto "$RUN/baseline/HappyRanch.app" "$RUN/work/HappyRanch.app"
    manifest "$RUN/baseline/HappyRanch.app" > "$LOG/baseline-files.txt"
    manifest "$RUN/work/HappyRanch.app" > "$LOG/copy-files.txt"
    cmp "$LOG/baseline-files.txt" "$LOG/copy-files.txt"
    APP="$RUN/work/HappyRanch.app"; i=0
    check_plist "$APP/Contents/Info.plist"
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$APP/$rel"
        executable="$APP/$(executable_rel "$kind" "$rel")"
        [ -f "$executable" ] && [ -x "$executable" ] || fail "Missing executable: $rel"
        check_macho "$typ" "$kind" < "$executable"
        capture "original-metadata-$i" /usr/bin/codesign -d --verbose=4 "$target"
        capture "original-entitlements-$i" /usr/bin/codesign -d --entitlements :- "$target"
        if grep -q '<key>' "$LOG/original-entitlements-$i.out"; then fail 'Unexpected original entitlements; return for review.'; fi
        capture "dependencies-$i" /usr/bin/otool -L "$APP/$(executable_rel "$kind" "$rel")"
    done < <(code_inventory)
    i=0
    while IFS=$'\t' read -r kind typ ident rel; do
        i=$((i+1)); target="$APP/$rel"
        case "$kind" in
            lib) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --identifier "$ident" "$target" ;;
            exec|sparkle|bundle|framework) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --options runtime --identifier "$ident" "$target" ;;
            main) capture "sign-$i" /usr/bin/codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp --options runtime "$APP" ;;
        esac
    done < <(code_inventory)
    verify_code "$APP"
    manifest "$APP" > "$RUN/signed-files.txt"
    printf 'Signed/verified only; no Apple submission.\n' > "$RUN/sign.complete"
    printf 'Signed run: %s\nNext, separately: submit RUN_DIRECTORY\n' "$RUN"
    ;;
submit)
    load_run "$1"
    # mkdir is also the durable one-attempt gate; never remove/reuse it.
    mkdir "$RUN/submission" || fail 'Submission already prepared/attempted. Use status/recovery; NEVER re-upload automatically.'
    LOG="$RUN/submission"; check_signed; verify_code "$APP"
    ARTIFACT="HappyRanch-$VERSION-$BUILD-submission-$(/usr/bin/uuidgen).zip"
    printf '%s\n' "$ARTIFACT" > "$LOG/artifact-name.txt"
    SUBZIP="$LOG/$ARTIFACT"
    capture archive /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$SUBZIP"
    check_members zip "$SUBZIP"
    mkdir "$LOG/reextracted"
    capture archive-extract /usr/bin/ditto -x -k "$SUBZIP" "$LOG/reextracted"
    manifest "$LOG/reextracted/HappyRanch.app" > "$LOG/archive-files.txt"
    cmp "$RUN/signed-files.txt" "$LOG/archive-files.txt"
    sha "$SUBZIP" > "$LOG/artifact.sha256"
    stat -f %z "$SUBZIP" > "$LOG/artifact.size"
    printf 'Artifact SHA256 %s; size %s\n' "$(cat "$LOG/artifact.sha256")" "$(cat "$LOG/artifact.size")"
    date -u '+attempt-started %Y-%m-%dT%H:%M:%SZ; never automatically repeat' > "$LOG/ATTEMPTED"
    /bin/sync
    rc=0
    capture submit /usr/bin/xcrun notarytool submit "$SUBZIP" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --output-format json || rc=$?
    # Even a failed command may have returned a genuine ID. Preserve it first.
    id=$(/usr/bin/plutil -extract id raw -o - "$LOG/submit.out" 2>/dev/null) || id=''
    if uuid_ok "$id"; then printf '%s\n' "$id" > "$LOG/id.txt"; fi
    /bin/sync
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
    capture info /usr/bin/xcrun notarytool info "$id" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --output-format json
    cp "$LOG/info.out" "$LOG/info.json"
    [ "$(lower "$(plist_value "$LOG/info.json" id)")" = "$(lower "$id")" ]
    [ "$(plist_value "$LOG/info.json" name)" = "$ARTIFACT" ] || fail 'Apple submission name mismatch; do not adopt this ID.'
    if [ "$#" -eq 3 ]; then printf '%s\n' "$id" > "$RUN/submission/id.txt"; /bin/sync; fi
    printf 'Apple status: %s\n' "$(plist_value "$LOG/info.json" status)"
    capture log /usr/bin/xcrun notarytool log "$id" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" "$LOG/log.json"
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
    FINALZIP="$LOG/HappyRanch-$VERSION-$BUILD-arm64-signed-notarized-stapled.zip"
    capture archive /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$LOG/HappyRanch.app" "$FINALZIP"
    check_members zip "$FINALZIP"
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
    printf 'Final verification complete; GUI/install NOT RUN.\n' > "$LOG/complete"
    printf 'FINAL ZIP: %s\nNEW size: %s\nNEW SHA256: %s\n' "$FINALZIP" "$(cat "$LOG/final.size")" "$(cat "$LOG/final.sha256")"
    ;;
test)
    load_run "$1"
    owned_file "$RUN/final/complete"
    FINALZIP="$RUN/final/HappyRanch-$VERSION-$BUILD-arm64-signed-notarized-stapled.zip"
    [ "$(sha "$FINALZIP")" = "$(cat "$RUN/final/final.sha256")" ] || fail 'Final ZIP changed.'
    check_members zip "$FINALZIP"
    LOG=$(mktemp -d "$RUN/test.XXXXXX")
    manifest "$RUN/final/reextracted/HappyRanch.app" > "$LOG/final.actual"
    cmp "$RUN/final/final-files.txt" "$LOG/final.actual"
    verify_code "$RUN/final/reextracted/HappyRanch.app"
    capture ticket /usr/bin/xcrun stapler validate "$RUN/final/reextracted/HappyRanch.app"
    capture syspolicy /usr/bin/syspolicy_check distribution "$RUN/final/reextracted/HappyRanch.app"
    capture gatekeeper /usr/sbin/spctl --assess --type execute --verbose=4 "$RUN/final/reextracted/HappyRanch.app"
    printf 'Native command checks complete. Finder/GUI/install and clean-download venue remain separate README steps; macOS14 NOT RUN.\n'
    ;;
esac
