#!/bin/bash -p
# Attended workload-local entry. No source mode authorizes its live invocation.
set +x
set -euo pipefail
unset BASH_ENV ENV PERL5OPT PERL5LIB CDPATH GLOBIGNORE JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
umask 077
ulimit -c 0
exec /usr/bin/perl -x "$0" "$@"
: <<'OWNER_PERL'
#!perl
use strict;
use warnings;
use POSIX qw(:sys_wait_h setpgid TCSANOW ECHO ICANON);
use Fcntl qw(:DEFAULT :flock);
use Time::HiRes qw(time sleep);
use IO::Select;
use JSON::PP;
use File::Basename qw(dirname basename);
use Cwd qw(abs_path);
my $here=dirname(abs_path(__FILE__));
require "$here/bounded-run.pl";
my $json=JSON::PP->new->canonical->utf8;
my $node='happyranch-mac-signing-owner';
my $job='happyranch-mac-signing';
my $jar_sha='4ae5bbd00b0f79f4453d20fbb6b70dcac4d0a9a4d21d85b18b2c72dd9d117aae';
my $jar_version='3355.3357.v931d3c992987';
my $uuid=qr/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;
my %budget=(preflight=>180,sign=>900,submit=>600,status=>300,finalize=>900,test=>600);
my @files=qw(run-stage.sh mac-sign.sh code-inventory.tsv bounded-run.pl keychain-state.swift owner-session.sh);
my ($mode,@args)=@ARGV;
my $home=$ENV{HOME};
%ENV=(HOME=>$home//'',PATH=>'/usr/bin:/bin:/usr/sbin:/sbin',LC_ALL=>'C');
umask 0077;
my $cancel=0;
my $window_end;
$SIG{$_}=sub {$cancel=1} for qw(TERM INT HUP QUIT);
$SIG{PIPE}='IGNORE';

# Bounded fixed-command observation, with no raw stderr/argv publication. The
# caller owns every child; no external PID is ever signalled. No secret uses it.
sub observe {
    my ($seconds,$cap,@cmd)=@_;
    need(!defined($window_end)||mono()<$window_end,'remaining attended observation time');
    pipe(my $r,my $w) or die "observation pipe\n";
    my $pid=fork(); need(defined($pid),'observation fork');
    if (!$pid) {
        setpgid(0,0)==0 or POSIX::_exit(126);
        close($r); open(STDIN,'<','/dev/null') or POSIX::_exit(126);
        open(STDOUT,'>&',$w) or POSIX::_exit(126); close($w);
        open(STDERR,'>','/dev/null') or POSIX::_exit(126);
        exec {$cmd[0]} @cmd or POSIX::_exit(126);
    }
    close($w); setpgid($pid,$pid);
    my $sel=IO::Select->new($r); my ($out,$raw,$eof)=('',undef,0);
    my $end=mono()+$seconds; $end=$window_end if defined($window_end)&&$window_end<$end; my $bad=0;
    while (mono()<$end && !$cancel) {
        unless (defined($raw)) {
            my $n=waitpid($pid,WNOHANG); $raw=$? if $n==$pid;
            if ($n==-1) {$bad=1;last} # Lost original-child ownership, never signal.
        }
        last if defined($raw) && $eof && !kill(0,-$pid);
        if (!$eof && $sel->can_read(0.05)) {
            my $got=sysread($r,my $chunk,8192);
            if (!defined($got)) { next if $!{EINTR}; $bad=1; last; }
            if (!$got) { $eof=1; close($r); }
            else { $out.=$chunk; if (length($out)>$cap) {$bad=1;last} }
        } else { sleep 0.02; }
    }
    if (!defined($raw) || !$eof || kill(0,-$pid) || $cancel || $bad) {
        # A reaped leader no longer owns its numeric group. Even an inherited
        # stream or a currently existing group cannot renew signaling authority.
        my $settled=defined($raw)?!kill(0,-$pid):reap($pid);
        close($r) unless $eof;
        die "REFUSED: observation FAILED_OR_UNKNOWN; group/stream unresolved; no admission\n" unless $settled&&$eof;
        die "REFUSED: observation failed/timeout/cancelled; no admission\n";
    }
    need(WIFEXITED($raw)&&WEXITSTATUS($raw)==0,'observation exit');
    return $out;
}
sub line { my $s=observe(@_);$s=~s/\n\z//;return $s }
sub canonical {
    my ($raw)=@_; my $v=$json->decode($raw);
    need($raw eq $json->encode($v)."\n",'canonical current observation'); return $v;
}
sub id_args {
    my ($source,$root,$sid)=@_;
    need(defined($sid)&&$source=~/\A[0-9a-f]{40}\z/&&$root=~/\ATASK-[1-9][0-9]*\z/&&$sid=~/\Asess-[0-9a-f]{32}\z/,'current source/root/session syntax');
}
sub controller_home {
    # Selected existing controller owner/mapping. A different host/user refuses;
    # root must resolve mapping/trust, never guess or accept an advertised URL.
    need($^O eq 'linux'&&$<!=0&&$home eq '/home/benze','selected controller context');
    return '/home/benze/HappyRanchSigningController';
}
sub control_dir { my ($root)=@_;return controller_home()."/$root/grants" }
sub remote_release {my ($source,$root)=@_;return "/home/benze/HappyRanchSigningController/$root/release/$source"}
sub native_manager {
    my ($root,$sid)=@_;
    my $d=$json->decode(observe(10,262144,'/home/benze/projects/happyranch/.venv/bin/happyranch','details','--org','happyranch',$root,'--json'));
    my $t=$d->{task};my $p=$d->{pause};
    need(ref($t) eq 'HASH'&&$t->{task_id} eq $root&&$t->{assigned_agent} eq 'engineering_manager'&&$t->{task_type} eq 'task'&&!defined($t->{parent_task_id})&&$t->{status} eq 'in_progress'&&$t->{current_session_id} eq $sid,'genuine current native manager allocation');
    need(ref($p) eq 'HASH'&&$p->{evidence_complete}&& !$p->{effective_hold}&&$p->{root_task_id} eq $root,'current unheld native root');
    # This live allocation is necessary, NOT permission. Only the genuine root,
    # under separately granted live authority, may invoke controller-grant.
}
sub api {
    my ($path,$binary)=@_;
    need($path=~m{\A/(?:api/json\?|computer/|queue/|job/|jnlpJars/)} && $path!~/[\r\n]/,'fixed controller read');
    return observe(12,$binary?1406409:262144,'/usr/bin/curl','--disable','--globoff','--silent','--fail','--noproxy','*','--proxy','','--proto','=http','--max-redirs','0','--connect-timeout','3','--max-time','10','--max-filesize',$binary?'1406408':'262144','--netrc-file','/home/benze/.config/jenkins/netrc','http://127.0.0.1:8081'.$path);
}
# Inspect JSON types before any comparison can coerce a missing/string fact.
# JSON::PP preserves strings when encoding, so "0" cannot stand in for count 0.
sub json_count {return defined($_[0])&&!ref($_[0])&&$json->encode($_[0])=~/\A(?:0|[1-9][0-9]*)\z/}
sub json_string {return defined($_[0])&&!ref($_[0])&&$json->encode($_[0])=~/\A"/}
sub fresh_controller {
    my ($source,$root,$sid,$grant)=@_;
    native_manager($root,$sid);
    need($here eq remote_release($source,$root),'pinned controller release location'); private_dir($here);
    my $c=$json->decode(api('/api/json?tree=numExecutors,quietingDown,jobs[name,_class,buildable,disabled,labelExpression]'));
    need(ref($c) eq 'HASH'&&json_count($c->{numExecutors})&&JSON::PP::is_bool($c->{quietingDown})&&ref($c->{jobs}) eq 'ARRAY'&&@{$c->{jobs}}<=16,'complete typed bounded controller inventory');
    need($c->{numExecutors}==0&&!$c->{quietingDown},'controller zero executors and not quieting');
    my $seen=0;
    for my $j (@{$c->{jobs}}) {
        need(ref($j) eq 'HASH'&&json_string($j->{name})&&$j->{name}=~/\A[a-zA-Z0-9_-]+\z/&&json_string($j->{_class})&&JSON::PP::is_bool($j->{buildable})&&JSON::PP::is_bool($j->{disabled}),'complete typed top-level job inventory; folders/unknown hold');
        if ($j->{name} eq $job) { $seen++; next; }
        # Dynamic Pipeline/folder/unknown placement is not inferred from idle.
        need(($j->{_class}//'') eq 'hudson.model.FreeStyleProject','dynamic/unknown other-job placement');
        next if $j->{disabled};
        # Jenkins2.568.3 AbstractProject exports getAssignedLabelString under
        # labelExpression. Null means unrestricted/unknown, never a safe label.
        my $label=$j->{labelExpression};
        need(json_string($label)&&$label=~/\A[a-zA-Z0-9_-]+\z/&&$label ne $node,'other-job placement may reach owner');
    }
    need($seen==1,'exact signing job inventory');
    my $xml=api("/job/$job/config.xml");
    # Compare security fields AND builder bytes to the independently rendered
    # merged candidate. Jenkins may serialize insignificant whitespace differently;
    # this conservative equality can hold for root reconciliation, never rewrite.
    my $rendered=observe(5,65536,'/usr/bin/perl',"$here/bounded-run.pl",'--render',$source);
    $rendered =~ s/<disabled>true<\/disabled>/<disabled>false<\/disabled>/ if defined($grant->{build});
    my $compare='import sys,xml.etree.ElementTree as E\ndef shape(e):\n return (e.tag,sorted(e.attrib.items()),e.text if e.tag=="command" else (e.text or "").strip(),tuple(shape(c) for c in e))\na,b=map(E.fromstring,sys.argv[1:]); assert shape(a)==shape(b)\n';
    $compare =~ s/\\n/\n/g;
    observe(5,128,'/home/benze/projects/happyranch/.venv/bin/python3','-c',$compare,$xml,$rendered);
    my $nodes=$json->decode(api('/computer/api/json?tree=computer[displayName,offline,numExecutors,assignedLabels[name],executors[currentExecutable[number,url]],oneOffExecutors[currentExecutable[number,url]]]'));
    need(ref($nodes) eq 'HASH'&&ref($nodes->{computer}) eq 'ARRAY'&&@{$nodes->{computer}}<=8,'complete bounded node inventory');
    my $owner=0;
    for my $n (@{$nodes->{computer}}) {
        need(ref($n) eq 'HASH'&&json_string($n->{displayName})&&length($n->{displayName})&&json_count($n->{numExecutors})&&JSON::PP::is_bool($n->{offline})&&ref($n->{assignedLabels}) eq 'ARRAY'&&ref($n->{executors}) eq 'ARRAY'&&ref($n->{oneOffExecutors}) eq 'ARRAY','complete typed node facts');
        for my $label (@{$n->{assignedLabels}}) {need(ref($label) eq 'HASH'&&json_string($label->{name})&&length($label->{name}),'complete typed node labels')}
        need(@{$n->{executors}}==$n->{numExecutors},'complete node executor inventory');
        for my $e (@{$n->{executors}},@{$n->{oneOffExecutors}}) {
            need(ref($e) eq 'HASH'&&exists($e->{currentExecutable}),'complete executor observation');
            next unless defined($e->{currentExecutable});
            my $b=$e->{currentExecutable};
            need(ref($b) eq 'HASH'&&json_count($b->{number})&&$b->{number}>0&&json_string($b->{url})&&length($b->{url}),'complete typed running build');
        }
        if ($n->{displayName} eq $node) {
            $owner++; need($n->{numExecutors}==1&&(!defined($grant->{build})||!$n->{offline}),'one-executor owner node; stages require online');
            my @labels=map {$_->{name}} @{$n->{assignedLabels}};
            need(@labels==1&&$labels[0] eq $node,'self-label only');
            my $nc=api("/computer/$node/config.xml");
            need($nc=~m{<mode>EXCLUSIVE</mode>}&&$nc=~m{<numExecutors>1</numExecutors>}&&$nc=~m{<launcher\s+class="hudson.slaves.JNLPLauncher"}&&$nc!~m{<label>[^<]+</label>},'exclusive inbound node config');
            my $expected=$grant->{home}."/Library/Application Support/HappyRanchSigning/agent";
            need($expected!~/[<>&]/&&index($nc,"<remoteFS>$expected</remoteFS>")>=0,'actual owner remoteFS');
            need(!@{$n->{oneOffExecutors}},'no owner one-off workload');
            for my $e (@{$n->{executors}}) {
                next unless defined($e->{currentExecutable});
                my $b=$e->{currentExecutable};
                need(defined($grant->{build})&&$b->{number}==$grant->{build}&&($b->{url}//'')=~m{/job/\Q$job\E/\Q$grant->{build}\E/\z},'only selected owner build');
            }
        } else {
            # No positive no-Keychain-access proof exists for the general node.
            # Require it offline; no node is changed here and idle is insufficient.
            need($n->{numExecutors}==0||$n->{offline},'other executors online: actual distinct UID/no-key-access unresolved');
            for my $e (@{$n->{executors}},@{$n->{oneOffExecutors}}) {need(!defined($e->{currentExecutable}),'other running build holds isolation')}
        }
    }
    need($owner==1,'exact dedicated owner node');
    my $q=$json->decode(api('/queue/api/json?tree=items[id,task[name]]'));
    need(ref($q->{items}) eq 'ARRAY'&&@{$q->{items}}<=64,'complete bounded queue');
    need(!@{$q->{items}},'queue workload holds attended admission');
    my $jar=api('/jnlpJars/agent.jar',1);
    need(length($jar)==1406408&&Digest::SHA::sha256_hex($jar) eq $jar_sha,'current controller remoting pin');
    if (defined($grant->{build})) {
        my $b=$json->decode(api("/job/$job/$grant->{build}/api/json?tree=number,queueId,building,builtOn,actions[parameters[_class,name,value,originalFileName]]"));
        need($b->{number}==$grant->{build}&&$b->{building}&&$b->{builtOn} eq $node&&$b->{queueId}=~/\A[1-9][0-9]*\z/,'actual current running build/queue/node');
        my %params;
        for my $a (@{$b->{actions}//[]}) { for my $v (@{$a->{parameters}//[]}) {need(!exists($params{$v->{name}}),'duplicate build parameter');$params{$v->{name}}=$v} }
        for my $pair ([ACTION=>$grant->{action}],[REQUEST_ID=>$grant->{request}],[RUN_ID=>$grant->{run}]) {need(defined($params{$pair->[0]})&&($params{$pair->[0]}{value}//'') eq $pair->[1],'actual build selected action/request/run')}
        my $u=$params{ORIGINAL_ZIP};
        if ($grant->{action} eq 'preflight') {
            need(ref($u) eq 'HASH'&&$u->{_class} eq 'hudson.model.FileParameterValue'&&$u->{name} eq 'ORIGINAL_ZIP','actual build FileParameterValue');
            $grant->{upload}={type=>'hudson.model.FileParameterValue',name=>'ORIGINAL_ZIP',filename=>'original.zip',size=>49089611,sha256=>'85b96655a1d072328872fa8797e16ad0087e0e05aa30b8dda2a8bc78b60ac243',build=>$grant->{build}};
        } else {need(!defined($u),'later build upload refused');$grant->{upload}=undef}
        $grant->{queue}=0+$b->{queueId};
    }
    native_manager($root,$sid); # Reject session change during observations.
    return $grant;
}
sub ssh_args {
    # Explicit candidate only. inspect reports it unverified; root must establish
    # the actual mapping and pre-existing host/login trust before permission.
    return ('/usr/bin/ssh','-F','/dev/null','-a','-x','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=1','-o','ForwardAgent=no','-p','22','benze@192.168.18.174');
}
sub remote {
    my ($source,$root,$sid,$request)=@_;
    id_args($source,$root,$sid);need($request eq 'session'||$request=~/\A$uuid\z/,'selected observation');
    my $dir=remote_release($source,$root);
    # All interpolated pieces are closed validated tokens or fixed literals.
    return canonical(observe(55,65536,ssh_args(),"/bin/bash -p '$dir/owner-session.sh' controller-observe $source $root $sid $request"));
}
sub local_context {
    my ($interactive)=@_;$interactive=1 unless defined($interactive);
    need($^O eq 'darwin'&&$<!=0&&defined($home)&&(!$interactive||-t STDIN),'attended nonroot native Terminal required');
    need(line(5,128,'/usr/bin/uname','-m') eq 'arm64','native arm64');
    my $user=line(5,128,'/usr/bin/id','-un');need($user=~/\A[a-zA-Z0-9_.-]{1,64}\z/,'actual owner name');
    need(line(5,128,'/usr/bin/stat','-f','%u','/dev/console') eq "$<",'active GUI console owner');
    need(line(5,1024,'/usr/bin/dscl','.','-read',"/Users/$user",'NFSHomeDirectory') eq "NFSHomeDirectory: $home",'DirectoryServices HOME');plain($home);
    need((stat($home))[4]==$<,'owned home');
    my $host=line(5,512,'/bin/hostname');need($host=~/\A[a-zA-Z0-9_.-]{1,253}\z/,'actual host');
    my $java;
    my $java_ok=($mode//'') eq 'refresh' ? 1 : eval {
        my $jh=line(5,2048,'/usr/libexec/java_home','-v','21');plain($jh);$java="$jh/bin/java";plain($java);need(-x $java,'selected Java');
        my $ver=observe(5,1024,'/bin/sh','-c','exec "$1" -version 2>&1','java-version',$java);
        need($ver=~/(?:openjdk|java) version "21\./,'Java21 required');1;
    };
    need($java_ok,'selected Java21 unavailable') unless ($mode//'') eq 'inspect';
    $java=undef unless $java_ok;
    my $root="$home/Library/Application Support/HappyRanchSigning";
    return {host=>$host,user=>$user,uid=>0+$<,home=>$home,java=>$java,root=>$root,node=>$node};
}
sub owned_java {
    my ($expected)=@_;my $ps=observe(5,65536,'/bin/ps','-U',"$<",'-o','pid=,comm=');my @java;
    for my $l (split /\n/,$ps) {need($l=~/\A\s*([0-9]+)\s+(.+)\z/,'bounded owner process identity');push @java,$1 if basename($2) eq 'java'}
    need(defined($expected)?@java==1&&$java[0]==$expected:!@java,'foreign/extra owner-UID Java workload');
}
sub release {
    my ($source,$pins)=@_;need($source=~/\A[0-9a-f]{40}\z/,'merged release');
    closed($pins,@files);
    for my $f (@files) {regular("$here/$f",131072);need($pins->{$f}=~/\A[0-9a-f]{64}\z/&&digest("$here/$f") eq $pins->{$f},'reviewed release file pin')}
}
sub replace_record {
    my ($file,$value)=@_;
    regular($file,65536) if -e $file||-l $file;
    my $pending="$file.pending";save($pending,$json->encode($value)."\n");rename($pending,$file) or die "record replace\n";flush_dir(dirname($file));
}
sub prompt {
    my ($text)=@_;print STDERR "$text\n";my $sel=IO::Select->new(\*STDIN);
    need($sel->can_read(60)&&!$cancel,'attended response timeout');my $v=<STDIN>;need(defined($v)&&length($v)<=128&&!$cancel,'attended input');chomp $v;return $v;
}
sub secret {
    # Read one byte at a time with terminal echo/canonical input disabled. Input
    # never enters a shell variable, argv, environment, file or diagnostic.
    my $term=POSIX::Termios->new();$term->getattr(fileno(STDIN));my $old=$term->getlflag;
    $term->setlflag($old & ~(ECHO|ICANON));$term->setattr(fileno(STDIN),TCSANOW);
    print STDERR "New inbound secret (no echo, 64 lowercase hex, 60s): ";
    my ($s,$done)=('',0);my $ok=eval {
        my $end=mono()+60;$end=$window_end if defined($window_end)&&$window_end<$end;my $sel=IO::Select->new(\*STDIN);
        while (mono()<$end&&!$cancel) {
            next unless $sel->can_read(0.1);my $n=sysread(STDIN,my $c,1);need(defined($n)&&$n==1,'secret EOF');
            if ($c eq "\n") {$done=1;last}need($c=~/[0-9a-f]/&&length($s)<64,'invalid secret input');$s.=$c;
        }
        need($done&&length($s)==64&&!$cancel,'invalid/empty/interrupted secret');1;
    };
    POSIX::tcflush(fileno(STDIN),POSIX::TCIFLUSH());
    $term->setlflag($old);$term->setattr(fileno(STDIN),TCSANOW);print STDERR "\n";
    if (!$ok) {$s="\0"x length($s);die "REFUSED: inbound input; no agent launched\n"}
    return $s;
}
sub java_options {
    return ('-XX:+DisableAttachMechanism','-XX:-UsePerfData','-XX:-HeapDumpOnOutOfMemoryError','-XX:-CreateCoredumpOnCrash','-XX:ErrorFile=/dev/null','-XX:HeapDumpPath=/dev/null','-Djava.util.logging.config.file=/dev/null');
}
sub memory_arguments {
    my @values=@_;
    pipe(my $read,my $write) or die "argument memory pipe\n";
    my $data=join("\n",map {my $v=$_;$v=~s/([\\"])/\\$1/g;'"'.$v.'"'} @values)."\n";
    need(length($data)<=4096,'argument pipe bound');
    print {$write} $data or die "argument pipe write\n";close($write) or die "argument pipe close\n";
    $data="\0"x length($data);return $read;
}
sub launch {
    my ($stdin,@cmd)=@_;my $pid=fork();need(defined($pid),'owned child fork');
    if (!$pid) {
        setpgid(0,0)==0 or POSIX::_exit(126);
        open(STDIN,'<&',$stdin) or POSIX::_exit(126);
        open(STDOUT,'>','/dev/null') or POSIX::_exit(126);open(STDERR,'>','/dev/null') or POSIX::_exit(126);
        exec {$cmd[0]} @cmd or POSIX::_exit(126);
    }
    setpgid($pid,$pid);return $pid;
}
sub reap {
    my ($pid)=@_;return 1 unless defined($pid);
    # The retained process handle is a direct unreaped child; never kill a PID
    # from a record. Recheck before EACH signal, including TERM-to-KILL. Reaping
    # or ECHILD ends authority permanently; kill(0) is observation, not ownership.
    for my $signal (qw(TERM KILL)) {
        my $got=waitpid($pid,WNOHANG);
        return 0 if $got==-1;
        return !kill(0,-$pid) if $got==$pid;
        kill($signal,-$pid); # waitpid==0: original direct child remains unreaped.
        my $end=mono()+2;
        while (mono()<$end) {
            my $n=waitpid($pid,WNOHANG);
            return 0 if $n==-1;
            return !kill(0,-$pid) if $n==$pid;
            sleep 0.05;
        }
    }
    return 0; # No positive reap within the finite budget; retain uncertainty.
}
sub session_record_lock {
    my ($root)=@_;my $path="$root/SESSION/record.lock";
    regular($path,0);sysopen(my $lock,$path,O_RDWR|O_NOFOLLOW) or die "record lock open\n";
    flock($lock,LOCK_EX|LOCK_NB) or die "record update busy; retain hold\n";return $lock;
}
sub refresh {
    my ($source,$request)=@_;need($request=~/\A$uuid\z/,'current request');
    my $ctx=local_context(0);my $root=$ctx->{root};private_dir($root);
    my $s=read_control("$root/owner-session.json");
    need($s->{source} eq $source&&$s->{expires}>time()&&$s->{started}<=time()&&kill(0,$s->{supervisor_pid}),'live original attended window');
    need(!-e "$root/SESSION/CLOSING"&&!-l "$root/SESSION/CLOSING",'attended window closing');
    my $consent=read_control("$root/consents/$request.json");closed($consent,qw(request source root session));
    need($consent->{request} eq $request&&$consent->{source} eq $source&&$consent->{root} eq $s->{root}&&$consent->{session} eq $s->{session},'owner current selected-stage consent');
    owned_java($s->{agent_pid});
    my $obs=remote($source,$s->{root},$s->{session},$request);
    closed($obs,qw(version observed permission pins home));
    need($obs->{version} eq 'happyranch-signing-observation@1'&&$obs->{home} eq $home&&$obs->{observed}<=time()&&$obs->{observed}>=time()-15,'fresh live controller observation');release($source,$obs->{pins});
    my $p=$obs->{permission};closed($p,qw(version delivery_root root task session source action request run job build queue issued expires log_review upload));
    need($p->{source} eq $source&&$p->{root} eq $s->{root}&&$p->{session} eq $s->{session}&&$p->{request} eq $request&&$p->{issued}<=time()&&$p->{expires}>time()&&$p->{expires}<=$s->{expires},'current live permission binding/window');
    for my $k (qw(host user uid home)) {need("$ctx->{$k}" eq "$s->{$k}",'unchanged native owner context')}
    my $record_lock=session_record_lock($root);
    my $current=read_control("$root/owner-session.json");
    need(!-e "$root/SESSION/CLOSING"&&!-l "$root/SESSION/CLOSING"&&$current->{started}==$s->{started}&&$current->{expires}==$s->{expires}&&$current->{expires}>time()&&kill(0,$current->{supervisor_pid}),'unchanged open attended record');
    $s=$current;owned_java($s->{agent_pid});
    $s->{isolation_checked}=0+$obs->{observed};replace_record("$root/owner-session.json",$s);
    my $path="$root/permissions/$request.json";
    if (-e $path||-l $path) {need(bytes($path,65536) eq $json->encode($p)."\n",'permission changed; preserve original')}
    else {save($path,$json->encode($p)."\n")}
}

my $ok=eval {
if (($mode//'') eq 'inspect'&&@args==0) {
    my $c=local_context();my %tools;
    for my $tool (qw(/usr/bin/sw_vers /usr/bin/xcodebuild /usr/bin/xcrun /usr/bin/codesign /usr/bin/ditto /usr/bin/security /usr/bin/syspolicy_check /usr/sbin/spctl /usr/bin/ssh /usr/bin/perl)) {my $present=eval {plain($tool);-x $tool};$tools{$tool}=$present?'present; execution/access unverified':'UNAVAILABLE'}
    $c->{tools}=\%tools;$c->{os}=line(5,2048,'/usr/bin/sw_vers');$c->{xcode}=eval {line(5,2048,'/usr/bin/xcodebuild','-version')}//'UNAVAILABLE';
    $c->{keychain}="$home/Library/Keychains/login.keychain-db";$c->{profile}='HappyRanch-notary';
    $c->{controller_candidate}='benze@192.168.18.174:22; mapping/login/host trust UNVERIFIED';
    $c->{tunnel}='Mac127.0.0.1:18081 -> controller127.0.0.1:8081';
    print $json->encode($c)."\n";
} elsif (($mode//'') eq 'attend'&&@args==3) {
    my ($source,$root,$sid)=@args;id_args($source,$root,$sid);
    my $ctx=local_context();my $base=$ctx->{root};
    need($here eq "$base/release/$source",'fixed native merged release');private_dir($here);
    # Create only named private directories after checking every existing parent.
    plain(dirname($base));
    mkdir($base,0700) or need(-d $base,'owner state root');private_dir($base);
    need(!-e "$base/owner-session.json"&&!-l "$base/owner-session.json"&&!-e "$base/SESSION"&&!-l "$base/SESSION"&&!-e "$base/ACTIVE"&&!-l "$base/ACTIVE",'retained session/partial stage; root reconciliation required');
    # Pinned WorkDirManager validates workDir AND default internalDir before
    # creating caches/log directories when -failIfWorkDirIsMissing is selected.
    for my $dir (qw(inputs runs permissions receipts consents agent agent/remoting)) {
        private_dir(dirname("$base/$dir"));
        mkdir("$base/$dir",0700) or need(-d "$base/$dir",'private selected directory');
        private_dir("$base/$dir");
    }
    owned_java(undef);
    my $obs=remote($source,$root,$sid,'session');closed($obs,qw(version observed permission pins home));
    need($obs->{version} eq 'happyranch-signing-observation@1'&&!defined($obs->{permission})&&$obs->{home} eq $home&&$obs->{observed}<=time()&&$obs->{observed}>=time()-15,'fresh manager session observation');release($source,$obs->{pins});
    my $jar="$base/agent.jar";need(regular($jar,1406408)==1406408&&digest($jar) eq $jar_sha,'exact privately staged controller-matching jar');
    my $manifest=observe(5,8192,'/usr/bin/unzip','-p',$jar,'META-INF/MANIFEST.MF');$manifest=~s/\r\n/\n/g;
    need($manifest=~/^Implementation-Version: \Q$jar_version\E$/m&&$manifest=~/^Main-Class: hudson\.remoting\.Launcher$/m,'exact remoting version/main');
    print STDERR $json->encode({context=>$ctx,source=>$source,root=>$root,session=>$sid,jar_sha256=>$jar_sha,limit_seconds=>1800})."\n";
    need(prompt('After separately granted live setup authority, type ATTEND to start ONLY the fixed tunnel/agent window.') eq 'ATTEND','explicit attended setup consent');
    mkdir("$base/SESSION",0700) or die "retained attended window\n";
    save("$base/SESSION/owner.json",$json->encode({root=>$root,session=>$sid,source=>$source,supervisor_pid=>0+$$,started=>int(time())})."\n");
    save("$base/SESSION/record.lock",'');
    my ($tunnel,$agent);my $normal=0;my $end=mono()+1800;my $started=int(time());$window_end=$end-20;
    my $error;
    eval {
        # Existing-trust SSH only. No ssh config, public listener, new key or
        # authentication fallback. ExitOnForwardFailure rejects occupied port.
        open(my $null,'<','/dev/null') or die "null input\n";
        my @ssh=ssh_args();my $host=pop @ssh;
        $tunnel=launch($null,@ssh,'-N','-T','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18081:127.0.0.1:8081',$host);
        sleep 0.3;need(waitpid($tunnel,WNOHANG)==0,'owned tunnel startup');
        # The root's pinned JAR was checked through its existing credential route;
        # no authenticated download/agent secret is requested by this source task.
        my $secret=secret();
        my @argv=(java_options(),'-jar',$jar,'-url','http://127.0.0.1:18081/','-webSocket','-noReconnect','-name',$node,'-workDir',"$base/agent",'-agentLog','/dev/null','-failIfWorkDirIsMissing','-secret',$secret);
        my $arg_read=memory_arguments(@argv);
        $secret="\0"x length($secret);$argv[-1]='';
        chdir("$base/agent") or die "fixed agent cwd\n";
        $agent=launch($arg_read,$ctx->{java},'@/dev/stdin');close($arg_read);
        my $s={version=>'happyranch-signing-owner@2',source=>$source,host=>$ctx->{host},user=>$ctx->{user},uid=>0+$<,home=>$home,node=>$node,agent_pid=>0+$agent,supervisor_pid=>0+$$,root=>$root,session=>$sid,started=>$started,expires=>$started+1800,isolation_checked=>0,isolation=>'exclusive-one-owner-workload',profile_checked=>'UNVERIFIED'};
        # No stage is admitted by session launch alone. Each request needs owner
        # consent plus fresh actual controller/allocation observations at --gate.
        save("$base/owner-session.json",$json->encode($s)."\n");
        print STDERR "Attended window active. Type consent REQUEST_UUID before the root's one exact build, profile REQUEST_UUID for a separately authorized existing-profile check, or stop. Native profile proof remains separately required.\n";
        my $sel=IO::Select->new(\*STDIN);
        while (mono()<$window_end&&!$cancel) {
            need(waitpid($agent,WNOHANG)==0&&waitpid($tunnel,WNOHANG)==0,'owned child ended unexpectedly');
            owned_java($agent);
            if ($sel->can_read(1)) {
                my $input=<STDIN>;need(defined($input)&&length($input)<=128,'attended command EOF/size');chomp $input;
                if ($input eq 'stop') {$normal=1;last}
                if ($input=~/\Aprofile ($uuid)\z/) {
                    my $request=$1;my $proof=remote($source,$root,$sid,$request);
                    my $p=$proof->{permission};
                    need($proof->{home} eq $home&&$proof->{observed}<=time()&&$proof->{observed}>=time()-15&&ref($p) eq 'HASH'&&$p->{action} eq 'profile-check'&&$p->{source} eq $source&&$p->{root} eq $root&&$p->{session} eq $sid&&$p->{request} eq $request&&$p->{issued}<=time()&&$p->{expires}>time()&&$p->{expires}<=$s->{expires},'current separately authorized profile check');
                    release($source,$proof->{pins});owned_java($agent);
                    my $keychain="$home/Library/Keychains/login.keychain-db";regular($keychain,undef);
                    local $ENV{HR_DEADLINE}=$end-10;
                    need(command('profile-check',120,262144,"$base/SESSION/profile-$request",'/usr/bin/xcrun','notarytool','history','--keychain-profile','HappyRanch-notary','--keychain',$keychain,'--output-format','json')==0,'actual existing-profile bounded check');
                    # Retain private response, publish no history/credentials.
                    my $record_lock=session_record_lock($base);
                    my $current=read_control("$base/owner-session.json");
                    need($current->{expires}>time()&&$p->{expires}>time()&&$current->{session} eq $sid&&!$cancel,'unchanged live profile-check window');
                    $current->{profile_checked}='actual-authorized-history-exit0';replace_record("$base/owner-session.json",$current);
                    print STDERR "Existing profile check exited0; private-key use/signing remains unverified.\n";next;
                }
                need($input=~/\Aconsent ($uuid)\z/,'only consent REQUEST or stop');
                save("$base/consents/$1.json",$json->encode({request=>$1,source=>$source,root=>$root,session=>$sid})."\n");
                print STDERR "Local consent retained; genuine current manager grant and actual controller build still required.\n";
            }
        }
        need($normal&&!$cancel,'attended timeout/cancel; retain holds');
        1;
    } or $error=$@||'attended failure';
    # Always close admission BEFORE teardown, even when child launch/input fails.
    eval {save("$base/SESSION/CLOSING","closing\n");my $record_lock=session_record_lock($base);if (-e "$base/owner-session.json") {my $s=read_control("$base/owner-session.json");$s->{expires}=int(time());replace_record("$base/owner-session.json",$s)};1} or $error ||= 'admission close failed';
    my $agent_settled=reap($agent);my $tunnel_settled=reap($tunnel);
    save("$base/SESSION/settlement.json",$json->encode({agent_settled=>$agent_settled?JSON::PP::true:JSON::PP::false,tunnel_settled=>$tunnel_settled?JSON::PP::true:JSON::PP::false,outcome=>!$error&&$agent_settled&&$tunnel_settled&&!-e "$base/ACTIVE"?'closed':'FAILED_OR_UNKNOWN'})."\n");
    need(!$error&&$agent_settled&&$tunnel_settled&&!-e "$base/ACTIVE",'session incomplete/unsettled; retained state, no relaunch');
    print "Owned attended children reaped; retained window closed. Owner may lock login Keychain in UI.\n";
} elsif (($mode//'') eq 'controller-grant'&&@args==8) {
    my ($source,$root,$sid,$request,$action,$run,$build,$owner_home)=@args;id_args($source,$root,$sid);
    need($request eq 'session'||$request=~/\A$uuid\z/,'request syntax');
    need($owner_home=~m{\A/Users/[a-zA-Z0-9_.-]+\z},'inspected native owner home');native_manager($root,$sid);
    my $dir=control_dir($root);private_dir(dirname($dir));
    mkdir($dir,0700) or need(-d $dir,'control directory');private_dir($dir);
    my %pins=map {$_=>digest("$here/$_")} @files;release($source,\%pins);
    my $g={version=>'happyranch-signing-grant@1',source=>$source,root=>$root,session=>$sid,home=>$owner_home,pins=>\%pins,issued=>int(time()),expires=>int(time())+1800,permission=>undef};
    if ($request ne 'session') {
        need($action eq 'profile-check'?($run eq '-'&&$build eq '-'):(exists($budget{$action})&&$build=~/\A[1-9][0-9]*\z/&&($action eq 'preflight'?$run eq '-':$run=~/\A$uuid\z/)),'stage selection');
        my $s=read_control("$dir/session.json");need($s->{source} eq $source&&$s->{session} eq $sid&&$s->{home} eq $owner_home&&$s->{expires}>time(),'original current manager session grant');
        $g->{expires}=$s->{expires};
        my $until=int(time())+($action eq 'profile-check'?120:$budget{$action});$until=$s->{expires} if $s->{expires}<$until;
        my $review;
        if ($action eq 'finalize') {
            # Prepared by the genuine root AFTER actual full-log/warning review,
            # not by the owner or a scalar build parameter. Existing closed reader
            # and later real-log comparison continue to enforce its content.
            $review=read_control("$dir/$request.review.json");closed($review,qw(sha256 submission_id inspected_complete warnings_disposition safe_to_publish));
        }
        $g->{permission}={version=>'happyranch-signing-permission@2',delivery_root=>'TASK-14158',root=>$root,task=>$root,session=>$sid,source=>$source,action=>$action,request=>$request,run=>$run eq '-'?'':$run,job=>$job,build=>$build eq '-'?undef:0+$build,queue=>undef,issued=>int(time()),expires=>$until,log_review=>$review,upload=>undef};
    } else {need($action eq '-'&&$run eq '-'&&$build eq '-','session grant has no stage')}
    # Explicit genuine manager invocation is the authorization producer. It is
    # not usable on the source maker's SID, nor does source approval permit it.
    save("$dir/$request.json",$json->encode($g)."\n");print "Current manager grant retained; owner consent/live observations still required.\n";
} elsif (($mode//'') eq 'controller-observe'&&@args==4) {
    my ($source,$root,$sid,$request)=@args;id_args($source,$root,$sid);need($request eq 'session'||$request=~/\A$uuid\z/,'request');
    my $g=read_control(control_dir($root)."/$request.json");closed($g,qw(version source root session home pins issued expires permission));
    need($g->{version} eq 'happyranch-signing-grant@1'&&$g->{source} eq $source&&$g->{root} eq $root&&$g->{session} eq $sid&&$g->{issued}<=time()&&$g->{expires}>time()&&$g->{expires}-$g->{issued}<=1800,'current original grant');release($source,$g->{pins});
    my $p=$g->{permission};my $selected={home=>$g->{home}};
    if ($request ne 'session') {need(ref($p) eq 'HASH'&&$p->{request} eq $request&&$p->{expires}>time(),'current selected grant');$selected={%$p,home=>$g->{home}}}
    my $observed=int(time());
    $selected=fresh_controller($source,$root,$sid,$selected);
    if (defined($p)) {$p->{queue}=$selected->{queue};$p->{upload}=$selected->{upload}}
    print $json->encode({version=>'happyranch-signing-observation@1',observed=>$observed,permission=>$p,pins=>$g->{pins},home=>$g->{home}})."\n";
} elsif (($mode//'') eq 'refresh'&&@args==2) {refresh(@args)}
else {die "Usage: owner-session.sh inspect | attend SOURCE CURRENT_ROOT CURRENT_MANAGER_SID | refresh SOURCE REQUEST | controller-grant SOURCE ROOT SID REQUEST|session ACTION|- RUN|- BUILD|- INSPECTED_HOME | controller-observe SOURCE ROOT SID REQUEST|session\n"}
1;
};
if (!$ok) {my $error=$@;print STDERR "Boundary: $1\n" if $error=~/\AREFUSED: ([a-zA-Z0-9 \/;_.@:-]{1,128})\n/;print STDERR "Owner session REFUSED or FAILED_OR_UNKNOWN; retain state; root reconciliation required.\n";exit 1}
exit 0;

__END__
OWNER_PERL
