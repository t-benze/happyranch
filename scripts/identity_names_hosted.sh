#!/usr/bin/env bash
# One finite naming operation on a disposable GitHub runner; Docker stays outside.
set -euo pipefail
naming_sha=${1:?published 40-character source SHA required}
naming_image=${2:?reviewed node24.19.0-bookworm linux/amd64 digest required}
[[ $naming_sha =~ ^[0-9a-f]{40}$ ]]
[[ $naming_image =~ ^node@sha256:[0-9a-f]{64}$ ]]
[[ $(git rev-parse HEAD) == "$naming_sha" ]]
[[ -z $(git status --porcelain) ]]
command -v docker >/dev/null
command -v uv >/dev/null
command -v timeout >/dev/null
# Job start is recorded before checkout/tool setup. Leave three minutes for upload
# and runner overhead. Acquisition, stages and teardown share this deadline.
naming_started=${NAMING_JOB_STARTED:?job start timestamp required}
[[ $naming_started =~ ^[0-9]+$ ]]
naming_deadline=$((naming_started+1620))
naming_tag="naming-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
naming_scratch="${naming_tag}-scratch"
naming_artifacts="${naming_tag}-artifacts"
naming_holder="${naming_tag}-holder"
naming_init="${naming_tag}-init"
naming_prepare="${naming_tag}-prepare"
naming_work="${naming_tag}-work"
naming_dir=$(mktemp -d "${RUNNER_TEMP:?}/${naming_tag}.XXXXXX")
naming_destination="$RUNNER_TEMP/naming-evidence"
mkdir -p "$naming_destination"
naming_exit=1
naming_cleanup_failed=0
naming_export_failed=0
naming_scratch_created=0
naming_artifacts_created=0
# Capture complete bounded output, never stream the unbounded shared helper to
# Actions. Overflow is explicit, nonzero, and leaves the exact owned Docker
# container for deadline-bounded cleanup. Rotated Docker logs are diagnostic only.
cat > "$naming_dir/capture.py" <<'CAPTURE'
import os, signal, subprocess, sys
from pathlib import Path
limit=int(sys.argv[1]); destination=Path(sys.argv[2]); command=sys.argv[3:]
p=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
assert p.stdout is not None
used=0; capped=False
with destination.open('wb') as output:
    while True:
        data=p.stdout.read1(65536)
        if not data: break
        room=limit-used
        if len(data)>room:
            output.write(data[:max(0,room)])
            output.write(b'\n[NAMING OUTPUT CAP EXCEEDED; INCOMPLETE REQUIRED EVIDENCE]\n')
            capped=True
            os.killpg(p.pid,signal.SIGKILL)
            break
        output.write(data); used+=len(data)
status=p.wait()
# This control result is small even if the child was noisy.
print(f'naming control {destination.name}: exit={status} incomplete={capped}')
raise SystemExit(125 if capped else status if status>=0 else 128-status)
CAPTURE
naming_call() {
  local requested=$1 cap=$2 file=$3 remaining
  shift 3
  remaining=$((naming_deadline-$(date +%s)-1))
  (( remaining > 0 )) || { printf 'deadline exhausted: %s\n' "$file" >&2; return 124; }
  (( requested <= remaining )) || requested=$remaining
  python3 "$naming_dir/capture.py" "$cap" "$naming_destination/$file" \
    timeout --signal=TERM --kill-after=1s "${requested}s" "$@"
}
naming_stage() {
  local requested=$1 remaining
  shift
  remaining=$((naming_deadline-$(date +%s)-120-1))
  (( remaining > 0 )) || return 124
  (( requested <= remaining )) || requested=$remaining
  naming_call "$requested" 8388608 "$@"
}
naming_absent() {
  # Unlike inspect exit1, a successful filtered list proves owned-name absence;
  # Docker/service/time/output errors never masquerade as absence.
  local kind=$1 name=$2 file=$3
  naming_call 1 65536 "$file" docker "$kind" ls -q --filter "name=^${name}$" || return
  [[ ! -s "$naming_destination/$file" ]]
}
naming_clean() {
  local original=$? child exists
  set +e
  trap - EXIT TERM INT
  # Preserve the first stage/workload error even if later cleanup/export fails.
  if (( naming_exit == 1 && original != 0 )); then naming_exit=$original; fi
  for child in "$naming_init" "$naming_prepare" "$naming_work"; do
    if naming_call 1 65536 "${child}-exists.txt" docker container ls -aq --filter "name=^${child}$"; then
      exists=$(cat "$naming_destination/${child}-exists.txt")
      if [[ -n $exists ]]; then
        naming_call 4 65536 "${child}-stop.txt" docker stop --time 2 "$child" || naming_cleanup_failed=1
        naming_call 1 65536 "${child}-final.json" docker container inspect "$child" || naming_cleanup_failed=1
        naming_call 1 65536 "${child}-state.txt" docker inspect --format '{{.State.Running}}:{{.State.Pid}}' "$child" || naming_cleanup_failed=1
        [[ $(cat "$naming_destination/${child}-state.txt") == 'false:0' ]] || naming_cleanup_failed=1
        naming_call 2 2097152 "${child}-diagnostic.log" docker logs "$child" || naming_cleanup_failed=1
        naming_call 1 65536 "${child}-remove.txt" docker rm "$child" || naming_cleanup_failed=1
        naming_absent container "$child" "${child}-absent.txt" || naming_cleanup_failed=1
      fi
    else naming_cleanup_failed=1; fi
  done
  # The live holder keeps BOTH local-driver mounts present until export ends.
  if naming_call 1 65536 holder-state.txt docker inspect --format '{{.State.Running}}' "$naming_holder"; then
    if [[ $(cat "$naming_destination/holder-state.txt") == true ]]; then
      naming_call 1 65536 holder-boundary.txt docker exec "$naming_holder" bash -euc \
        '[[ $(</scratch/source-sentinel) == "$1" && $(</evidence/evidence-sentinel) == "$1" ]]; printf "export sentinels retained %s\n" "$1"' naming-export "$naming_sha" || naming_export_failed=1
      naming_call 5 65536 fixture-export.txt docker exec "$naming_holder" node /naming-export.cjs "$naming_exit" || naming_export_failed=1
      naming_call 18 65536 export.txt docker cp "$naming_holder:/evidence/." "$naming_destination/" || naming_export_failed=1
      [[ $(cat "$naming_destination/source-sentinel" 2>/dev/null || true) == "$naming_sha" ]] || naming_export_failed=1
      [[ $(cat "$naming_destination/evidence-sentinel" 2>/dev/null || true) == "$naming_sha" ]] || naming_export_failed=1
    else naming_export_failed=1; fi
    naming_call 4 65536 holder-stop.txt docker stop --time 2 "$naming_holder" || naming_cleanup_failed=1
    naming_call 1 65536 holder-final.json docker container inspect "$naming_holder" || naming_cleanup_failed=1
    naming_call 1 65536 holder-stopped.txt docker inspect --format '{{.State.Running}}:{{.State.Pid}}' "$naming_holder" || naming_cleanup_failed=1
    [[ $(cat "$naming_destination/holder-stopped.txt") == 'false:0' ]] || naming_cleanup_failed=1
    naming_call 1 65536 holder-remove.txt docker rm "$naming_holder" || naming_cleanup_failed=1
    naming_absent container "$naming_holder" holder-absent.txt || naming_cleanup_failed=1
  else naming_export_failed=1; naming_cleanup_failed=1; fi
  if (( naming_scratch_created )); then
    naming_call 1 65536 scratch-remove.txt docker volume rm "$naming_scratch" || naming_cleanup_failed=1
    naming_absent volume "$naming_scratch" scratch-absent.txt || naming_cleanup_failed=1
  fi
  if (( naming_artifacts_created )); then
    naming_call 1 65536 artifacts-remove.txt docker volume rm "$naming_artifacts" || naming_cleanup_failed=1
    naming_absent volume "$naming_artifacts" artifacts-absent.txt || naming_cleanup_failed=1
  fi
  # Only finite runner-generated scripts live here; never arbitrary host data.
  rm -r -- "$naming_dir" || naming_cleanup_failed=1
  printf '{"workload_exit":%d,"cleanup_failed":%d,"export_failed":%d,"source_sha":"%s","image":"%s","deadline_epoch":%d}\n' \
    "$naming_exit" "$naming_cleanup_failed" "$naming_export_failed" "$naming_sha" "$naming_image" "$naming_deadline" > "$naming_destination/operation.json" || naming_cleanup_failed=1
  # Exact finite export:192MiB tmpfs plus<=32MiB stage/control diagnostics,
  # leaving headroom to256MiB/14000 entries. Never silently drop required files.
  python3 - "$naming_destination" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]); entries=list(root.rglob('*'))
assert len(entries)<=14000, 'export inode cap exceeded'
assert sum(p.stat().st_size for p in entries if p.is_file())<=256*1024**2, 'export byte cap exceeded'
PY
  if (( $? )); then naming_export_failed=1; fi
  printf '{"workload_exit":%d,"cleanup_failed":%d,"export_failed":%d,"source_sha":"%s","image":"%s","deadline_epoch":%d}\n' \
    "$naming_exit" "$naming_cleanup_failed" "$naming_export_failed" "$naming_sha" "$naming_image" "$naming_deadline" > "$naming_destination/operation.json" || naming_cleanup_failed=1
  if (( naming_exit )); then exit "$naming_exit"; fi
  if (( naming_cleanup_failed || naming_export_failed )); then exit 3; fi
  exit 0
}
trap naming_clean EXIT
trap 'naming_exit=124; exit 124' TERM INT
naming_stage 120 acquisition.log docker pull "$naming_image"
naming_call 5 65536 naming-image.json docker image inspect "$naming_image"
python3 - "$naming_destination/naming-image.json" <<'PY'
import json,sys
image=json.load(open(sys.argv[1]))
assert len(image)==1 and image[0]['Architecture']=='amd64' and image[0]['Os']=='linux'
assert image[0]['Size'] <= 2*1024**3, 'immutable base image exceeds2GiB admission'
PY
naming_call 3 65536 docker-version.txt docker version --format '{{.Server.Version}}'
naming_version=$(cat "$naming_destination/docker-version.txt")
[[ $naming_version =~ ^([0-9]+)\.[0-9]+\.[0-9]+$ && ${BASH_REMATCH[1]} -ge 26 ]]
naming_call 3 65536 docker-info.json docker info --format '{{json .}}'
python3 - "$naming_destination/docker-info.json" <<'PY'
import json,sys
info=json.load(open(sys.argv[1]))
assert info['CgroupVersion']=='2', 'cgroup v2 required'
assert 'local' in info['Plugins']['Volume'], 'local volume driver required'
assert not info.get('MemoryLimit') is False and not info.get('PidsLimit') is False
PY
# Retain exact admitted engine's local-driver implementation, not a version guess.
naming_call 10 65536 local-driver.go curl --fail --silent --show-error --max-time 9 \
  "https://raw.githubusercontent.com/moby/moby/v${naming_version}/volume/local/local.go"
sha256sum "$naming_destination/local-driver.go" > "$naming_destination/local-driver.sha256"
naming_scratch_created=1 # include a create with uncertain response in cleanup
naming_call 3 65536 scratch-create.txt docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
  --opt o=size=5g,nr_inodes=200000,mode=1777 "$naming_scratch"
naming_artifacts_created=1 # include a create with uncertain response in cleanup
naming_call 3 65536 artifacts-create.txt docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
  --opt o=size=192m,nr_inodes=10000,mode=1777 "$naming_artifacts"
# Holder0.05CPU/64MiB/16processes + one serial payload1.95CPU/2560MiB/496.
# Conservatively reserve ALL persistent tmpfs pages separately:5GiB+192MiB+
# 2560MiB+64MiB=7936MiB<8GiB. Each payload's cgroup also charges its new tmpfs
# pages; filesystem capacity is never promised as additional cgroup memory.
# /dev/shm pages charge the active cgroup and are already within its memory cap.
naming_limits=(--cpus=1.95 --memory=2560m --memory-swap=2560m --pids-limit=496 --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges --shm-size=256m \
  --log-driver=local --log-opt max-size=2m --log-opt max-file=1 --log-opt compress=false)
naming_remaining=$((naming_deadline-$(date +%s)))
(( naming_remaining > 120 ))
cat > "$naming_dir/export.cjs" <<'EXPORT'
const f=require('fs'), p=require('path');
const root='/scratch/naming-tests', output='/evidence/fixture-artifacts';
let files=0, bytes=0;
function walk(directory, target, relative=[]) {
  for(const entry of f.readdirSync(directory,{withFileTypes:true})) {
    const source=p.join(directory,entry.name), next=[...relative,entry.name];
    const chosen=next.includes('evidence') || next.includes('receipts') || entry.name==='ownership.json';
    if(entry.isSymbolicLink()) { if(chosen) throw Error('symlink in required evidence:'+source); continue; }
    if(entry.isDirectory()) walk(source,p.join(target,entry.name),next);
    else if(entry.isFile() && chosen) {
      const size=f.statSync(source).size;
      if(size>8*1024**2) throw Error('required evidence exceeds per-file limit:'+source);
      files++;bytes+=size;
      if(files>10000 || bytes>192*1024**2) throw Error('required evidence aggregate limit');
      f.mkdirSync(p.dirname(p.join(target,entry.name)),{recursive:true});
      f.copyFileSync(source,p.join(target,entry.name));
    }
  }
}
if(f.existsSync(root)) {
  for(const entry of f.readdirSync(root,{withFileTypes:true}))
    if(entry.isDirectory()) walk(p.join(root,entry.name),p.join(output,entry.name));
}
if(Number(process.argv[2])===0) {
  for(const name of ['naming.log','naming.xml','work-status.json','boundary-completed.json'])
    if(!f.existsSync('/evidence/'+name) || f.statSync('/evidence/'+name).size===0)
      throw Error('missing required completed evidence:'+name);
}
f.writeFileSync('/evidence/fixture-export.json',JSON.stringify({files,bytes,workloadExit:Number(process.argv[2])})+'\n');
console.log('fixture export complete',files,bytes);
EXPORT
naming_call 5 65536 holder-start.txt docker run -d --name "$naming_holder" \
  --cpus=0.05 --memory=64m --memory-swap=64m --pids-limit=16 --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges --network=none --user=1000:1000 \
  --log-driver=local --log-opt max-size=64k --log-opt max-file=1 --log-opt compress=false \
  --mount "type=volume,src=$naming_scratch,dst=/scratch" \
  --mount "type=volume,src=$naming_artifacts,dst=/evidence" \
  --mount "type=bind,src=$naming_dir/export.cjs,dst=/naming-export.cjs,readonly" \
  "$naming_image" bash -euc '
    [[ $(</sys/fs/cgroup/memory.max) == 67108864 ]]
    [[ $(</sys/fs/cgroup/memory.swap.max) == 0 ]]
    [[ $(</sys/fs/cgroup/pids.max) == 16 ]]
    [[ $(</sys/fs/cgroup/cpu.max) == "5000 100000" ]]
    printf "holder pid=%s\n" "$$"
    exec sleep "$1"
  ' naming-holder "$naming_remaining"
cat > "$naming_dir/boundary.sh" <<'BOUNDARY'
#!/usr/bin/env bash
set -euo pipefail
stage=$1 sha=$2
# Refuse absent or silently weakened cgroup/volume byte/inode controls.
test "$(cat /sys/fs/cgroup/memory.max)" = 2684354560
test "$(cat /sys/fs/cgroup/memory.swap.max)" = 0
test "$(cat /sys/fs/cgroup/pids.max)" = 496
test "$(cat /sys/fs/cgroup/cpu.max)" = '195000 100000'
node - "$stage" "$sha" <<'JS'
const f=require('fs'); const [stage,sha]=process.argv.slice(2);
for(const [path,bytes,inodes] of [['/scratch',5*1024**3,200000],['/evidence',192*1024**2,10000]]) {
  const s=f.statfsSync(path);
  if(s.type!==0x1021994 || s.bsize*s.blocks!==bytes || s.files!==inodes)
    throw Error('unsupported tmpfs byte/inode quota: '+path+JSON.stringify(s));
}
if(stage==='init') {
  f.writeFileSync('/scratch/source-sentinel',sha);
  f.writeFileSync('/evidence/evidence-sentinel',sha);
  f.writeFileSync('/evidence/source-sentinel',sha);
} else {
  for(const path of ['/scratch/source-sentinel','/evidence/evidence-sentinel'])
    if(f.readFileSync(path,'utf8')!==sha) throw Error('lost stage sentinel: '+path);
}
f.writeFileSync('/evidence/boundary-'+stage+'.json',JSON.stringify({stage,sha,pid:process.pid,tmpfs_quotas_verified:true})+'\n');
console.log('source/evidence stage boundary retained',stage,sha);
JS
BOUNDARY
naming_stage 60 init.log docker run --name "$naming_init" \
  "${naming_limits[@]}" --network=none --user=0 --cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER \
  --mount "type=volume,src=$naming_scratch,dst=/scratch" \
  --mount "type=volume,src=$naming_artifacts,dst=/evidence" \
  --mount "type=bind,src=$(pwd -P),dst=/input/source,readonly" \
  --mount "type=bind,src=$(command -v uv),dst=/input/uv,readonly" \
  --mount "type=bind,src=$naming_dir/boundary.sh,dst=/boundary.sh,readonly" \
  "$naming_image" bash -euc '
    cp -a /usr /scratch/usr
    cp -a /etc /scratch/etc
    cp -a /var /scratch/var
    mkdir -p /scratch/{tmp,cache,browsers,python,home}
    cp /input/uv /scratch/usr/local/bin/uv
    # upload-pack opens .git separately; both processes need the exact mounted
    # input admitted in protected config, confined to this clone and scratch.
    printf "[safe]\n\tdirectory =\n\tdirectory = /input/source\n\tdirectory = /input/source/.git\n" > /scratch/source-read.gitconfig
    git --version
    stat -c "%u:%g %n" /input/source /input/source/.git
    GIT_CONFIG_GLOBAL=/scratch/source-read.gitconfig git clone --no-local /input/source /scratch/source
    git -C /scratch/source checkout --detach "$1"
    test "$(git -C /scratch/source rev-parse HEAD)" = "$1"
    bash /boundary.sh init "$1"
  ' naming-init "$naming_sha"
# A common mount layout keeps every dependency and workload write in quota.
naming_mounts=(
  --mount "type=volume,src=$naming_scratch,dst=/scratch"
  --mount "type=volume,src=$naming_scratch,dst=/tmp,volume-subpath=tmp"
  --mount "type=volume,src=$naming_scratch,dst=/source,volume-subpath=source"
  --mount "type=volume,src=$naming_artifacts,dst=/evidence"
  --mount "type=bind,src=$naming_dir/boundary.sh,dst=/boundary.sh,readonly")
cat > "$naming_dir/prepare.sh" <<'PREP'
#!/usr/bin/env bash
set -euo pipefail
export HOME=/scratch/home TMPDIR=/tmp UV_CACHE_DIR=/scratch/cache/uv
export UV_PYTHON_INSTALL_DIR=/opt/naming-python PLAYWRIGHT_BROWSERS_PATH=/opt/naming-browsers
export npm_config_cache=/scratch/cache/npm
cd /source
bash /boundary.sh prepare "$1"
uv python install 3.14
uv sync --frozen --python 3.14
cd web
npm ci
npm run build
# Existing screenshot-harness CLI, provisioned as venue tooling only.
npm install --global @playwright/cli@0.1.18
node /usr/local/lib/node_modules/@playwright/cli/node_modules/playwright/cli.js install --with-deps chromium
ln -sfn /usr/local/bin/node /usr/bin/node
ln -sfn /usr/local/bin/playwright-cli /usr/bin/playwright-cli
node -e 'const p=require("/usr/local/lib/node_modules/@playwright/cli/package.json"); if(p.version!=="0.1.18" || p.dependencies.playwright!=="1.63.0-alpha-2026-08-05") process.exit(1); console.log(JSON.stringify(p));'
/source/.venv/bin/python -I -c 'import sys; assert sys.version_info[:2]==(3,14); print(sys.version,sys.executable,sys.prefix)'
node --version
[[ $(node --version) == v24.19.0 ]]
uv --version
sha256sum /usr/local/bin/node /usr/local/bin/uv /source/uv.lock /source/web/package-lock.json
bash /boundary.sh prepared "$1"
# Effective venv and all download locations stay inside the bounded volume.
# Retain tool version/pin/digest provenance in setup's bounded Docker log.
chown -R 1000:1000 /scratch
PREP
naming_stage 600 setup.log docker run --name "$naming_prepare" \
  "${naming_limits[@]}" "${naming_mounts[@]}" \
  --user=0 --cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER --cap-add=SETUID --cap-add=SETGID \
  --mount "type=volume,src=$naming_scratch,dst=/usr,volume-subpath=usr" \
  --mount "type=volume,src=$naming_scratch,dst=/etc,volume-subpath=etc" \
  --mount "type=volume,src=$naming_scratch,dst=/var,volume-subpath=var" \
  --mount "type=volume,src=$naming_scratch,dst=/opt/naming-python,volume-subpath=python" \
  --mount "type=volume,src=$naming_scratch,dst=/opt/naming-browsers,volume-subpath=browsers" \
  --mount "type=bind,src=$naming_dir/prepare.sh,dst=/prepare.sh,readonly" \
  "$naming_image" bash /prepare.sh "$naming_sha"
cat > "$naming_dir/work.sh" <<'WORK'
#!/usr/bin/env bash
set -euo pipefail
ulimit -f 8192 # per-file eight MiB, independent of aggregate volume quotas
export HOME=/scratch/home TMPDIR=/tmp UV_CACHE_DIR=/scratch/cache/uv
export UV_PYTHON=/source/.venv/bin/python UV_PROJECT_ENVIRONMENT=/source/.venv
export UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never UV_OFFLINE=1 UV_NO_CONFIG=1
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PATH=/usr/local/bin:/usr/bin:/bin
mkdir -p /evidence
cd /source
bash /boundary.sh work "$1"
test "$(git rev-parse HEAD)" = "$1"
test -z "$(git status --porcelain)"
/source/.venv/bin/python -I -c 'import sys; assert sys.version_info[:2]==(3,14); print(sys.executable,sys.prefix,sys.version)'
set +e
bash tests/helpers/identity_names/run.sh
status=$?
set -e
# Copy only naming-owned basetemp evidence: SQL observations, real captures,
# actual children/ports/closure and CLI results. Aggregate 192MiB target refuses
# exhaustion visibly; do not silently omit screenshots/logs after a cap.
if [ -d /scratch/naming-tests ]; then
  /source/.venv/bin/python - <<'PY'
from pathlib import Path
import shutil
root=Path('/scratch/naming-tests')
output=Path('/evidence/fixture-artifacts')
output.mkdir()
# Only exact naming fixture evidence/receipts/child ownership; no global temp
# or dependency/cache/runtime copy, and no silent skip on output exhaustion.
for directory in root.iterdir():
    if not directory.is_dir():
        continue
    for path in directory.rglob('*'):
        relative=path.relative_to(directory)
        if ('evidence' not in relative.parts and 'receipts' not in relative.parts
                and path.name != 'ownership.json'):
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink in required evidence: {path}")
        if not path.is_file():
            continue
        target=output/directory.name/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
PY
fi
# A successful child with a retained tail is incomplete required evidence.
# Keep the shared bounded-output helper unchanged.
set +e
/source/.venv/bin/python - <<'PY'
from pathlib import Path
import json, xml.etree.ElementTree as ET
root=Path('/evidence')
marker=b'[nightly log truncated; showing final output bytes]'
for path in root.rglob('*'):
    if path.is_file():
        assert path.stat().st_size <= 8*1024**2, f'per-file evidence cap: {path}'
        if path.suffix in {'.log', '.txt'}:
            assert marker not in path.read_bytes(), f'incomplete evidence: {path}'
# A failed generator/test still exports its actual partial artifacts and exit.
if Path('/evidence/naming.xml').exists():
    ET.parse('/evidence/naming.xml')
PY
proof_status=$?
set -e
printf '{"workload_exit":%d,"evidence_check_exit":%d}\n' "$status" "$proof_status" > /evidence/work-status.json
bash /boundary.sh completed "$1"
if (( status )); then exit "$status"; fi
(( proof_status == 0 )) || exit 125
test -s /evidence/naming.xml
test -s /evidence/naming.log
exit 0
WORK
set +e
naming_stage 780 workload.log docker run --name "$naming_work" \
  "${naming_limits[@]}" "${naming_mounts[@]}" --network=none --user=1000:1000 \
  --mount "type=volume,src=$naming_scratch,dst=/usr,volume-subpath=usr,readonly" \
  --mount "type=volume,src=$naming_scratch,dst=/etc,volume-subpath=etc,readonly" \
  --mount "type=volume,src=$naming_scratch,dst=/var,volume-subpath=var,readonly" \
  --mount "type=volume,src=$naming_scratch,dst=/opt/naming-python,volume-subpath=python,readonly" \
  --mount "type=volume,src=$naming_scratch,dst=/opt/naming-browsers,volume-subpath=browsers,readonly" \
  --mount "type=bind,src=$naming_dir/work.sh,dst=/work.sh,readonly" \
  "$naming_image" bash /work.sh "$naming_sha"
naming_exit=$?
set -e
exit "$naming_exit"
