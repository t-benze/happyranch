// Install reviewed inline CpsFlowDefinition bytes with the manager's literal
// THR211_INSTALL binding prepended. Never evaluate a moving SCM ref.
def prepareShell = '''#!/bin/bash -p
(
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin
reject() { printf 'THR211 refused: %s\n' "$1" >&2; exit 64; }
literal_directory() {
  local rest="$1" current='' component
  [[ "$rest" == /* && "$rest" != / && "$rest" != */ && "$rest" != *//* ]] || return 1
  rest=${rest#/}
  while [[ -n "$rest" ]]; do
    component=${rest%%/*}
    [[ "$component" != . && "$component" != .. ]] || return 1
    current="$current/$component"
    [[ ! -L "$current" && -d "$current" ]] || return 1
    if [[ "$rest" == */* ]]; then rest=${rest#*/}; else rest=''; fi
  done
}
# Expected values come ONLY from the installed literal binding, not parameters.
for name in REQUEST MODE SOURCE PIPELINE; do
  requested="REQUESTED_$name"; admitted="ADMITTED_$name"
  [[ -n "${!admitted:-}" && "${!requested:-}" == "${!admitted}" ]] || reject "binding-$name"
done
[[ "$ADMITTED_REQUEST" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || reject request
[[ "$ADMITTED_MODE" == SETUP || "$ADMITTED_MODE" == ABORT || "$ADMITTED_MODE" == DIAGNOSTIC ]] || reject mode
[[ "$ADMITTED_SOURCE" =~ ^[0-9a-f]{40}$ && "$ADMITTED_PIPELINE" =~ ^[0-9a-f]{64}$ ]] || reject digest
[[ "$ADMITTED_LOCK" =~ ^[0-9a-f]{64}$ && "$ADMITTED_CONFIG" =~ ^[0-9a-f]{64}$ ]] || reject config
[[ "$ADMITTED_NODE" == "$NODE_NAME" && "$ADMITTED_ACCOUNT" == "$(/usr/bin/id -un)" ]] || reject venue
[[ "$ADMITTED_START" =~ ^[1-9][0-9]{9}$ && "$ADMITTED_EXPIRY" =~ ^[1-9][0-9]{9}$ ]] || reject window
now=$(/bin/date -u +%s)
(( ADMITTED_START <= now && now < ADMITTED_EXPIRY && ADMITTED_EXPIRY - ADMITTED_START <= 4200 && ADMITTED_EXPIRY - now >= 3000 )) || reject expired-window
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || reject build
literal_directory "$WORKSPACE" || reject workspace-ancestry
for tool in "$ADMITTED_PYTHON" "$ADMITTED_UV" "$ADMITTED_GIT"; do
  [[ "$tool" == /* && -x "$tool" && ! -d "$tool" ]] || reject absolute-tool
  literal_directory "${tool%/*}" || reject tool-ancestry
done
# Create EVERY private directory before Python/uv/plugins/Git/checkout code.
root="$WORKSPACE/thr211-$BUILD_NUMBER"
/bin/mkdir -m 700 "$root" || reject root-acquisition
for leaf in home xdg-config xdg-cache xdg-state xdg-runtime tmp uv-cache venv daemon-home plans artifacts bin; do
  /bin/mkdir -m 700 "$root/$leaf" || reject directory-acquisition
done
printf 'write-ready\n' > "$root/tmp/probe" || reject write-probe
[[ "$(< "$root/tmp/probe")" == write-ready ]] || reject write-readback
publication_code=\'import json
import os
import subprocess
import sys

mode, root, nonce = sys.argv[1:4]
fds = []
errors = []

def directory(path, expected=None):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fds.append(fd)
    identities = []
    for component in path.split("/")[1:]:
        if component in ("", ".", ".."):
            raise ValueError("noncanonical publication path")
        fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        fds.append(fd)
        info = os.fstat(fd)
        identities.append([info.st_dev, info.st_ino])
    if expected is not None and identities != expected:
        raise ValueError("publication ancestry identity changed")
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("nonprivate publication directory")
    return fd, identities

def write(fd, name, data):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, "w") as stream:
        stream.write(data)

result = None
try:
    if mode == "prepare":
        fd, identities = directory(root + "/artifacts")
        result = dict(nonce=nonce, root=root, ancestry=identities, errors=errors)
        log = os.open("setup.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(log, "w") as stream:
            child = subprocess.run(["/bin/bash", "-p", "-s", "--", *sys.argv[4:]], stdin=sys.stdin, stdout=stream, stderr=subprocess.STDOUT)
            result["exit"] = child.returncode if child.returncode >= 0 else 128 - child.returncode
        primary = result["exit"]
        try:
            write(fd, "shell-result.json", json.dumps(dict(primary_exit=primary, pytest_exit=None, cleanup="UNKNOWN", observer="UNAVAILABLE", held="F04")))
        except Exception as error:
            errors.append("shell-receipt:" + type(error).__name__ + ":" + str(error))
    elif mode == "publish":
        acquired = json.loads(sys.argv[4])
        if acquired["nonce"] != nonce or acquired["root"] != root:
            raise ValueError("invocation acquisition mismatch")
        fd, identities = directory(root + "/artifacts", acquired["ancestry"])
        write(fd, "pipeline.json", sys.argv[5])
        result = dict(nonce=nonce, root=root, published=True, errors=errors)
    else:
        raise ValueError("unknown publication mode")
except Exception as error:
    errors.append("publication:" + type(error).__name__ + ":" + str(error))
    if result is None:
        result = dict(nonce=nonce, root=root, errors=errors)
finally:
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError as error:
            errors.append("close:" + type(error).__name__ + ":" + str(error))
print("THR211_ACQUIRED=" + json.dumps(result), flush=True)
if mode == "prepare":
    sys.exit(result.get("exit", 1))
sys.exit(0)  # Return every publication error through stdout.
\'
# -p ignores BASH_ENV. Jenkins cookie is metadata, NOT process ownership.
exec /usr/bin/env -i PATH="$root/bin:/usr/bin:/bin" HOME="$root/home" \
  XDG_CONFIG_HOME="$root/xdg-config" XDG_CACHE_HOME="$root/xdg-cache" \
  XDG_STATE_HOME="$root/xdg-state" XDG_DATA_HOME="$root/xdg-state" \
  XDG_RUNTIME_DIR="$root/xdg-runtime" TMPDIR="$root/tmp" TMP="$root/tmp" TEMP="$root/tmp" \
  UV_CACHE_DIR="$root/uv-cache" UV_PROJECT_ENVIRONMENT="$root/venv/env" \
  UV_PYTHON_DOWNLOADS=never UV_NO_CONFIG=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  GIT_TERMINAL_PROMPT=0 GIT_ALLOW_PROTOCOL=https \
  HAPPYRANCH_DAEMON_HOME="$root/daemon-home" HAPPYRANCH_DAEMON_PORT=0 \
  JENKINS_NODE_COOKIE="${JENKINS_NODE_COOKIE:-}" \
  "$ADMITTED_PYTHON" -I -c "$publication_code" prepare "$root" "$PUBLICATION_NONCE" "$root" "$ADMITTED_SOURCE" "$ADMITTED_PIPELINE" "$ADMITTED_LOCK" \
  "$ADMITTED_PYTHON" "$ADMITTED_UV" "$ADMITTED_GIT" "$ADMITTED_ARCH" \
  "$ADMITTED_REQUEST" "$ADMITTED_MODE" "$ADMITTED_CONFIG" <<'THR211_PRIVATE'
set -euo pipefail
umask 077
root=$1; source_sha=$2; pipeline_sha=$3; lock_sha=$4
python=$5; uv=$6; git=$7; native_arch=$8; request=$9; mode=${10}; config=${11}
printf 'request=%s mode=%s source=%s pipeline=%s config=%s\n' "$request" "$mode" "$source_sha" "$pipeline_sha" "$config"
printf 'python=%s uv=%s git=%s native_arch=%s\n' "$python" "$uv" "$git" "$native_arch"
"$git" clone --no-checkout -- https://github.com/t-benze/happyranch.git "$root/source"
"$git" -C "$root/source" fetch --no-tags origin "$source_sha"
"$git" -C "$root/source" checkout --detach "$source_sha"
[[ "$("$git" -C "$root/source" rev-parse HEAD)" == "$source_sha" ]]
[[ "$("$git" -C "$root/source" symbolic-ref -q HEAD || :)" == '' ]]
[[ "$("$git" -C "$root/source" status --porcelain)" == '' ]]
"$git" -C "$root/source" rev-parse 'HEAD^{tree}'
cd "$root/source"
"$python" -I - "$pipeline_sha" "$lock_sha" "$native_arch" <<'THR211_IDENTITY'
import hashlib, pathlib, platform, sys
assert sys.version_info[:2] == (3, 12), "Python3.12 required"
assert platform.machine() == sys.argv[3], "native architecture mismatch"
for name, expected in zip(("Jenkinsfile", "uv.lock"), sys.argv[1:3]):
    assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == expected, name
print("native", sys.executable, sys.version, platform.machine(), flush=True)
THR211_IDENTITY
"$uv" --version
"$uv" sync --frozen --python "$python"
/bin/chmod 700 "$root/venv/env"
/bin/ln -s "$uv" "$root/bin/uv"
"$root/venv/env/bin/python" -I - "$root/source" "$root" "$python" "$native_arch" <<'THR211_ORIGINS'
import pathlib, sys
source = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(source))  # -I omits cwd: arrange source explicitly.
from tests.thr211_containment import prepare_pipeline_environment
prepare_pipeline_environment(source, pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4])
THR211_ORIGINS
# No caller receipt or boolean can release this unresolved F04 gate.
printf 'HELD: independent daemon/descendant lifetime and observer not proved\n' >&2
exit 78
THR211_PRIVATE
)
status=$?
printf '\nTHR211_EXIT=%s\n' "$status"
exit 0
'''

def publishShell = '''#!/bin/bash -p
set -eu
root="$PUBLICATION_ROOT"
a="$root"
while [ "$a" != / ]; do
  [ ! -L "$a" ] && [ -d "$a" ] || exit 64
  a=$(/usr/bin/dirname "$a")
done
for leaf in home xdg-config xdg-cache xdg-state xdg-runtime tmp uv-cache venv daemon-home; do
  [ ! -L "$root/$leaf" ] && [ -d "$root/$leaf" ] && [ -O "$root/$leaf" ] || exit 64
done
exec /usr/bin/env -i HOME="$root/home" XDG_CONFIG_HOME="$root/xdg-config" XDG_CACHE_HOME="$root/xdg-cache" \
 XDG_STATE_HOME="$root/xdg-state" XDG_DATA_HOME="$root/xdg-state" XDG_RUNTIME_DIR="$root/xdg-runtime" \
 TMPDIR="$root/tmp" TMP="$root/tmp" TEMP="$root/tmp" UV_CACHE_DIR="$root/uv-cache" \
 UV_PROJECT_ENVIRONMENT="$root/venv/env" HAPPYRANCH_DAEMON_HOME="$root/daemon-home" \
 PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "$PUBLICATION_PYTHON" -I -c \'import json
import os
import subprocess
import sys

mode, root, nonce = sys.argv[1:4]
fds = []
errors = []

def directory(path, expected=None):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fds.append(fd)
    identities = []
    for component in path.split("/")[1:]:
        if component in ("", ".", ".."):
            raise ValueError("noncanonical publication path")
        fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        fds.append(fd)
        info = os.fstat(fd)
        identities.append([info.st_dev, info.st_ino])
    if expected is not None and identities != expected:
        raise ValueError("publication ancestry identity changed")
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("nonprivate publication directory")
    return fd, identities

def write(fd, name, data):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, "w") as stream:
        stream.write(data)

result = None
try:
    if mode == "prepare":
        fd, identities = directory(root + "/artifacts")
        result = dict(nonce=nonce, root=root, ancestry=identities, errors=errors)
        log = os.open("setup.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(log, "w") as stream:
            child = subprocess.run(["/bin/bash", "-p", "-s", "--", *sys.argv[4:]], stdin=sys.stdin, stdout=stream, stderr=subprocess.STDOUT)
            result["exit"] = child.returncode if child.returncode >= 0 else 128 - child.returncode
        primary = result["exit"]
        try:
            write(fd, "shell-result.json", json.dumps(dict(primary_exit=primary, pytest_exit=None, cleanup="UNKNOWN", observer="UNAVAILABLE", held="F04")))
        except Exception as error:
            errors.append("shell-receipt:" + type(error).__name__ + ":" + str(error))
    elif mode == "publish":
        acquired = json.loads(sys.argv[4])
        if acquired["nonce"] != nonce or acquired["root"] != root:
            raise ValueError("invocation acquisition mismatch")
        fd, identities = directory(root + "/artifacts", acquired["ancestry"])
        write(fd, "pipeline.json", sys.argv[5])
        result = dict(nonce=nonce, root=root, published=True, errors=errors)
    else:
        raise ValueError("unknown publication mode")
except Exception as error:
    errors.append("publication:" + type(error).__name__ + ":" + str(error))
    if result is None:
        result = dict(nonce=nonce, root=root, errors=errors)
finally:
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError as error:
            errors.append("close:" + type(error).__name__ + ":" + str(error))
print("THR211_ACQUIRED=" + json.dumps(result), flush=True)
if mode == "prepare":
    sys.exit(result.get("exit", 1))
sys.exit(0)  # Return every publication error through stdout.
\' publish "$PUBLICATION_ROOT" "$PUBLICATION_NONCE" "$PUBLICATION_ACQUIRED" "$PUBLICATION_RECEIPT"
'''

if (!binding.hasVariable('THR211_INSTALL')) error('HELD: manager-installed configuration absent')
def installed = binding.getVariable('THR211_INSTALL')
def keys = ['REQUEST', 'MODE', 'SOURCE', 'PIPELINE', 'LOCK', 'CONFIG', 'NODE',
            'ACCOUNT', 'START', 'EXPIRY', 'PYTHON', 'UV', 'GIT', 'ARCH']
if (!(installed instanceof Map) || installed.keySet() != keys.toSet() ||
    keys.any { !(installed[it] instanceof String) || installed[it].contains('\n') || installed[it].contains('\r') }) {
  error('HELD: malformed installed configuration')
}
properties([disableConcurrentBuilds(), parameters([
  choice(name: 'MODE', choices: ['SETUP', 'ABORT', 'DIAGNOSTIC']),
  string(name: 'REQUEST_ID', defaultValue: ''), string(name: 'SOURCE_SHA', defaultValue: ''),
  string(name: 'PIPELINE_SHA', defaultValue: '')
])])
// Scripted Pipeline has no implicit checkout. Do not add checkout scm or agent.
// This watchdog bounds allocation ONLY, without wrapping node's later work.
def allocated = false
def executionEnded = false
def receipt = [pytest_exit: null, result: 'UNKNOWN', cleanup: 'UNKNOWN', observer: 'UNAVAILABLE', errors: []]
try {
  parallel allocation: {
    timeout(time: 15, unit: 'MINUTES') {
      while (!allocated && !executionEnded) sleep(time: 1, unit: 'SECONDS')
    }
  }, execution: {
    try {
      node(installed.NODE) {
        allocated = true
        receipt.workspace = pwd()
        receipt.request = params.REQUEST_ID
        receipt.source = params.SOURCE_SHA
        receipt.pipeline = params.PIPELINE_SHA
        def primaryFailure = null
        def acquisition = null
        def nonce = java.util.UUID.randomUUID().toString()
        try {
          stage('Private frozen preparation') {
            timeout(time: 15, unit: 'MINUTES') {
              withEnv(keys.collect { "ADMITTED_${it}=${installed[it]}" } + [
                "PUBLICATION_NONCE=${nonce}", "REQUESTED_REQUEST=${params.REQUEST_ID}", "REQUESTED_MODE=${params.MODE}",
                "REQUESTED_SOURCE=${params.SOURCE_SHA}", "REQUESTED_PIPELINE=${params.PIPELINE_SHA}"
              ]) {
                def output = sh(script: prepareShell, returnStdout: true)
                def exits = output.readLines().findAll { it.startsWith('THR211_EXIT=') }
                if (exits.size() == 1 && exits[0].substring(12) ==~ /[0-9]{1,3}/) {
                  receipt.setup_exit = exits[0].substring(12).toInteger()
                }
                def records = output.readLines().findAll { it.startsWith('THR211_ACQUIRED=') }
                try {
                 if (records.size() == 1) {
                  def candidate = new groovy.json.JsonSlurperClassic().parseText(records[0].substring(16))
                  receipt.errors.addAll(candidate.errors ?: [])
                  if (candidate.nonce == nonce && candidate.root == "${receipt.workspace}/thr211-${env.BUILD_NUMBER}" &&
                      candidate.ancestry instanceof List && candidate.ancestry &&
                      candidate.exit == receipt.setup_exit && receipt.setup_exit != null) acquisition = candidate
                 }
                } catch (Throwable e) { receipt.errors.add('acquisition:' + e.getClass().getSimpleName()) }
              }
              if (receipt.setup_exit != 0) error("preparation exit ${receipt.setup_exit}; F04 held")
            }
          }
          stage('Workload') {
            timeout(time: 30, unit: 'MINUTES') {
              // A future reviewed F04 release must wire the owned observer first:
              // uv run --frozen pytest tests/integration/ -v -m integration --junitxml=artifacts/integration.xml
              error('HELD: fixture lifetime proof absent; pytest unrun')
            }
          }
        } catch (Throwable primary) {
          receipt.result = primary.getClass().getSimpleName() == 'FlowInterruptedException' ? 'ABORTED' : 'FAILURE'
          receipt.primary = primary.getClass().getSimpleName()
          primaryFailure = primary
        } finally {
          try {
            timeout(time: 5, unit: 'MINUTES') {
              // A shell status or on-disk marker never establishes acquisition.
              if (acquisition == null) {
                receipt.errors.add('publication:workspace-root-unverified')
              } else {
                def published = false
                try {
                  withEnv(["PUBLICATION_PYTHON=${installed.PYTHON}", "PUBLICATION_ROOT=${acquisition.root}",
                           "PUBLICATION_NONCE=${nonce}", "PUBLICATION_ACQUIRED=${groovy.json.JsonOutput.toJson(acquisition)}",
                           "PUBLICATION_RECEIPT=${groovy.json.JsonOutput.toJson(receipt)}"]) {
                    // Actual writer traverses with O_NOFOLLOW, checks every saved
                    // inode and creates exclusively relative to its open directory.
                    def output = sh(script: publishShell, returnStdout: true)
                    def record = new groovy.json.JsonSlurperClassic().parseText(output.trim().substring(16))
                    receipt.errors.addAll(record.errors ?: [])
                    published = record.published == true && record.nonce == nonce && record.root == acquisition.root
                  }
                } catch (Throwable e) { receipt.errors.add('receipt:' + e.getClass().getSimpleName()) }
                if (published) {
                  try {
                    archiveArtifacts(artifacts: "thr211-${env.BUILD_NUMBER}/artifacts/pipeline.json", allowEmptyArchive: false, followSymlinks: false)
                  } catch (Throwable e) { receipt.errors.add('receipt-archive:' + e.getClass().getSimpleName()) }
                  try {
                    archiveArtifacts(artifacts: "thr211-${env.BUILD_NUMBER}/artifacts/**", allowEmptyArchive: false, followSymlinks: false)
                  } catch (Throwable e) { receipt.errors.add('artifacts:' + e.getClass().getSimpleName()) }
                } else {
                  receipt.errors.add('publication:writer-unverified;archives-skipped')
                }
              }
            }
          } catch (Throwable e) { receipt.errors.add('cleanup-publication:' + e.getClass().getSimpleName()) }
          // Console is also collected by the independent durable observer. The
          // archived initial receipt cannot contain errors occurring after it.
          try {
            echo groovy.json.JsonOutput.toJson(receipt)
          } catch (Throwable e) { receipt.errors.add('console:' + e.getClass().getSimpleName()) }
          if (receipt.errors) currentBuild.result = 'FAILURE'
        }
        if (primaryFailure != null) {
          receipt.errors.each { primaryFailure.addSuppressed(new RuntimeException(it.toString())) }
          throw primaryFailure
        }
        if (receipt.errors) error('cleanup/publication failed; see every secondary error')
      }
    } finally { executionEnded = true }
  }, failFast: true
} catch (Throwable primary) {
  // Without allocation there is no workspace to archive. Observer reads console.
  if (!allocated) {
    try {
      echo groovy.json.JsonOutput.toJson([result: 'ALLOCATION_FAILED', workspace: null,
          pytest_exit: null, errors: [primary.getClass().getSimpleName()]])
    } catch (Throwable e) { currentBuild.result = 'FAILURE' }
  }
  throw primary
}
