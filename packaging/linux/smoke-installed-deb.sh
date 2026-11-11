#!/usr/bin/env bash
# Destructive package install/remove smoke: disposable Ubuntu 22.04 only.
set -euo pipefail
exec /usr/bin/python3 -I - "$@" <<'PY'
from __future__ import annotations

import configparser
from collections.abc import Callable
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

if len(sys.argv) != 3:
    sys.exit('usage: smoke-installed-deb.sh <path-to.deb> <evidence-dir>')
deb = Path(sys.argv[1]).resolve(strict=True)
evidence = Path(sys.argv[2]).absolute()
evidence.mkdir(parents=True, exist_ok=False)
result = {'format': 'happyranch-installed-deb-smoke@1', 'status': 'failed',
          'stage': 'preflight', 'checks': [], 'commands': [], 'cleanup_errors': []}
processes = []
logs = []
scratch = None
installed = False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(argv: list[str | Path], name: str, *, env: dict[str, str] | None = None,
        timeout: int = 300) -> str:
    path = evidence / (name + '.log')
    print('+', shlex.join(map(str, argv)), flush=True)
    record = {'argv': list(map(str, argv)), 'exit_code': None, 'log': path.name}
    result['commands'].append(record)
    with path.open('wb') as log:
        try:
            completed = subprocess.run(record['argv'], env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            record['timed_out'] = True
            raise
    record['exit_code'] = completed.returncode
    require(completed.returncode == 0, name + ' failed; see ' + path.name)
    return path.read_text(errors='replace')


def start(argv: list[str], name: str, env: dict[str, str], *,
          pass_fds: tuple[int, ...] = ()) -> subprocess.Popen:
    log = (evidence / (name + '.log')).open('wb')
    logs.append(log)
    process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, pass_fds=pass_fds)
    processes.append(process)
    return process


def check(name: str) -> None:
    result['checks'].append(name)
    print('PASS:', name, flush=True)


def wait_until(predicate: Callable[[], bool], seconds: int, message: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise RuntimeError(message)


def packages() -> list[str]:
    return subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'],
        text=True).splitlines()


def home_snapshot(home: Path) -> dict[str, int | str]:
    # Compare all retained user-data bytes and metadata without exporting them.
    digest = hashlib.sha256()
    paths = [home, *sorted(home.rglob('*'))]
    for path in paths:
        info = path.lstat()
        digest.update(repr((str(path.relative_to(home)), info.st_mode, info.st_ino,
                            info.st_size, info.st_mtime_ns, info.st_ctime_ns)).encode())
        if stat.S_ISREG(info.st_mode):
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        elif path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
    return {'entries': len(paths), 'sha256': digest.hexdigest()}


try:
    require(os.geteuid() != 0, 'run as a normal user with passwordless sudo')
    # Adopt and reap display/WebKit grandchildren if a parent exits first.
    require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0,
            'cannot enable child subreaper')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '22.04', 'requires disposable Ubuntu 22.04')
    require(subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', 'happyranch'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0,
            'happyranch is already registered; use a fresh machine')
    package = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Package'], text=True).strip()
    arch = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Architecture'], text=True).strip()
    native = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    result.update(package=package, package_arch=arch, host_arch=native,
                  deb_sha256=hashlib.sha256(deb.read_bytes()).hexdigest())
    require(package == 'happyranch' and arch == native, 'package name/architecture mismatch')
    require(native in ('amd64', 'arm64'), 'unsupported Ubuntu architecture')
    check('native package architecture')
    scratch = Path(tempfile.mkdtemp(prefix='happyranch-installed-smoke-'))
    # Restrict downloads to stock Jammy archives, even on a configured runner.
    archive = ('https://archive.ubuntu.com/ubuntu' if native == 'amd64'
               else 'https://ports.ubuntu.com/ubuntu-ports')
    security = 'https://security.ubuntu.com/ubuntu' if native == 'amd64' else archive
    sources = scratch / 'ubuntu.list'
    sources.write_text(f'deb {archive} jammy main universe restricted multiverse\n'
                       f'deb {archive} jammy-updates main universe restricted multiverse\n'
                       f'deb {security} jammy-security main universe restricted multiverse\n')
    # apt's unprivileged acquisition user must be able to read these inputs.
    scratch.chmod(0o755)
    staged_deb = scratch / deb.name
    shutil.copyfile(deb, staged_deb)
    staged_deb.chmod(0o644)
    apt = ['sudo', '-n', 'apt-get', '-o', 'Dir::Etc::sourcelist=' + str(sources),
           '-o', 'Dir::Etc::sourceparts=-', '-o', 'APT::Install-Recommends=false']
    result['stage'] = 'install'
    before = packages()
    (evidence / 'packages-before.log').write_text('\n'.join(before) + '\n')
    run(apt + ['update'], 'apt-update')
    # No display-tool installation is allowed before this independent install.
    installed = True  # Also remove a partially installed package on failure.
    run(apt + ['install', '-y', str(staged_deb)], 'apt-install', timeout=600)
    run(apt + ['check'], 'apt-check')
    run(['happyranch', '--help'], 'cli-help')
    run(['/usr/bin/python3', '-I', '-c',
         'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); '
         'from gi.repository import Gtk, WebKit2; '
         'print("GTK", Gtk.get_major_version(), "WebKit", WebKit2.get_major_version())'],
        'gi-before-display-tools')
    after = packages()
    (evidence / 'packages-after-deb.log').write_text('\n'.join(after) + '\n')
    result['packages_added_by_deb'] = sorted(set(after) - set(before))
    check('deb Depends and CLI/GI work before installing display tools')
    owned = subprocess.check_output(['dpkg-query', '-L', 'happyranch'], text=True).splitlines()
    (evidence / 'package-owned-paths.log').write_text('\n'.join(owned) + '\n')
    # Shared /usr ancestors belong to many packages. Every payload-specific
    # directory, file and link must disappear; retain all of them for removal.
    payload_paths = [Path(p) for p in owned if p.startswith(('/opt/happyranch-desktop',
                     '/usr/share/doc/happyranch')) or not Path(p).is_dir()]
    build_info = json.loads(Path('/opt/happyranch-desktop/build-info.json').read_text())
    result['build_source_sha'] = build_info['source_sha']
    if os.environ.get('GITHUB_SHA'):
        require(build_info['source_sha'] == os.environ['GITHUB_SHA'],
                'installed payload source differs from workflow head')
    desktop = Path('/usr/share/applications/happyranch.desktop')
    entry = configparser.ConfigParser(interpolation=None)
    entry.read(desktop)
    command = shlex.split(entry['Desktop Entry']['Exec'])
    require(command == ['/usr/bin/happyranch-app'] and
            entry['Desktop Entry']['Type'] == 'Application', 'unexpected installed desktop Exec')
    (evidence / 'happyranch.desktop').write_bytes(desktop.read_bytes())
    result['desktop_exec'] = command
    result['stage'] = 'display-tools'
    run(apt + ['install', '-y', 'xvfb', 'openbox', 'xdotool', 'scrot',
               'desktop-file-utils', 'dbus-x11'], 'apt-display-tools', timeout=600)
    run(['desktop-file-validate', str(desktop)], 'desktop-validate')
    check('installed desktop entry validates')
    private = scratch / 'user'
    private.mkdir(mode=0o700)
    for name in ('home', 'data', 'config', 'cache', 'run'):
        (private / name).mkdir(mode=0o700)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(private / 'home'), 'LANG': 'C.UTF-8',
           'XDG_DATA_HOME': str(private / 'data'), 'XDG_CONFIG_HOME': str(private / 'config'),
           'XDG_CACHE_HOME': str(private / 'cache'), 'XDG_RUNTIME_DIR': str(private / 'run'),
           'LIBGL_ALWAYS_SOFTWARE': '1'}
    display_file = scratch / 'display'
    with display_file.open('wb') as display_fd:
        xvfb = start(['Xvfb', '-displayfd', str(display_fd.fileno()), '-screen', '0',
                      '1440x900x24', '-nolisten', 'tcp', '-noreset'], 'xvfb', env,
                     pass_fds=(display_fd.fileno(),))
        wait_until(lambda: xvfb.poll() is None and display_file.stat().st_size > 0,
                   10, 'Xvfb did not start')
    env['DISPLAY'] = ':' + display_file.read_text().strip()
    bus = start(['dbus-daemon', '--session', '--nofork', '--nopidfile', '--print-address=1'],
                'dbus', env)
    bus_log = evidence / 'dbus.log'
    wait_until(lambda: bus.poll() is None and bus_log.stat().st_size > 0,
               10, 'private D-Bus did not start')
    env['DBUS_SESSION_BUS_ADDRESS'] = bus_log.read_text().splitlines()[0]
    wm = start(['openbox', '--sm-disable'], 'openbox', env)
    time.sleep(1)
    require(wm.poll() is None, 'window manager exited')
    result['stage'] = 'app-launch'
    app = start(command, 'app', env)
    home = private / 'data/happyranch'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    daemon = {}

    def ready() -> bool:
        require(app.poll() is None, 'desktop app exited before becoming healthy')
        try:
            pid = int((home / 'daemon.pid').read_text().strip())
            port = int((home / 'daemon.port').read_text().strip())
            parent = int(Path(f'/proc/{pid}/status').read_text().split('PPid:')[1].split()[0])
            require(parent == app.pid and 1 <= port <= 65535, 'daemon is not the app child')
            with opener.open(f'http://127.0.0.1:{port}/api/v1/health', timeout=1) as response:
                health = json.load(response)
                if response.status != 200 or health.get('status') != 'ok':
                    return False
            daemon.update(pid=pid, port=port, app_pid=app.pid, health_http=200, health_status='ok')
            return True
        except (OSError, ValueError):
            return False

    wait_until(ready, 90, 'owned daemon did not become healthy')
    result['daemon'] = daemon
    with opener.open(f'http://127.0.0.1:{daemon["port"]}/', timeout=5) as response:
        html = response.read()
        require(response.status == 200 and html ==
                Path('/opt/happyranch-desktop/web/dist/index.html').read_bytes(),
                'root response differs from built index.html')
    result['root'] = {'http_status': 200, 'matches_built_index': True,
                      'sha256': hashlib.sha256(html).hexdigest()}
    (evidence / 'root.html').write_bytes(html)
    check('installed menu Exec owns a healthy daemon and serves built index.html')
    window = run(['xdotool', 'search', '--sync', '--onlyvisible', '--pid', str(app.pid),
                  '--name', '^HappyRanch$'], 'find-window', env=env, timeout=20).splitlines()[0]
    run(['xdotool', 'windowactivate', '--sync', window], 'activate', env=env, timeout=10)
    time.sleep(8)  # Allow WebKit to render; actual PNG inspection remains required.
    require(app.poll() is None, 'app exited before screenshot')
    run(['scrot', '--focused', str(evidence / 'window.png')], 'screenshot', env=env)
    require((evidence / 'window.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),
            'screenshot is not PNG')
    result['screenshot'] = {'file': 'window.png', 'sha256': hashlib.sha256(
        (evidence / 'window.png').read_bytes()).hexdigest(), 'visually_reviewed': False}
    check('live window screenshot captured')
    result['stage'] = 'quit'
    run(['xdotool', 'key', '--clearmodifiers', 'ctrl+q'], 'normal-quit', env=env)
    result['app_exit_code'] = app.wait(timeout=20)
    require(app.returncode == 0, 'normal Quit did not exit zero')
    require(not Path(f'/proc/{daemon["pid"]}').exists(), 'app-owned daemon remains after Quit')
    require(all(not os.path.lexists(home / name) for name in ('daemon.pid', 'daemon.port')),
            'daemon markers remain after Quit')
    check('Ctrl+Q exits zero, reaps daemon and removes lifecycle markers')
    result['stage'] = 'remove'
    sentinel = home / 'smoke-user-data'
    sentinel.write_text('preserve this user data\n')
    snapshot = home_snapshot(home)
    run(['sudo', '-n', 'apt-get', 'remove', '-y', 'happyranch'], 'apt-remove')
    installed = False
    leftovers = [str(path) for path in payload_paths if os.path.lexists(path)]
    require(not leftovers, 'package-owned leftovers: ' + repr(leftovers[:20]))
    require(home_snapshot(home) == snapshot, 'package removal changed user data')
    result['user_data_after_remove'] = snapshot
    result['package_paths_checked'] = len(payload_paths)
    check('remove leaves no package payload and preserves complete user-data home')
    result['status'] = 'passed'
    result['stage'] = 'complete'
except Exception as exc:
    result['error'] = str(exc)
    print('FAIL:', exc, file=sys.stderr, flush=True)
finally:
    # Failure teardown is separate from the assertions above: killing a failed
    # app can never turn a broken Quit/daemon-removal assertion into a pass.
    for process in reversed(processes):
        try:
            # An exited parent may leave live descendants in its owned group.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while True:
                try:
                    while os.waitpid(-process.pid, os.WNOHANG)[0] > 0:
                        pass
                except ChildProcessError:
                    pass
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    os.killpg(process.pid, signal.SIGKILL)
                    raise RuntimeError('owned process group did not settle: ' + str(process.pid))
                time.sleep(0.1)
        except Exception as exc:
            result['cleanup_errors'].append(str(exc))
    for log in logs:
        log.close()
    if installed:
        try:
            run(['sudo', '-n', 'apt-get', 'remove', '-y', 'happyranch'], 'failure-remove')
        except Exception as exc:
            result['cleanup_errors'].append(str(exc))
    if scratch is not None:
        try:
            shutil.rmtree(scratch)
        except Exception as exc:
            result['cleanup_errors'].append(str(exc))
    if result['cleanup_errors']:
        result['status'] = 'failed'
    (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
sys.exit(0 if result['status'] == 'passed' else 1)
PY
