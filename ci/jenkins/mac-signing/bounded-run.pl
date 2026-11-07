#!/usr/bin/perl
# Workload-local supervision, admission and durable publication. Stock Perl only.
use strict;
use warnings;
use Fcntl qw(:DEFAULT :flock :mode);
use POSIX qw(:sys_wait_h setpgid);
use Time::HiRes qw(time sleep clock_gettime CLOCK_MONOTONIC);
use IO::Select;
use IO::Handle;
use Digest::SHA;
use JSON::PP;
use File::Basename qw(dirname basename);
use Cwd qw(abs_path);

my $JSON = JSON::PP->new->canonical->utf8;
my $HERE = dirname(abs_path(__FILE__));
my @FILES = qw(run-stage.sh mac-sign.sh code-inventory.tsv bounded-run.pl keychain-state.swift owner-session.sh);
my $INPUT_SHA = '85b96655a1d072328872fa8797e16ad0087e0e05aa30b8dda2a8bc78b60ac243';
my $INPUT_SIZE = 49089611;
my $FINAL = 'HappyRanch-1.0.1-1.0.1-arm64-signed-notarized-stapled.zip';
my %BUDGET = (preflight=>180, sign=>900, submit=>600, status=>300, finalize=>900, test=>600);
my $UUID = qr/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;
sub need ($$) { die "REFUSED: $_[1]\n" unless $_[0] }
sub mono { clock_gettime(CLOCK_MONOTONIC) }
sub plain {
    my ($p) = @_;
    need(defined($p) && $p =~ m{\A/[^\x00-\x1f\x7f]*\z} && $p !~ m{//|/(?:\.|\.\.)(?:/|$)|/$}, 'plain absolute path');
    my $q = '';
    for my $part (split '/', substr($p, 1)) {
        $q .= "/$part";
        my @s = lstat($q);
        need(@s && !S_ISLNK($s[2]), 'missing or aliased path');
        need(!($s[2] & 0022), 'shared writable ancestry');
    }
    return $p;
}
sub regular {
    my ($p, $max) = @_;
    plain($p);
    my @s = lstat($p);
    need(S_ISREG($s[2]) && $s[3] == 1 && $s[4] == $< && !($s[2] & 0077), 'private owned single-link file');
    need(!defined($max) || $s[7] <= $max, 'file byte cap');
    return $s[7];
}
sub private_dir {
    my ($p) = @_;
    plain($p);
    my @s = stat($p);
    need(S_ISDIR($s[2]) && $s[4] == $< && ($s[2] & 0777) == 0700, 'private owned directory');
}
sub bytes {
    my ($p, $max) = @_;
    regular($p, $max);
    sysopen(my $f, $p, O_RDONLY | O_NOFOLLOW) or die "open read: $!\n";
    my $data = '';
    while (1) {
        my $n = sysread($f, my $chunk, 65536);
        need(defined($n), 'read'); last unless $n;
        $data .= $chunk; need(length($data) <= $max, 'read byte cap');
    }
    close($f) or die "close read: $!\n";
    return $data;
}
sub digest {
    my ($p) = @_;
    sysopen(my $f, $p, O_RDONLY | O_NOFOLLOW) or die "hash open: $!\n";
    my $d = Digest::SHA->new(256); $d->addfile($f);
    close($f) or die "hash close: $!\n";
    return $d->hexdigest;
}
sub flush_dir {
    sysopen(my $d, $_[0], O_RDONLY | O_NOFOLLOW) or die "directory open: $!\n";
    $d->sync or die "directory sync: $!\n";
    close($d) or die "directory close: $!\n";
}
sub save {
    my ($p, $data) = @_;
    private_dir(dirname($p));
    sysopen(my $f, $p, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600) or die "exclusive save: $!\n";
    my @owned=stat($f);
    my $ok=eval {
        print {$f} $data or die "save write: $!\n";
        $f->flush && $f->sync or die "save sync: $!\n";
        close($f) or die "save close: $!\n";
        flush_dir(dirname($p));
        1;
    };
    if (!$ok) {
        my $error=$@;
        close($f) if defined(fileno($f));
        my @now=lstat($p);
        # Only our newly opened partial inode may be invalidated. An existing
        # successful marker or a replacement is never deleted on failure.
        unlink($p) if @now && $now[0]==$owned[0] && $now[1]==$owned[1];
        die $error;
    }
}
sub read_json { $JSON->decode(bytes($_[0], $_[1] // 65536)) }
sub read_control {
    my $raw=bytes($_[0],65536); my $value=$JSON->decode($raw);
    # A canonical closed record also refuses duplicate keys, trailing data and
    # alternate encodings. Real Apple JSON is parsed separately, never rewritten.
    need($raw eq $JSON->encode($value)."\n",'canonical control record');
    return $value;
}
sub closed {
    my ($r, @keys) = @_;
    need(ref($r) eq 'HASH' && join(',',sort keys %$r) eq join(',',sort @keys), 'closed record keys');
}
sub copy_private {
    my ($from, $to, $max) = @_;
    regular($from, $max); private_dir(dirname($to));
    sysopen(my $in, $from, O_RDONLY | O_NOFOLLOW) or die "copy source: $!\n";
    sysopen(my $out, $to, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600) or die "copy destination: $!\n";
    my $total = 0;
    while (1) {
        my $n = sysread($in, my $chunk, 65536); need(defined($n), 'copy read'); last unless $n;
        $total += $n; need($total <= $max, 'copy byte cap');
        print {$out} $chunk or die "copy write: $!\n";
    }
    $out->flush && $out->sync or die "copy sync: $!\n";
    close($in) && close($out) or die "copy close: $!\n";
    flush_dir(dirname($to));
    need(digest($from) eq digest($to), 'copy digest');
}
sub associated {
    my ($info,$log,$id,$name,$sha) = @_;
    need($id =~ /\A$UUID\z/ && $name =~ /\AHappyRanch-1\.0\.1-submission-$UUID\.zip\z/ && $sha =~ /\A[0-9a-f]{64}\z/, 'submission identity syntax');
    my $i=read_json($info,1048576); my $l=read_json($log,1048576);
    need(ref($i) eq 'HASH' && ref($l) eq 'HASH', 'real info/log objects');
    need(lc($i->{id}//'') eq $id && ($i->{name}//'') eq $name && lc($l->{jobId}//'') eq $id && lc($l->{sha256}//'') eq $sha, 'info/name/log/hash association');
    need(($i->{status}//'') =~ /\A(?:Accepted|Invalid)\z/ && ($l->{status}//'') eq $i->{status}, 'complete associated Apple result');
    return ($i,$l);
}
sub seal_attempt {
    my ($dir) = @_;
    private_dir($dir);
    my $name=bytes("$dir/artifact-name.txt",128); chomp($name);
    need($name =~ /\AHappyRanch-1\.0\.1-submission-$UUID\.zip\z/,'unique archive name');
    my $sha=bytes("$dir/artifact.sha256",65); chomp($sha);
    my $size=bytes("$dir/artifact.size",32); chomp($size);
    need($sha =~ /\A[0-9a-f]{64}\z/ && $size =~ /\A[1-9][0-9]*\z/ && $size<=134217728,'submission bounds');
    need(regular("$dir/$name",134217728)==$size && digest("$dir/$name") eq $sha,'exact attempted bytes');
    bytes("$dir/ATTEMPTED",256);
    for my $file ($name,qw(artifact-name.txt artifact.sha256 artifact.size ATTEMPTED)) {
        sysopen(my $f,"$dir/$file",O_RDONLY|O_NOFOLLOW) or die "attempt open\n";
        $f->sync or die "attempt sync\n"; close($f) or die "attempt close\n";
    }
    flush_dir($dir);
}
sub seal_files {
    for my $file (@_) {
        regular($file,undef);
        sysopen(my $f,$file,O_RDONLY|O_NOFOLLOW) or die "state open\n";
        $f->sync or die "state sync\n"; close($f) or die "state close\n";
        flush_dir(dirname($file));
    }
}

# No shell interpolation. Streams stay private; only a bounded structured receipt
# and command label/exit (never argv or raw streams) can enter public evidence.
sub command {
    my ($label, $seconds, $cap, $base, @argv) = @_;
    need($label =~ /\A[a-z0-9-]{1,64}\z/ && $seconds =~ /\A[0-9]+\z/ && $seconds >= 1 && $seconds <= 900, 'command label/time');
    need($cap =~ /\A[0-9]+\z/ && $cap >= 1 && $cap <= 1048576 && @argv && $argv[0] =~ m{\A/}, 'command cap/absolute executable');
    private_dir(dirname($base));
    my $end = mono() + $seconds;
    if (defined $ENV{HR_DEADLINE}) {
        need($ENV{HR_DEADLINE} =~ /\A[0-9]+(?:\.[0-9]+)?\z/, 'stage deadline');
        $end = $ENV{HR_DEADLINE} - 10 if $ENV{HR_DEADLINE} - 10 < $end;
    }
    need($end > mono(), 'remaining command time');
    sysopen(my $out, "$base.out", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600) or die "capture stdout: $!\n";
    sysopen(my $err, "$base.err", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600) or die "capture stderr: $!\n";
    pipe(my $ro, my $wo) or die "stdout pipe: $!\n";
    pipe(my $re, my $we) or die "stderr pipe: $!\n";
    my $pid = fork(); need(defined($pid), 'fork');
    if (!$pid) {
        eval {
            setpgid(0, 0) == 0 or die "setpgid\n";
            close($ro); close($re); close($out); close($err);
            open(STDIN, '<', '/dev/null') or die "stdin\n";
            open(STDOUT, '>&', $wo) or die "stdout\n";
            open(STDERR, '>&', $we) or die "stderr\n";
            close($wo); close($we);
            exec {$argv[0]} @argv; die "exec\n";
        };
        POSIX::_exit(126);
    }
    close($wo); close($we);
    # The child also establishes the group before exec; this parent call closes
    # the ordinary scheduling race without treating its EACCES as child failure.
    setpgid($pid, $pid);
    my $select = IO::Select->new($ro, $re);
    my %sink = (fileno($ro)=>$out, fileno($re)=>$err);
    my ($reason, $raw, $count, $teardown, $kill_sent, $owned) = ('', undef, 0, undef, 0, 1);
    local $SIG{TERM} = sub { $reason ||= 'signal'; };
    local $SIG{INT} = sub { $reason ||= 'signal'; };
    local $SIG{HUP} = sub { $reason ||= 'signal'; };
    while (1) {
        if ($owned) {
            my $w = waitpid($pid, WNOHANG);
            $raw = $? if $w == $pid;
            $reason ||= 'wait_error' if $w == -1;
            $owned = 0 if $w == $pid || $w == -1;
        }
        my $group = kill(0, -$pid);
        last if defined($raw) && !$group && !$select->count;
        $reason ||= 'timeout' if mono() >= $end;
        $reason ||= 'unsettled_child' if defined($raw) && $group;
        # A closed leader with inherited streams still needs settlement.
        $reason ||= 'inherited_stream' if defined($raw) && !$group && $select->count && mono() >= $end;
        if ($reason && !defined($teardown)) {
            $teardown = mono(); kill('TERM', -$pid) if $owned;
        }
        # Only the original unreaped child owns this numeric group. Reaping or
        # losing it above ends authority, including between TERM and KILL.
        if ($owned && defined($teardown) && mono() >= $teardown + ($label eq 'stage' ? 8 : 2) && !$kill_sent) {
            kill('KILL', -$pid); $kill_sent = 1;
        }
        last if defined($teardown) && mono() >= $teardown + 10;
        for my $fh ($select->can_read(0.05)) {
            my $n = sysread($fh, my $chunk, 8192);
            if (!defined($n)) { next if $!{EINTR}; $reason ||= 'capture_read'; $select->remove($fh); close($fh); next; }
            if (!$n) { $select->remove($fh); close($fh); next; }
            $count += $n;
            if ($count > $cap) { $reason ||= 'output_cap'; next; }
            print {$sink{fileno($fh)}} $chunk or $reason ||= 'capture_write';
        }
    }
    my $settled = defined($raw) && !kill(0, -$pid) && !$select->count;
    $reason ||= 'teardown_unknown' unless $settled;
    for my $fh ($out, $err) {
        $reason ||= 'capture_sync' unless $fh->flush && $fh->sync;
        $reason ||= 'capture_close' unless close($fh);
    }
    my $r = {label=>$label, pid=>$pid, raw_status=>$raw,
        exit=>defined($raw) && WIFEXITED($raw) ? WEXITSTATUS($raw) : undef,
        signal=>defined($raw) && WIFSIGNALED($raw) ? WTERMSIG($raw) : undef,
        reason=>$reason || 'exited', captured_bytes=>$count, settled=>$settled ? JSON::PP::true : JSON::PP::false};
    save("$base.receipt.json", $JSON->encode($r)."\n");
    save("$base.exit", (defined($r->{exit}) ? $r->{exit} : 'UNKNOWN')."\n");
    if (defined($ENV{HR_STAGE_DIR})) {
        my $path="$ENV{HR_STAGE_DIR}/commands.tsv";
        my $line=join("\t",$label,$r->{exit}//'UNKNOWN',$r->{signal}//0,$r->{reason},$settled?1:0)."\n";
        private_dir($ENV{HR_STAGE_DIR});
        sysopen(my $f,$path,O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW,0600) or die "command journal open\n";
        regular($path,262144-length($line));
        print {$f} $line or die "command journal write\n";
        $f->flush && $f->sync or die "command journal sync\n";
        close($f) or die "command journal close\n";
    }
    return (!$reason && defined($r->{exit}) && $r->{exit} == 0) ? 0 : 1;
}

sub identity {
    my ($argv) = @_;
    # Only fixed, bounded-output, local metadata tools below; supervised too.
    my $base = "$ENV{HR_STAGE_DIR}/identity-" . ++$ENV{HR_ID_SEQUENCE};
    need(command('identity-'.basename($argv->[0]), 5, 8192, $base, @$argv) == 0, 'identity command');
    my $v = bytes("$base.out", 8192); $v =~ s/\n\z//; return $v;
}
sub gate {
    my ($action, $source) = @_;
    my $root = "$ENV{HOME}/Library/Application Support/HappyRanchSigning";
    private_dir($root);
    # Every admission and native recheck obtains live controller/allocation
    # observations through the SAME attended window, never an old scalar.
    if ($^O eq 'darwin') {
        need(!-e "$root/SESSION/CLOSING" && !-l "$root/SESSION/CLOSING",'attended window closing');
        system('/bin/bash','-p',"$HERE/owner-session.sh",'refresh',$source,$ENV{REQUEST_ID});
        need($? == 0, 'current owner/manager consent and controller observation');
    }
    my $s = read_control("$root/owner-session.json");
    closed($s, qw(version source host user uid home node agent_pid supervisor_pid started expires isolation_checked isolation profile_checked root session));
    need($s->{version} eq 'happyranch-signing-owner@2' && $s->{source} eq $source, 'setup source/version');
    need($s->{user} =~ /\A[a-zA-Z0-9_.-]{1,64}\z/ && $s->{host} =~ /\A[a-zA-Z0-9_.-]{1,253}\z/, 'nonsecret context names');
    need($s->{uid} == $< && $< != 0 && $s->{home} eq $ENV{HOME} && $s->{node} eq 'happyranch-mac-signing-owner', 'owner mapping');
    need($s->{started} <= time() && $s->{expires} > time() && $s->{expires} - $s->{started} <= 1800, 'attended session window');
    need($s->{isolation} eq 'exclusive-one-owner-workload' && $s->{isolation_checked} >= $s->{started} && $s->{isolation_checked} <= time(), 'external isolation observation');
    need($s->{agent_pid} =~ /\A[1-9][0-9]*\z/ && kill(0,$s->{agent_pid}), 'live attended agent');
    my $p = read_control("$root/permissions/$ENV{REQUEST_ID}.json");
    closed($p, qw(version delivery_root root task session source action request run job build queue issued expires log_review upload));
    need($p->{version} eq 'happyranch-signing-permission@2' && $p->{delivery_root} eq 'TASK-14158' && $p->{root} eq $s->{root} && $p->{session} eq $s->{session}, 'permission owner/version');
    need($p->{task} eq $p->{root} && $s->{root} =~ /\ATASK-[1-9][0-9]*\z/ && $s->{supervisor_pid} =~ /\A[1-9][0-9]*\z/ && kill(0,$s->{supervisor_pid}), 'current attended continuation');
    need($p->{task} =~ /\ATASK-[1-9][0-9]*\z/ && $p->{session} =~ /\Asess-[0-9a-f]{32}\z/, 'actual current task/session required');
    need($p->{source} eq $source && $p->{action} eq $action && $p->{request} eq $ENV{REQUEST_ID} && $p->{run} eq $ENV{RUN_ID}, 'permission selection');
    need($p->{queue} =~ /\A[1-9][0-9]*\z/,'observed numeric queue');
    need($p->{job} eq $ENV{JOB_NAME} && "$p->{build}" eq $ENV{BUILD_NUMBER} && (!defined($ENV{QUEUE_ID}) || "$p->{queue}" eq $ENV{QUEUE_ID}), 'current build association');
    # Jenkins core does not export queueId. The held producer must obtain it
    # from THIS numeric build's API observation, never a caller scalar.
    $ENV{QUEUE_ID}="$p->{queue}";
    need($p->{issued} <= time() && $p->{expires} > time() && $p->{expires} <= $s->{expires} && $p->{expires} - $p->{issued} <= $BUDGET{$action}, 'current stage window');
    # Evaluate freshness at this admission/recheck, never at permission issuance.
    my $now = time();
    need($s->{isolation_checked} <= $now && $s->{isolation_checked} >= $now-120,'fresh stage isolation observation');
    if ($action eq 'preflight') {
        my $u=$p->{upload}; closed($u,qw(type name filename size sha256 build));
        need($u->{type} eq 'hudson.model.FileParameterValue' && $u->{name} eq 'ORIGINAL_ZIP' && $u->{filename} eq 'original.zip' && $u->{size} == $INPUT_SIZE && $u->{sha256} eq $INPUT_SHA && "$u->{build}" eq $ENV{BUILD_NUMBER},'actual current upload observation');
    } else { need(!defined($p->{upload}),'unexpected upload observation'); }
    need($s->{profile_checked} eq 'actual-authorized-history-exit0', 'existing profile check') if $action =~ /\A(?:submit|status|finalize)\z/;
    if ($action eq 'finalize') {
        my $r = $p->{log_review};
        closed($r, qw(sha256 submission_id inspected_complete warnings_disposition safe_to_publish));
        need($r->{sha256} eq $ENV{REVIEWED_LOG_SHA256} && $r->{sha256} =~ /\A[0-9a-f]{64}\z/ && $r->{submission_id} =~ /\A$UUID\z/,
            'review binding');
        need(JSON::PP::is_bool($r->{inspected_complete}) && $r->{inspected_complete} && JSON::PP::is_bool($r->{safe_to_publish}) && $r->{safe_to_publish}, 'root full-log inspection and nonsecret disposition');
        need($r->{warnings_disposition} eq 'none-present' || $r->{warnings_disposition} eq 'all-explicitly-accepted-by-root', 'root warning disposition');
    } else { need(!defined($p->{log_review}), 'unexpected review'); }
    need(!-e "$root/SESSION/CLOSING" && !-l "$root/SESSION/CLOSING",'attended window closing');
    return ($s,$p);
}

sub render {
    my ($source) = @_;
    need($source =~ /\A[0-9a-f]{40}\z/, 'literal merged source SHA');
    open(my $f, '<', "$HERE/config.xml.in") or die "template open\n";
    local $/; my $xml = <$f>; close($f) or die "template close\n";
    my %pins = (SOURCE=>$source);
    for my $i (0..$#FILES) { $pins{'FILE'.$i} = digest("$HERE/$FILES[$i]"); }
    $xml =~ s/@@([A-Z0-9]+)@@/exists $pins{$1} ? $pins{$1} : die "unresolved placeholder\n"/ge;
    need($xml !~ /@@/, 'unresolved placeholder');
    print $xml or die "render output\n";
}

# Save only a closed, nonsecret projection of THIS info response. Raw output,
# argv and parser errors remain private. This is not full-log acceptance or ID
# adoption; the failure reader separately requires the saved canonical ID.
sub observe_info {
    my ($base,$id,$name) = @_;
    need($id =~ /\A$UUID\z/ && $name =~ /\AHappyRanch-1\.0\.1-submission-$UUID\.zip\z/,'info identity syntax');
    my $o={id=>$id,name=>$name,status=>'UNKNOWN',cause=>'info-unobserved'};
    my $file="$base.out";
    if (-e $file || -l $file) {
        # A timeout/capped/unsettled response cannot supply complete info, even
        # if the retained prefix happens to parse as an object.
        my $complete=eval {
            my $r=read_json("$base.receipt.json");
            need($r->{label} eq 'info' && defined($r->{exit}) && $r->{exit}==0 && !defined($r->{signal}) && $r->{reason} eq 'exited' && JSON::PP::is_bool($r->{settled}) && $r->{settled},'complete info command'); 1;
        };
        if (!$complete) {
            $o->{cause}='info-command-incomplete';
            save("$ENV{HR_STAGE_DIR}/notary-info.json",$JSON->encode($o)."\n");
            return;
        }
        my $i; my $parsed=eval { $i=read_json($file,262144); 1 };
        $o->{cause}='info-malformed-or-unavailable';
        if ($parsed && ref($i) eq 'HASH') {
            $o->{cause}='info-id-name-mismatch';
            if (!ref($i->{id}) && !ref($i->{name}) && lc($i->{id}//'') eq $id && ($i->{name}//'') eq $name) {
                $o->{cause}='info-status-unknown';
                if (!ref($i->{status}) && ($i->{status}//'') =~ /\A(?:Accepted|Invalid|Pending|In Progress)\z/) {
                    $o->{status}=$i->{status}; $o->{cause}='validated-info';
                }
            }
        }
    }
    save("$ENV{HR_STAGE_DIR}/notary-info.json",$JSON->encode($o)."\n");
}
sub failure_output {
    my ($sd,$run,$action) = @_;
    my $o={final=>undef,native_status=>'UNKNOWN',private_key_profile_preflight=>'UNVERIFIED',
        notarization=>{info_status=>undef,cause=>'info-unobserved',association=>'unproven'}};
    return $o unless $action eq 'status' && (-e "$sd/notary-info.json" || -l "$sd/notary-info.json");
    my $i=read_control("$sd/notary-info.json"); closed($i,qw(id name status cause));
    need($i->{id} =~ /\A$UUID\z/ && $i->{name} =~ /\AHappyRanch-1\.0\.1-submission-$UUID\.zip\z/,'observed info identity');
    need($i->{cause} =~ /\A(?:info-unobserved|info-command-incomplete|info-malformed-or-unavailable|info-id-name-mismatch|info-status-unknown|validated-info)\z/,'info cause allowlist');
    need($i->{status} =~ /\A(?:UNKNOWN|Accepted|Invalid|Pending|In Progress)\z/ && (($i->{cause} eq 'validated-info') == ($i->{status} ne 'UNKNOWN')),'info status allowlist');
    $o->{notarization}{cause}=$i->{cause};
    return $o if $i->{status} eq 'UNKNOWN';
    $o->{notarization}{info_status}=$i->{status};
    $o->{notarization}{cause}='recovery-association-unproven';
    # An unassociated recovery candidate remains UNKNOWN. Only the existing
    # submit/recovery writer may create id.txt; this reader never adopts one.
    my $matched=eval {
        my $id=bytes("$run/submission/id.txt",64); chomp $id;
        my $name=bytes("$run/submission/artifact-name.txt",128); chomp $name;
        need($id eq $i->{id} && $name eq $i->{name},'saved submission info association'); 1;
    };
    if ($matched) {
        $o->{native_status}=$i->{status};
        $o->{notarization}{cause}='validated-info';
        $o->{notarization}{association}='saved-id-and-name; full-log acceptance separate';
    }
    return $o;
}
sub command_journal {
    my ($sd) = @_;
    my $raw=(-e "$sd/commands.tsv" || -l "$sd/commands.tsv") ? bytes("$sd/commands.tsv",262100) : '';
    my $native=qr/(?:retain-unsigned|extract-baseline|copy-app|archive|archive-extract|submit|info|log|history|copy-final|staple|ticket|reextract|reextracted-ticket|syspolicy|gatekeeper|verify-outer|(?:original-metadata|original-entitlements|dependencies|sign|verify|metadata|entitlements)-(?:[1-9]|1[01]))/;
    my $label=qr/(?:stage|keychain-state|identity-(?:uname|hostname|id|stat|dscl|ps|df)|(?:console-|lock-)?$native)/;
    my $reason=qr/(?:exited|signal|wait_error|timeout|unsettled_child|inherited_stream|capture_read|output_cap|capture_write|teardown_unknown|capture_sync|capture_close)/;
    for my $line (split /(?<=\n)/,$raw) {
        need($line =~ /\A$label\t(?:UNKNOWN|[0-9]{1,3})\t[0-9]{1,3}\t$reason\t[01]\n\z/,'nonsecret command journal allowlist');
    }
    return "label\texit\tsignal\treason\tsettled\n".$raw;
}
sub publish_failure {
    my ($root,$ws,$sd,$action,$source,$p,$cause) = @_;
    # Quarantine the fixed publisher entries without following their contents.
    # Even partially published success/final artifacts must not be reused.
    private_dir($ws); private_dir("$root/ACTIVE");
    my $hold="$root/ACTIVE/failed-publisher";
    mkdir($hold,0700) or die "failure publisher hold\n";
    for my $d (qw(public-evidence public-output)) {
        rename("$ws/$d","$hold/$d") or die "retain failed publisher\n" if -e "$ws/$d" || -l "$ws/$d";
    }
    mkdir("$ws/public-evidence",0700) or die "failure evidence directory\n";
    mkdir("$ws/public-output",0700) or die "failure output directory\n";
    my $journal; my $journal_ok=eval { $journal=command_journal($sd); 1 };
    my $output; my $output_ok=eval { $output=failure_output($sd,"$root/runs/$ENV{RUN_ID}",$action); 1 };
    $output //= {final=>undef,native_status=>'UNKNOWN',notarization=>{cause=>'observation-unavailable-or-invalid'}};
    my $r={version=>'happyranch-signing-stage@1',action=>$action,request=>$ENV{REQUEST_ID},run=>$ENV{RUN_ID},
        build=>$ENV{BUILD_NUMBER},queue=>$ENV{QUEUE_ID},source=>$source,outcome=>'FAILED_OR_UNKNOWN',cause=>$cause,
        task=>$p ? $p->{task}:undef,session=>$p ? $p->{session}:undef,output=>$output,
        command_journal=>$journal_ok ? (length($journal)>length("label\texit\tsignal\treason\tsettled\n") ? 'available-snapshot':'unobserved'):'unavailable-or-invalid',
        evidence_publication=>$journal_ok && $output_ok ? 'complete':'failed'};
    save("$ws/public-evidence/commands.tsv",$journal) if $journal_ok;
    save("$ws/public-evidence/output.json",$JSON->encode($output)."\n");
    save("$ws/public-evidence/stage.json",$JSON->encode($r)."\n");
    my $total=0;
    $total+=regular("$ws/public-evidence/$_",1048576) for grep {-e "$ws/public-evidence/$_"} qw(stage.json commands.tsv output.json);
    need($total<=2097152,'public evidence aggregate');
}

sub stage {
    my ($source,@pins) = @_;
    need(defined($source) && $source =~ /\A[0-9a-f]{40}\z/ && @pins == @FILES, 'literal source/file pins');
    for my $i (0..$#FILES) {
        need($pins[$i] =~ /\A[0-9a-f]{64}\z/ && digest("$HERE/$FILES[$i]") eq $pins[$i], 'installed file pin');
    }
    my $action = $ENV{ACTION} // '';
    need(exists($BUDGET{$action}), 'six-action allowlist');
    for my $k (qw(REQUEST_ID RUN_ID RECOVER_ID REVIEWED_LOG_SHA256)) { $ENV{$k} //= ''; }
    need($ENV{REQUEST_ID} =~ /\A$UUID\z/, 'request UUID');
    need($action eq 'preflight' ? $ENV{RUN_ID} eq '' : $ENV{RUN_ID} =~ /\A$UUID\z/, 'run UUID');
    need($ENV{RECOVER_ID} eq '' || ($action eq 'status' && $ENV{RECOVER_ID} =~ /\A$UUID\z/), 'recovery selection');
    need($action eq 'finalize' ? $ENV{REVIEWED_LOG_SHA256} =~ /\A[0-9a-f]{64}\z/ : $ENV{REVIEWED_LOG_SHA256} eq '', 'review hash selection');
    need(($ENV{NODE_NAME}//'') eq 'happyranch-mac-signing-owner' && ($ENV{JOB_NAME}//'') eq 'happyranch-mac-signing', 'fixed job/node');
    need(($ENV{BUILD_NUMBER}//'') =~ /\A[1-9][0-9]*\z/, 'numeric build');
    need($^O eq 'darwin', 'Darwin required; no native effects');
    need($< != 0 && defined($ENV{HOME}), 'nonroot owner home');
    my $root = "$ENV{HOME}/Library/Application Support/HappyRanchSigning";
    need($HERE eq "$root/release/$source", 'fixed installed merged release');
    private_dir($HERE); private_dir($root);
    private_dir("$root/inputs"); private_dir("$root/runs"); private_dir("$root/permissions");
    my $ws = plain($ENV{WORKSPACE});
    need(index($ws, "$root/agent/workspace/") == 0, 'fixed agent workspace');
    private_dir($ws);
    need((stat($ws))[0] == (stat($root))[0], 'workspace/durable state on checked volume');
    my $deadline = mono() + $BUDGET{$action};
    # Strip launch/credential/shell injection environment; keep only consumed data.
    my %keep; for (qw(HOME ACTION REQUEST_ID RUN_ID RECOVER_ID REVIEWED_LOG_SHA256 NODE_NAME JOB_NAME BUILD_NUMBER QUEUE_ID WORKSPACE ORIGINAL_ZIP)) {
        $keep{$_} = $ENV{$_} if defined $ENV{$_};
    }
    %ENV = (%keep, PATH=>'/usr/bin:/bin:/usr/sbin:/sbin', LC_ALL=>'C', HR_SOURCE=>$source, HR_DEADLINE=>$deadline, HR_ID_SEQUENCE=>0);
    sysopen(my $lock, "$root/workload.lock", O_RDWR | O_CREAT | O_NOFOLLOW, 0600) or die "workload lock\n";
    regular("$root/workload.lock", 0); flock($lock, LOCK_EX | LOCK_NB) or die "workload busy\n";
    # Retained directory survives crashes; no TTL, automatic repair or age unlock.
    mkdir("$root/ACTIVE",0700) or die "retained active operation; root reconciliation required\n";
    my $sd = "$root/ACTIVE/$ENV{REQUEST_ID}";
    mkdir($sd,0700) or die "stage directory\n"; $ENV{HR_STAGE_DIR}=$sd;
    my ($s,$p,$result,$error,$marker);
    my $failure_cause='admission-incomplete';
    eval {
        ($s,$p) = gate($action,$source);
        save("$sd/operation.json",$JSON->encode({source=>$source,action=>$action,task=>$p->{task},session=>$p->{session},request=>$ENV{REQUEST_ID},run=>$ENV{RUN_ID},job=>$ENV{JOB_NAME},build=>$ENV{BUILD_NUMBER},queue=>$ENV{QUEUE_ID}})."\n");
        if ($action ne 'preflight') {
            mkdir("$root/runs/$ENV{RUN_ID}.lock",0700) or die "retained run lock\n";
            save("$root/runs/$ENV{RUN_ID}.lock/owner.json",$JSON->encode({request=>$ENV{REQUEST_ID},task=>$p->{task},session=>$p->{session}})."\n");
        }
        # FileParameterValue/build proof AND the actual executor bytes precede
        # extraction, keychain queries, signing and Apple access.
        my $input = "$root/inputs/original.zip";
        if ($action eq 'preflight') {
            need(($ENV{ORIGINAL_ZIP}//'') eq 'original.zip', 'current core FileParameterValue original filename');
            need(!-e $input && !-l $input, 'input already admitted; no replacement');
            need(regular("$ws/ORIGINAL_ZIP",67108864)==$INPUT_SIZE && digest("$ws/ORIGINAL_ZIP") eq $INPUT_SHA, 'current upload size/hash before extraction');
        } else {
            need(!exists($ENV{ORIGINAL_ZIP}) && !-e "$ws/ORIGINAL_ZIP" && !-l "$ws/ORIGINAL_ZIP", 'later stage must not upload/reuse workspace file');
            need(regular($input,67108864)==$INPUT_SIZE && digest($input) eq $INPUT_SHA, 'retained input');
            my $m=read_json("$root/inputs/preflight.complete");
            need($m->{source} eq $source && $m->{action} eq 'preflight','completed input admission pins');
        }
        # Preserve only the previous workload's finite publisher directories;
        # nothing in a caller workspace is adopted as current stage evidence.
        for my $d (qw(public-evidence public-output)) {
            next unless -e "$ws/$d" || -l "$ws/$d";
            private_dir("$ws/$d");
            opendir(my $dh,"$ws/$d") or die "publisher enumerate\n";
            my @names=grep {$_ ne '.' && $_ ne '..'} readdir($dh);
            closedir($dh) or die "publisher close\n";
            need(@names<=4,'publisher file count');
            for my $n (@names) {
                need($d eq 'public-output' ? $n eq $FINAL : $n =~ /\A(?:stage\.json|commands\.tsv|output\.json|notary-log\.json)\z/,'publisher filename allowlist');
                regular("$ws/$d/$n",$d eq 'public-output' ? 134217728 : 1048576);
            }
            rename("$ws/$d","$sd/prior-$d") or die "preserve prior publisher directory\n";
        }
        need(identity(['/usr/bin/uname','-m']) eq 'arm64', 'native arm64');
        need(identity(['/bin/hostname']) eq $s->{host}, 'pinned host');
        need(identity(['/usr/bin/id','-un']) eq $s->{user}, 'actual owner name');
        need(identity(['/usr/bin/stat','-f','%u','/dev/console']) eq "$s->{uid}", 'active console owner');
        my $home = identity(['/usr/bin/dscl','.','-read',"/Users/$s->{user}",'NFSHomeDirectory']);
        need($home eq "NFSHomeDirectory: $ENV{HOME}", 'directory-service home');
        my $processes=identity(['/bin/ps','-U',"$s->{uid}",'-o','pid=,comm=']);
        my @java;
        for my $line (split /\n/,$processes) {
            need($line =~ /\A\s*([0-9]+)\s+(.+)\z/,'bounded owner process projection');
            my ($pid,$comm)=($1,$2);
            push @java,$pid if basename($comm) eq 'java';
        }
        need(@java==1 && $java[0]==$s->{agent_pid},'sole owner-UID Java agent');
        my $keychain = "$ENV{HOME}/Library/Keychains/login.keychain-db";
        regular($keychain,undef); # Metadata only: never read the keychain bytes.
        need(command('keychain-state',180,8192,"$sd/keychain-state",'/usr/bin/xcrun','swift',"$HERE/keychain-state.swift",$keychain)==0, 'exact unlocked keychain');
        my $free = identity(['/bin/df','-Pk',$root]);
        my @lines = split /\n/,$free; my @fields = split /\s+/,$lines[-1];
        need(@fields >= 6 && $fields[3] =~ /\A[0-9]+\z/ && $fields[3] >= 1048576, 'one GiB free');
        if ($action eq 'preflight') {
            copy_private("$ws/ORIGINAL_ZIP",$input,67108864);
        }
        my $run = "$root/runs/$ENV{RUN_ID}";
        my @args = ($action);
        if ($action eq 'preflight') { push @args,$input; }
        elsif ($action eq 'sign') { need(!-e $run && !-l $run,'new run only'); push @args,$input,$run; }
        else {
            private_dir($run); push @args,$run;
            push @args,'--recover-id',$ENV{RECOVER_ID} if length($ENV{RECOVER_ID});
            push @args,'--log-reviewed',$ENV{REVIEWED_LOG_SHA256} if $action eq 'finalize';
        }
        # Native script sees only a checked stage context; direct invocation refuses.
        $failure_cause='native-stage-incomplete';
        need(command('stage', $BUDGET{$action}-10,262144,"$sd/native",'/bin/bash','-p',"$HERE/mac-sign.sh",@args)==0, 'native stage incomplete');
        $failure_cause='post-work-check-incomplete';
        gate($action,$source);
        if ($action eq 'sign') {
            seal_files(map {"$run/$_"} qw(original.zip package.sha256 baseline.sha256 baseline-data.txt signed-files.txt sign.ready));
        }
        if ($action eq 'submit') { seal_files("$run/submission/id.txt"); }
        if ($action eq 'status') {
            seal_files("$run/submission/id.txt","$run/accepted-status.txt");
            my $snap=bytes("$run/accepted-status.txt",64); chomp($snap);
            need($snap =~ /\Astatus\.[A-Za-z0-9]{6}\z/,'accepted snapshot');
            seal_files("$run/$snap/info.json","$run/$snap/log.json");
        }
        if ($action eq 'finalize') {
            seal_files(map {"$run/final/$_"} ($FINAL,qw(final.sha256 final.size association.txt final-files.txt ready)));
        }
        $marker = $action eq 'preflight' ? "$root/inputs/preflight.complete" : $action eq 'sign' ? "$run/sign.complete" : $action eq 'finalize' ? "$run/final/complete" : "$sd/complete";
        if ($action eq 'preflight') { unlink("$ws/ORIGINAL_ZIP") or die "remove admitted workspace input\n"; }
        $failure_cause='evidence-publication-incomplete';
        # Publisher must start empty; a stale artifact cannot satisfy this build.
        mkdir("$ws/public-evidence",0700) or die "public evidence exists\n";
        mkdir("$ws/public-output",0700) or die "public output exists\n";
        my $output = {final=>undef, native_status=>$action eq 'status' ? 'Accepted' : 'selected-stage-complete', private_key_profile_preflight=>'UNVERIFIED'};
        if ($action eq 'finalize' || $action eq 'test') {
            my $zip = "$run/final/$FINAL";
            my $size = regular($zip,134217728); need($size>0,'nonempty final ZIP');
            copy_private($zip,"$ws/public-output/$FINAL",134217728);
            $output->{final}={filename=>$FINAL,size=>$size,sha256=>digest($zip)};
        }
        if ($action eq 'finalize') {
            my $snap = bytes("$run/accepted-status.txt",64); chomp $snap;
            need($snap =~ /\Astatus\.[A-Za-z0-9]{6}\z/, 'status snapshot');
            my $log = "$run/$snap/log.json";
            need(digest($log) eq $p->{log_review}{sha256}, 'reviewed complete log');
            copy_private($log,"$ws/public-evidence/notary-log.json",1048576);
        }
        save("$ws/public-evidence/output.json",$JSON->encode($output)."\n");
        $result = {version=>'happyranch-signing-stage@1',source=>$source,files=>{map {$FILES[$_]=>$pins[$_]} 0..$#FILES},action=>$action,
            task=>$p->{task},session=>$p->{session},root=>$p->{root},request=>$ENV{REQUEST_ID},run=>$ENV{RUN_ID},job=>$ENV{JOB_NAME},build=>$ENV{BUILD_NUMBER},queue=>$ENV{QUEUE_ID},node=>$ENV{NODE_NAME},host=>$s->{host},uid=>$<,
            input=>{size=>$INPUT_SIZE,sha256=>$INPUT_SHA},outcome=>'selected-stage-complete',output=>$output};
        my $tsv=command_journal($sd);
        save("$ws/public-evidence/commands.tsv",$tsv);
        save("$ws/public-evidence/stage.json",$JSON->encode($result)."\n");
        save("$sd/stage.json",$JSON->encode($result)."\n");
        my $public_bytes=0;
        for my $f (qw(stage.json commands.tsv output.json notary-log.json)) {
            $public_bytes+=regular("$ws/public-evidence/$f",1048576) if -e "$ws/public-evidence/$f";
        }
        need($public_bytes<=2097152,'public evidence aggregate');
        need(mono() < $deadline,'stage publication deadline');
        # Rename retained operation into durable history while kernel lock stays
        # held. No deletion, lock expiry or automatic incomplete-run reuse.
        $failure_cause='history-publication-incomplete';
        private_dir("$root/receipts");
        if ($action ne 'preflight') {
            rename("$root/runs/$ENV{RUN_ID}.lock","$sd/run-lock") or die "retain completed run lock\n";
            flush_dir("$root/runs");
        }
        rename("$root/ACTIVE","$root/receipts/$ENV{REQUEST_ID}") or die "retain stage history\n";
        $marker = "$root/receipts/$ENV{REQUEST_ID}/$ENV{REQUEST_ID}/complete" if $marker eq "$sd/complete";
        $sd = "$root/receipts/$ENV{REQUEST_ID}/$ENV{REQUEST_ID}";
        flush_dir($root); flush_dir("$root/receipts");
        $failure_cause='completion-marker-incomplete';
        save($marker,$JSON->encode({request=>$ENV{REQUEST_ID},source=>$source,action=>$action})."\n");
        1;
    } or $error = $@ || 'stage failure';
    if ($error) {
        # A failed post-rename publication still latches admission closed.
        mkdir("$root/ACTIVE",0700) unless -e "$root/ACTIVE";
        eval { save("$root/ACTIVE/failure.txt",substr($error,0,4096)); };
        my $published=eval { publish_failure($root,$ws,$sd,$action,$source,$p,$failure_cause); 1 };
        print STDERR "Failure evidence publication FAILED_OR_UNKNOWN.\n" unless $published;
        print STDERR "Signing stage FAILED_OR_UNKNOWN; retain private operation and reconcile.\n";
        return 1;
    }
    print "Selected signing stage complete; GUI/install/public release unverified.\n";
    return 0;
}

return 1 if caller; # Workload-local primitives shared with owner-session.sh.
umask 0077;
my $mode = shift(@ARGV) // '';
if ($mode eq '--render' && @ARGV == 1) { render($ARGV[0]); exit 0; }
if ($mode eq '--command' && @ARGV >= 6) {
    my ($label,$seconds,$cap,$base,$separator,@argv)=@ARGV;
    need($separator eq '--','command separator'); exit command($label,$seconds,$cap,$base,@argv);
}
if ($mode eq '--gate' && @ARGV == 0) { gate($ENV{ACTION},$ENV{HR_SOURCE}); exit 0; }
if ($mode eq '--save' && @ARGV == 2) { save(@ARGV); exit 0; }
if ($mode eq '--seal-attempt' && @ARGV == 1) { seal_attempt(@ARGV); exit 0; }
if ($mode eq '--associated' && @ARGV == 5) { associated(@ARGV); exit 0; }
if ($mode eq '--observe-info' && @ARGV == 3) { observe_info(@ARGV); exit 0; }
if ($mode eq '--recover' && @ARGV == 6) {
    my ($history,$info,$log,$id,$name,$sha)=@ARGV;
    associated($info,$log,$id,$name,$sha);
    my $h=read_json($history,262144);
    need(ref($h) eq 'HASH' && ref($h->{history}) eq 'ARRAY' && @{$h->{history}}<=1000,'bounded real history');
    my @matches=grep {ref($_) eq 'HASH' && ($_->{name}//'') eq $name} @{$h->{history}};
    need(@matches==1 && lc($matches[0]{id}//'') eq $id,'one exact real history candidate');
    exit 0;
}
if ($mode eq '--review-log' && @ARGV == 2) {
    my ($file,$id)=@ARGV; my ($s,$p)=gate('finalize',$ENV{HR_SOURCE});
    my $r=$p->{log_review}; my $l=read_json($file,1048576);
    need($r->{sha256} eq digest($file) && $r->{submission_id} eq lc($id),'actual reviewed ID/full log hash');
    need(!defined($l->{issues}) || ref($l->{issues}) eq 'ARRAY','complete issue list');
    my @issues=@{$l->{issues}//[]};
    need(!grep({ref($_) ne 'HASH' || ($_->{severity}//'') ne 'warning'} @issues),'no unresolved error/unknown issue');
    need(@issues ? $r->{warnings_disposition} eq 'all-explicitly-accepted-by-root' : $r->{warnings_disposition} eq 'none-present','actual warning disposition');
    exit 0;
}
if ($mode eq '--stage') { exit stage(@ARGV); }
die "Usage: bounded-run.pl --render MERGED_SHA | --stage SOURCE SIX_FILE_HASHES | --command LABEL SECONDS CAP PRIVATE_BASE -- ABS_COMMAND ARGS | --gate | --save PRIVATE_FILE TEXT\n";
