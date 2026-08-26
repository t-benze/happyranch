from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


# Standard test agents that many modules invoke through _run_agent or
# run_invocation. Under THR-095/TASK-5293 launch is fail-closed, so tests
# that previously relied on a silent claude fallback now need active
# AgentDef frontmatter. Modules with special agent requirements can
# override or supplement this list in their own fixtures.
_TEST_AGENT_NAMES = (
    "engineering_head", "product_manager", "dev_agent", "payment_agent",
    "qa_engineer", "senior_dev", "content_head", "content_agent",
    "alice", "bob",
)


def seed_test_agents(paths: OrgPaths, names: tuple[str, ...] | None = None) -> None:
    """Write active AgentDef frontmatter for test agents under ``paths``."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    for name in (names or _TEST_AGENT_NAMES):
        ad = AgentDef(
            name=name, team="engineering", role="manager",
            executor="claude", allow_rules=(), repos={},
            enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
            system_prompt=f"You are {name}.", description="", model=None,
        )
        (paths.agents_dir / f"{name}.md").write_text(render_agent_text(ad))


# ── Test-mode platform isolation ──────────────────────────────────────
# Test environments use a same-owner launch double so subprocess mocks remain
# interceptable on both supported platforms.
# This fixture monkeypatches detect_platform_isolation to return a
# test-mode isolation that permits same-owner launches (the user running
# the tests IS both daemon and executor). Direct platform evidence lives in
# test_canonical_production_bound.py and the Linux platform operation tests.

_TEST_ISOLATION_FIXTURE_ACTIVE = True


@pytest.fixture(autouse=True)
def _test_mode_platform_isolation(monkeypatch):
    """Install a test-mode platform detector that permits same-owner launches.

    Set HAPPYRANCH_TEST_REAL_PLATFORM=1 in platform-specific CI to exercise
    the production adapter throughout the canonical-store suites.
    """
    import os

    if (
        not _TEST_ISOLATION_FIXTURE_ACTIVE
        or os.environ.get("HAPPYRANCH_TEST_REAL_PLATFORM") == "1"
    ):
        yield
        return

    from runtime.platform.isolation import (
        PlatformIsolationError,
        _PosixSameOwnerIsolation,
        detect_platform_isolation as _real_detect,
    )
    import subprocess
    import sys

    class _TestPlatformIsolation(_PosixSameOwnerIsolation):
        """Test-mode same-owner isolation for unit tests.

        The test process runs as both daemon and executor — the executor
        and daemon share the same OS identity.

        The link-writer surface (``create_relative_symlink``,
        ``withdraw_workspace_link``, ``admit_skills_directory``,
        ``verify_workspace_link``) is INHERITED from the production POSIX
        implementation so the unit suite exercises the REAL THR-190 PR-B
        containment enforcement (no-follow admission, resolved-parent
        containment, atomic pinned-dirfd writes). A test-only writer that
        bypassed containment would create a false green — deliberately not
        done here. Only the executor launcher is overridden: it delegates to
        ``executors.subprocess.Popen`` so test mocks intercept the call (the
        same module ``_run_command`` uses).
        """

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
        return _TestPlatformIsolation()

    monkeypatch.setattr(
        "runtime.platform.isolation.detect_platform_isolation",
        _test_detect,
    )
    # `from X import Y` creates module-local names that are NOT updated
    # when X.Y is monkeypatched.  Sweep runtime.* modules for every
    # detect_platform_isolation reference that still points at the
    # original (_real_detect) and patch each one.
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


@pytest.fixture(autouse=True)
def _isolate_org_slug():
    """Clear HAPPYRANCH_ORG_SLUG so tests aren't contaminated by ambient env.

    The CLI's ``resolve_org_slug`` checks HAPPYRANCH_ORG_SLUG before falling
    back to the mock-controlled available-orgs list.  An ambient value (e.g.
    ``happyranch`` from a developer shell) overrides test mocks and causes
    org-slug mismatches in 10 test_cli tests.

    Dedicated resolve-org-slug tests that need the env var set it explicitly
    via monkeypatch and are unaffected.
    """
    import os
    old = os.environ.get("HAPPYRANCH_ORG_SLUG")
    os.environ.pop("HAPPYRANCH_ORG_SLUG", None)
    yield
    if old is not None:
        os.environ["HAPPYRANCH_ORG_SLUG"] = old
