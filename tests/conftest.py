from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


# ── Test-mode platform isolation ──────────────────────────────────────
# In test environments, we don't have provisioned macOS executor accounts.
# This fixture monkeypatches detect_platform_isolation to return a
# test-mode isolation. By DEFAULT, the test isolation models distinct-
# identity (is_same_owner_mode=False), reflecting production behavior.
# Tests that need same-owner mode (prelaunch integrity adversarial tests,
# mutation feasibility proofs) must request the ``same_owner_mode``
# fixture explicitly.
#
# Real isolation tests against provisioned accounts live in
# test_canonical_production_bound.py and bypass the monkeypatch entirely.

_TEST_ISOLATION_FIXTURE_ACTIVE = True

# Internal flag: set to True by the ``same_owner_mode`` fixture so the
# autouse fixture knows to configure same-owner isolation for that test.
_SAME_OWNER_REQUESTED = False


@pytest.fixture
def same_owner_mode():
    """Fixture: request same-owner test isolation for this test.

    Use this ON tests that need same-owner mode:
    - Adversarial prelaunch integrity tests (mutation detection)
    - Mutation feasibility proofs (technical possibility of chmod+write)
    - Specific canonical store tests that exercise same-owner code paths

    Without this fixture, the default test isolation models distinct-
    identity, matching production behavior for provisioned deployments.

    Manages both the monkeypatched isolation AND the
    ``HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR`` env var so tests that
    branch on the env var get consistent behavior.
    """
    global _SAME_OWNER_REQUESTED
    import os as _os
    _SAME_OWNER_REQUESTED = True
    _prev = _os.environ.get("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR")
    _os.environ["HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR"] = "1"
    yield
    _SAME_OWNER_REQUESTED = False
    if _prev is not None:
        _os.environ["HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR"] = _prev
    else:
        _os.environ.pop("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR", None)


@pytest.fixture(autouse=True)
def _test_mode_platform_isolation(monkeypatch, request):
    """Install a test-mode platform detector.

    By default, this models DISTINCT-IDENTITY isolation (production-
    faithful). Tests that need same-owner mode must request the
    ``same_owner_mode`` fixture.

    Also manages ``HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR`` env var so
    tests branching on it see the same mode as the isolation object.

    Real isolation tests in test_canonical_production_bound.py call
    detect_platform_isolation directly (bypassing the monkeypatch) or
    are skipped on non-provisioned hosts.
    """
    global _SAME_OWNER_REQUESTED
    if not _TEST_ISOLATION_FIXTURE_ACTIVE:
        yield
        return

    _same_owner = _SAME_OWNER_REQUESTED

    from runtime.platform.isolation import (
        PlatformIdentity,
        PlatformIsolation,
        PlatformIsolationError,
        _MacOSPlatformIsolation as _RealMacOSIsolation,
        detect_platform_isolation as _real_detect,
    )
    import os
    import stat
    import subprocess
    import sys

    # Manage env var consistently with the isolation mode so tests that
    # branch on HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR get the right path.
    _prev_so_env = os.environ.get("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR")
    if not _same_owner:
        os.environ.pop("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR", None)
    else:
        os.environ["HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR"] = "1"

    # Try to get the real isolation; if it fails (unsupported platform),
    # create a test-only stub.
    try:
        _real_isolation = _real_detect()
    except PlatformIsolationError:
        _real_isolation = None

    class _TestMacOSIsolation(PlatformIsolation):
        """Test-mode macOS isolation.

        Two configurations, controlled by ``_same_owner``:
        - Distinct-identity (default): models production provisioned
          deployment. ``is_same_owner_mode`` returns False, and
          ``_assert_executor_distinct`` passes (test process IS the
          daemon — we model the executor as distinct via the isolation
          contract, not via actual UID separation). File hardening
          (make_file_readonly) still sets 0o444.
        - Same-owner (opt-in via ``same_owner_mode`` fixture): the
          executor and daemon share the same UID. Files may be owner-
          writable; integrity relies on hash detection.
        """

        def __init__(self) -> None:
            self._daemon_uid = os.getuid()
            self._daemon_gid = os.getgid()
            # In test mode, executor IS the daemon (same user running tests)
            self._executor_identity = PlatformIdentity(
                uid=self._daemon_uid,
                gid=self._daemon_gid,
                is_service=False,
                is_restricted=True,  # Treat as restricted for contract conformance
            )
            self._same_owner = _same_owner

        def current_identity(self) -> PlatformIdentity:
            return PlatformIdentity(
                uid=self._daemon_uid,
                gid=self._daemon_gid,
                is_service=True,
                is_restricted=False,
            )

        def executor_identity(self):
            return self._executor_identity

        @property
        def is_same_owner_mode(self) -> bool:
            # Default: distinct-identity (production-faithful).
            # Only True when same_owner_mode fixture is active.
            return self._same_owner

        def _assert_executor_distinct(self) -> None:
            # In test mode, we model distinct-identity via the isolation
            # contract, not actual UID separation. The test process IS the
            # daemon user — we accept this as test-mode accommodation.
            pass

        def provision_canonical_store(self, path: Path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                         | stat.S_IROTH | stat.S_IXOTH)
            except OSError:
                pass

        def verify_canonical_ownership(self, path: Path) -> None:
            if not path.exists():
                raise PlatformIsolationError(
                    "canonical_missing",
                    f"Canonical path does not exist: {path}",
                )

        def create_relative_symlink(
            self, target: Path, link_path: Path,
        ) -> None:
            if target.is_absolute():
                raise PlatformIsolationError(
                    "absolute_target",
                    f"Symlink target must be relative, got absolute: {target}",
                )
            target_parts = str(target).split(os.sep)
            up_count = sum(1 for p in target_parts if p == "..")
            if up_count > 50:
                raise PlatformIsolationError(
                    "target_escape",
                    f"Excessive .. traversal",
                )
            if link_path.is_symlink():
                link_path.unlink()
            elif link_path.exists(follow_symlinks=False):
                if link_path.is_dir(follow_symlinks=False):
                    raise PlatformIsolationError(
                        "ordinary_dir_at_link_path",
                        "Expected symlink, found ordinary directory",
                    )
                else:
                    link_path.unlink()
            link_path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(target), str(link_path))

        def verify_workspace_link(
            self, link_path: Path, expected_target: Path, canonical_root: Path,
        ) -> bool:
            if not link_path.is_symlink():
                return False
            try:
                actual = Path(os.readlink(str(link_path)))
                actual_resolved = (link_path.parent / actual).resolve()
                expected_resolved = expected_target.resolve()
                if actual_resolved != expected_resolved:
                    return False
                try:
                    actual_resolved.relative_to(canonical_root.resolve())
                except ValueError:
                    return False
                return True
            except (OSError, ValueError):
                return False

        def is_valid_symlink(self, path: Path) -> bool:
            try:
                return path.is_symlink()
            except OSError:
                return False

        def make_file_readonly(self, path: Path) -> None:
            if path.exists():
                os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        def make_dir_readonly_executor(self, path: Path) -> None:
            if path.exists() and path.is_dir():
                os.chmod(path, stat.S_IRUSR | stat.S_IXUSR
                         | stat.S_IRGRP | stat.S_IXGRP
                         | stat.S_IROTH | stat.S_IXOTH)

        def launch_executor(
            self,
            cmd: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text: bool = True,
        ):
            """Test-mode launch: delegates to executors.subprocess.Popen
            so that test mocks on runtime.orchestrator.executors.subprocess
            intercept the call (the same module _run_command uses).
            """
            import runtime.orchestrator.executors as _exec_mod
            return _exec_mod.subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=text,
                env=env,
            )

    def _test_detect():
        if sys.platform == "darwin":
            return _TestMacOSIsolation()
        else:
            return _TestMacOSIsolation()  # fallback for test environments

    monkeypatch.setattr(
        "runtime.platform.isolation.detect_platform_isolation",
        _test_detect,
    )
    # `from X import Y` creates module-local names that are NOT updated
    # when X.Y is monkeypatched.  Sweep runtime.* modules for every
    # detect_platform_isolation reference that still points at the
    # original (_real_detect) and patch each one.  We deliberately
    # exclude tests.* — test_canonical_production_bound needs the real
    # detector for OS-provisioned-isolation tests (those are macOS-only
    # and already skip on non-darwin).
    for _mod_name, _mod in list(sys.modules.items()):
        if not _mod_name.startswith("runtime."):
            continue
        try:
            if getattr(_mod, "detect_platform_isolation", None) is _real_detect:
                monkeypatch.setattr(
                    f"{_mod_name}.detect_platform_isolation", _test_detect,
                )
        except Exception:
            pass
    # Explicit anchors for grep discoverability
    monkeypatch.setattr(
        "runtime.orchestrator.executors.detect_platform_isolation",
        _test_detect,
    )
    yield
    # Restore env var to its pre-test value
    if _prev_so_env is not None:
        os.environ["HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR"] = _prev_so_env
    else:
        os.environ.pop("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR", None)


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    return Settings(project_root=tmp_dir)


@pytest.fixture(autouse=True)
def _isolate_canonical_store(tmp_path: Path):
    """Point HAPPYRANCH_DAEMON_HOME at a temp dir for test isolation.

    The canonical skill store resolves its root from daemon_home. In tests,
    this must point at a temp directory so no real user state leaks.
    """
    import os
    old = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path / ".happyranch")
    yield
    if old is not None:
        os.environ["HAPPYRANCH_DAEMON_HOME"] = old
    else:
        os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)


@pytest.fixture
def test_runtime(tmp_dir: Path) -> OrgPaths:
    """OrgPaths rooted at <tmp>/runtime/orgs/test/.

    Historical name kept for backward compatibility — tests treat this as the
    single per-org root, not the multi-org container. The multi-org RuntimeDir
    is materialized at <tmp>/runtime/ so ``RuntimeDir.load`` could re-read it.
    """
    rt = RuntimeDir.init(tmp_dir / "runtime")
    return OrgPaths(root=rt.orgs_dir / "test")


@pytest.fixture
def db(tmp_dir: Path) -> Database:
    """A fresh Database instance backed by a temporary file."""
    return Database(tmp_dir / "test.db")


@pytest.fixture(autouse=True)
def _deterministic_throttle():
    """Install a no-spacing, no-backoff, roomy-ceiling executor throttle for the
    whole unit suite (issue #85).

    The real defaults (spacing 1.5s, backoff [5,15,45]) would make any test that
    launches a provider subprocess sleep on real wall-clock time. The dedicated
    throttle tests construct their own ``ProviderThrottle`` instances, so this
    global neutralization doesn't weaken their coverage.
    """
    from runtime.orchestrator import throttle

    throttle.set_throttle(
        throttle.ProviderThrottle(
            ceiling_default=64, spacing_seconds=0.0, backoff_seconds=()
        )
    )
    yield
    throttle.reset_throttle()
