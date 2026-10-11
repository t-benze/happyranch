"""Disposable hosted local-all receipts; retired unit proof entries are absent."""
import concurrent.futures, gzip, hashlib, json, os, pathlib, shutil, signal, subprocess, sys, tempfile, threading


MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
SEGMENT_BYTES = 8 * 1024 * 1024
artifact_lock = threading.Lock()
artifact_reserved = 0


def capture_stream(reader, *, directory, stem):
    # Ordered gzip members keep complete raw evidence below acquisition bounds.
    # A bound refusal retains partial files and is never a complete capture.
    global artifact_reserved
    metadata = {'complete': False, 'raw_bytes': 0, 'raw_sha256': None,
                'stored_bytes': 0, 'stored_sha256': None, 'segments': []}
    raw_digest, stored_digest = hashlib.sha256(), hashlib.sha256()
    pending = b''
    try:
        while True:
            pending = reader.read(65536)
            if not pending:
                break
            reservation = SEGMENT_BYTES + 65536
            with artifact_lock:
                existing = sum(f.stat().st_size for f in evidence.rglob('*')
                               if f.is_file() and 'scratch' not in f.relative_to(evidence).parts)
                # Reserve overhead for tails and compact receipts before admission.
                if existing + artifact_reserved + reservation + 2 * 1048576 > MAX_ARCHIVE_BYTES:
                    raise RuntimeError('artifact archive bound: incomplete capture retained')
                artifact_reserved += reservation
            number = len(metadata['segments']) + 1
            name = stem + ('' if number == 1 else '-' + str(number).zfill(6)) + '.gz'
            path = directory / name
            segment = {'order': number, 'path': str(path), 'complete': False,
                       'raw_bytes': 0, 'raw_sha256': None, 'stored_bytes': None, 'stored_sha256': None}
            metadata['segments'].append(segment)
            segment_digest = hashlib.sha256()
            try:
                with gzip.open(path, 'wb') as stream:
                    while pending and segment['raw_bytes'] < SEGMENT_BYTES:
                        stream.write(pending)
                        segment_digest.update(pending)
                        raw_digest.update(pending)
                        segment['raw_bytes'] += len(pending)
                        metadata['raw_bytes'] += len(pending)
                        pending = b''
                        if segment['raw_bytes'] < SEGMENT_BYTES:
                            pending = reader.read(min(65536, SEGMENT_BYTES - segment['raw_bytes']))
                segment['complete'] = True
            finally:
                segment['raw_sha256'] = segment_digest.hexdigest()
                if path.exists():
                    digest = hashlib.sha256()
                    with path.open('rb') as stored:
                        for chunk in iter(lambda: stored.read(65536), b''):
                            digest.update(chunk)
                            stored_digest.update(chunk)
                    segment.update(stored_bytes=path.stat().st_size, stored_sha256=digest.hexdigest())
                    metadata['stored_bytes'] += segment['stored_bytes']
                with artifact_lock:
                    artifact_reserved -= reservation
            if segment['stored_bytes'] > MAX_MEMBER_BYTES:
                raise RuntimeError('artifact member bound: incomplete capture retained')
        metadata['complete'] = True
    finally:
        metadata.update(raw_sha256=raw_digest.hexdigest(), stored_sha256=stored_digest.hexdigest())
        (directory / (stem + '.json')).write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def bounded_run(argv, *, cwd, env, directory, tail):
    metadata = {'argv': argv, 'cwd': str(cwd), 'complete': False, 'exit_code': None,
                'stdout_contract': 'runner stdout includes merged child stdout/stderr',
                'stderr_contract': 'runner stderr, separately captured', 'streams': {}}
    path = directory / 'full-log.json'
    path.write_text(json.dumps(metadata, indent=2) + '\n')
    process = subprocess.Popen([sys.executable, str(source / 'scripts/run_bounded_output.py'),
        '--output', str(tail), '--max-bytes', '1048576', '--', *argv],
        cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as readers:
            futures = {'stdout': readers.submit(capture_stream, process.stdout, directory=directory, stem='full.log'),
                       'stderr': readers.submit(capture_stream, process.stderr, directory=directory, stem='runner-stderr.log')}
            try:
                for name, future in futures.items():
                    metadata['streams'][name] = future.result()
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                raise
        metadata.update(exit_code=process.wait(), complete=True)
        metadata.update({key: metadata['streams']['stdout'][key]
                         for key in ('raw_bytes', 'raw_sha256', 'stored_bytes', 'stored_sha256', 'segments')})
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
        # Read partial stream manifests too; preserve cleanup and error evidence.
        for name, stem in (('stdout', 'full.log'), ('stderr', 'runner-stderr.log')):
            manifest_path = directory / (stem + '.json')
            if manifest_path.exists():
                metadata['streams'][name] = json.loads(manifest_path.read_text())
        metadata['reaped_exit'] = process.returncode
        path.write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata['exit_code'], metadata


source = pathlib.Path.cwd()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert head == os.environ['GITHUB_SHA']
assert not subprocess.check_output(['git', 'status', '--porcelain'])
assert sys.version_info[:2] == (3, 14)
evidence = pathlib.Path(os.environ['RUNNER_TEMP']) / 'local-ci-all'
evidence.mkdir()
receipt = {'event': os.environ['GITHUB_EVENT_NAME'], 'ref': os.environ['GITHUB_REF'],
           'github_sha': os.environ['GITHUB_SHA'], 'checkout': head,
           'command': 'scripts/local_ci.sh all', 'all_only': os.environ.get('ALL_ONLY') == 'true',
           'python': sys.executable, 'cwd': str(source),
           'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
           'python_version': sys.version, 'source_sha256': {
               name: hashlib.sha256((source / name).read_bytes()).hexdigest()
               for name in ('scripts/local_ci.sh', 'tests/helpers/integration_parent.py',
                            'tests/helpers/integration_stub_guard/guard.py', 'cli/main.py')}}
with tempfile.TemporaryDirectory(prefix='local-ci-parent-') as directory:
    root = pathlib.Path(directory)
    for name in ('home', 'config', 'cache', 'tmp', 'daemon', 'bin'):
        (root / name).mkdir(mode=0o700)
    (root / 'daemon/executors.json').write_text('{}')
    for tool in ('uv', 'node', 'npm', 'npx'):
        resolved = shutil.which(tool)
        assert resolved is not None, tool
        (root / 'bin' / tool).symlink_to(pathlib.Path(resolved).resolve())
    env = {'HOME': str(root / 'home'), 'XDG_CONFIG_HOME': str(root / 'config'),
           'XDG_CACHE_HOME': str(root / 'cache'), 'TMPDIR': str(root / 'tmp'),
           'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'),
           'HAPPYRANCH_DAEMON_HOME': str(root / 'daemon'), 'HAPPYRANCH_DAEMON_PORT': '0',
           'PATH': os.pathsep.join((str(root / 'bin'), str(source / '.venv/bin'), '/usr/local/bin', '/usr/bin', '/bin')),
           'UV_PYTHON': sys.executable, 'UV_PYTHON_DOWNLOADS': 'never',
           'UV_CACHE_DIR': str(root / 'cache/uv'), 'PYTHONNOUSERSITE': '1',
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'CI': 'true'}
    receipt['tools'] = {tool: {'path': str((root / 'bin' / tool).resolve()),
                              'version': subprocess.check_output([tool, '--version'], env=env, text=True).strip()}
                        for tool in ('uv', 'node', 'npm')}
    assert receipt['tools']['node']['version'].split('.')[0] == 'v24'
    receipt_path = evidence / 'provenance.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True), flush=True)
    all_exit, receipt['full_log'] = bounded_run(['scripts/local_ci.sh', 'all'],
        cwd=source, env=env, directory=evidence, tail=evidence / 'local-ci-all.log')
    result = subprocess.CompletedProcess(['scripts/local_ci.sh', 'all'], all_exit)
    receipt['exit_code'] = result.returncode
    receipt['environment'] = env
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print('scripts/local_ci.sh all exit_code=' + str(result.returncode), flush=True)
receipt['cleanup_exit'] = 0 if not root.exists() else None
receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
receipt['python_units'] = {'status': 'RETIRED', 'authority': 'THR-291 seq40', 'executed': False}
receipt['fresh_e2e'] = {'status': 'PENDING', 'executed': False}
receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
raise SystemExit(result.returncode)
