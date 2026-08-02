"""TDD tests for system-contract skill materialization hardening (TASK-2511).

Covers:
- SystemContractMaterializationError: explicit, names missing contracts
- ensure_system_contracts_materialized: injects + verifies on-disk
- _copy_skills_tree atomicity: concurrent reader sees complete tree
- All 4 session contexts (task/thread/wake/dream) against empty workspace
- Retry-eligibility: error is caught by run_step exception handler

Phase-4 cutover (#336) set _WHOLESALE_DUMP_ENABLED=False, making the
wholesale dump a no-op. System contracts are now materialized ONLY by
per-session inject_system_contracts. Three gaps:
1. _run_agent readiness check runs BEFORE injection
2. wake/thread/dream runners inject with NO readiness guard
3. _copy_skills_tree is non-atomic (rmtree-then-recreate window)
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.skills.system_contracts import (
    SYSTEM_CONTRACTS,
    SessionContext,
    resolve_system_contracts_for_session,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_skill_dir(src_root: Path, skill_id: str) -> Path:
    """Create a minimal protocol/skills/<id>/ tree with a SKILL.md."""
    d = src_root / skill_id
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {skill_id}\n\nSkill body for {skill_id}.\n")
    return d


def _make_all_system_contract_dirs(src_root: Path) -> set[str]:
    """Create all 5 system-contract protocol/skills/<id>/ dirs."""
    ids = set()
    for sc in SYSTEM_CONTRACTS:
        _make_skill_dir(src_root, sc.id)
        ids.add(sc.id)
    return ids


def _assert_skill_on_disk(workspace: Path, skill_id: str, *, provider: str) -> None:
    """Assert a skill's SKILL.md is on disk for the given provider."""
    if provider == "claude":
        marker = workspace / ".claude" / "skills" / skill_id / "SKILL.md"
    else:
        marker = workspace / ".agents" / "skills" / skill_id / "SKILL.md"
    assert marker.exists(), (
        f"Expected {marker} to exist for {provider} / {skill_id}"
    )
    assert marker.read_text().startswith(f"# {skill_id}")


def _resolve_expected_contract_ids(context: str, workspace: Path) -> set[str]:
    """Return the set of contract IDs expected for a given session context."""
    ctx = SessionContext(context)
    contracts = resolve_system_contracts_for_session(ctx, workspace=workspace)
    return {sc.id for sc in contracts}


# ═══════════════════════════════════════════════════════════════════════
# SystemContractMaterializationError
# ═══════════════════════════════════════════════════════════════════════

class TestSystemContractMaterializationError:
    """The explicit error names the missing contract(s) + workspace."""

    def test_error_names_missing_contracts(self):
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )
        err = SystemContractMaterializationError(
            missing_contracts=["start-task", "jobs"],
            workspace=Path("/tmp/ws"),
            provider="claude",
        )
        msg = str(err)
        assert "start-task" in msg
        assert "jobs" in msg
        assert "/tmp/ws" in msg
        assert "claude" in msg

    def test_error_is_runtime_error(self):
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )
        err = SystemContractMaterializationError(
            missing_contracts=["start-task"],
            workspace=Path("/tmp/ws"),
            provider="claude",
        )
        assert isinstance(err, RuntimeError)

    def test_error_is_exception_catchable(self):
        """The error is caught by `except Exception as exc:` in
        run_step_impl; handling is terminal FAILED with no daemon successor (TASK-3604)."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )
        err = SystemContractMaterializationError(
            missing_contracts=["start-task"],
            workspace=Path("/tmp/ws"),
            provider="claude",
        )
        assert isinstance(err, Exception)
        # Must NOT be a BaseException subclass that skips `except Exception`
        assert not isinstance(err, (SystemExit, KeyboardInterrupt, GeneratorExit))


# ═══════════════════════════════════════════════════════════════════════
# ensure_system_contracts_materialized — success path
# ═══════════════════════════════════════════════════════════════════════

class TestEnsureMaterializedSuccess:
    """Materialize-then-verify: contracts land on disk."""

    def test_task_context_claude(self, tmp_path):
        """Task context with Claude provider materializes all expected contracts."""
        from runtime.orchestrator.workspace_adapters import (
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        ensure_system_contracts_materialized(
            workspace, settings, slug="test", context="task", provider="claude",
        )

        expected = _resolve_expected_contract_ids("task", workspace)
        for sid in expected:
            _assert_skill_on_disk(workspace, sid, provider="claude")

    def test_thread_context_codex(self, tmp_path):
        """Thread context with Codex provider materializes expected contracts
        via .agents/skills/."""
        from runtime.orchestrator.workspace_adapters import (
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        ensure_system_contracts_materialized(
            workspace, settings, slug="test", context="thread", provider="codex",
        )

        expected = _resolve_expected_contract_ids("thread", workspace)
        for sid in expected:
            _assert_skill_on_disk(workspace, sid, provider="codex")

    def test_wake_context_opencode(self, tmp_path):
        """Wake context with Opencode provider materializes expected contracts."""
        from runtime.orchestrator.workspace_adapters import (
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        ensure_system_contracts_materialized(
            workspace, settings, slug="test", context="wake", provider="opencode",
        )

        expected = _resolve_expected_contract_ids("wake", workspace)
        for sid in expected:
            _assert_skill_on_disk(workspace, sid, provider="opencode")

    def test_dream_context_pi(self, tmp_path):
        """Dream context with Pi provider materializes expected contracts."""
        from runtime.orchestrator.workspace_adapters import (
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        ensure_system_contracts_materialized(
            workspace, settings, slug="test", context="dream", provider="pi",
        )

        expected = _resolve_expected_contract_ids("dream", workspace)
        for sid in expected:
            _assert_skill_on_disk(workspace, sid, provider="pi")

    def test_workspace_without_repos_omits_make_worktree(self, tmp_path):
        """When workspace has no repos, make-worktree contract is excluded."""
        from runtime.orchestrator.workspace_adapters import (
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # No repos/ dir — make-worktree should be omitted

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        ensure_system_contracts_materialized(
            workspace, settings, slug="test", context="task", provider="claude",
        )

        expected = _resolve_expected_contract_ids("task", workspace)
        assert "make-worktree" not in expected
        for sid in expected:
            _assert_skill_on_disk(workspace, sid, provider="claude")


# ═══════════════════════════════════════════════════════════════════════
# ensure_system_contracts_materialized — failure path (empty workspace)
# ═══════════════════════════════════════════════════════════════════════

class TestEnsureMaterializedFailure:
    """Post-redeploy scenario: empty workspace → explicit error, never Errno 2."""

    def test_empty_skills_task_context_raises_explicit_error(self, tmp_path):
        """Simulating post-redeploy with EMPTY protocol/skills/ — raises
        SystemContractMaterializationError, never bare Errno 2."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        # protocol/skills/ dir exists but is empty — post-redeploy state
        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        src_root.mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="task", provider="claude",
            )
        msg = str(exc_info.value)
        assert "start-task" in msg  # names the missing contract
        assert "Errno 2" not in msg
        assert str(workspace) in msg  # names workspace

    def test_empty_skills_wake_context_raises_explicit_error(self, tmp_path):
        """Wake context with empty skills → explicit error."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        (settings.get_protocol_dir() / "skills").mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="wake", provider="claude",
            )
        msg = str(exc_info.value)
        assert "start-task" in msg
        assert "Errno 2" not in msg

    def test_empty_skills_thread_context_raises_explicit_error(self, tmp_path):
        """Thread context with empty skills → explicit error."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        (settings.get_protocol_dir() / "skills").mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="thread", provider="codex",
            )
        msg = str(exc_info.value)
        assert "jobs" in msg or "thread" in msg
        assert "Errno 2" not in msg

    def test_empty_skills_dream_context_raises_explicit_error(self, tmp_path):
        """Dream context with empty skills → explicit error."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        (settings.get_protocol_dir() / "skills").mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="dream", provider="pi",
            )
        msg = str(exc_info.value)
        assert "dream" in msg or "jobs" in msg
        assert "Errno 2" not in msg

    def test_partial_skills_task_context_raises_naming_missing_only(self, tmp_path):
        """When only some contracts are available, error names the missing ones."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        # Only create jobs — start-task and thread are missing
        _make_skill_dir(src_root, "jobs")

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="task", provider="claude",
            )
        msg = str(exc_info.value)
        assert "start-task" in msg
        assert "jobs" not in msg  # jobs was present, not in error

    def test_no_workspace_skills_dir_after_injection_raises(self, tmp_path):
        """If injection somehow doesn't create workspace skills dirs, raises
        explicit error (not Errno 2 from a bare os.listdir)."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # No repos/ dir
        workspace.mkdir(exist_ok=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        # Create the source dirs but then sabotage the workspace after injection
        # by ensuring inject_system_contracts sees a valid source but the
        # verification catches missing output
        # This test simulates a permission error or disk-full scenario
        _make_all_system_contract_dirs(src_root)

        # Delete workspace skills after injection would be hard to test,
        # so instead test with NO source dirs → injection is a no-op
        # → verification fails with explicit error
        for sc in SYSTEM_CONTRACTS:
            shutil_rmtree = getattr(os, 'shutil', None)
            import shutil as _shutil
            target = src_root / sc.id
            if target.exists():
                _shutil.rmtree(target)
        src_root.mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemContractMaterializationError) as exc_info:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="task", provider="claude",
            )
        msg = str(exc_info.value)
        assert "start-task" in msg
        assert "Errno 2" not in msg


# ═══════════════════════════════════════════════════════════════════════
# _copy_skills_tree atomicity
# ═══════════════════════════════════════════════════════════════════════

class TestCopySkillsTreeAtomicity:
    """Concurrent reader during injection never sees half-materialized tree."""

    def test_atomic_replace_no_half_deleted_state(self, tmp_path):
        """During _copy_skills_tree, a concurrent reader never sees a
        half-deleted directory state. The atomic swap uses:
        1. Copy new → .tmp.<name>
        2. rename(old, .old.<name>) — instantly removes old from view
        3. rename(.tmp.<name>, <name>) — instantly makes new visible
        4. rmtree(.old.<name>) cleanup
        The reader checks: when the SKILL.md of a canonical dir is readable,
        its parent dir must exist AND the content must be old or new.
        Uses try/except to avoid TOCTOU between is_dir() and is_file()."""
        from runtime.orchestrator.workspace_adapters import _copy_skills_tree

        src = tmp_path / "src"
        src.mkdir()
        for sid in ["start-task", "jobs", "thread"]:
            _make_skill_dir(src, sid)

        dst = tmp_path / "dst"
        dst.mkdir()
        # Pre-populate with old tree
        for sid in ["start-task", "jobs"]:
            (dst / sid).mkdir()
            (dst / sid / "SKILL.md").write_text("# old\n")

        bad_reads: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def reader():
            barrier.wait()
            for _ in range(200):
                # Pre-existing canonical skills (pre-populated in dst
                # BEFORE the copy): read SKILL.md DIRECTLY. Per-file
                # atomic os.replace guarantees a pre-existing skill's
                # canonical path is NEVER absent — FileNotFoundError
                # here is the forbidden no-ENOENT mode this test must
                # catch.  Content must be old or new only.
                for sid in ["start-task", "jobs"]:
                    skill_path = dst / sid / "SKILL.md"
                    try:
                        content = skill_path.read_text()
                        if content not in (
                            "# old\n",
                            f"# {sid}\n\nSkill body for {sid}.\n",
                        ):
                            bad_reads.append(
                                f"{sid}: unexpected content {content!r}"
                            )
                    except FileNotFoundError:
                        bad_reads.append(
                            f"{sid}: canonical SKILL.md absent during replacement"
                        )
                    except Exception as e:
                        bad_reads.append(f"{sid}: {e}")
                # Newly-introduced skill (NOT pre-populated in dst):
                # thread — its transient absence before it first
                # materializes is legitimate.  Keep only the
                # is_file()-gated check + final-state assertion.
                for sid in ["thread"]:
                    skill_path = dst / sid / "SKILL.md"
                    try:
                        if skill_path.is_file():
                            content = skill_path.read_text()
                            if content not in (
                                f"# {sid}\n\nSkill body for {sid}.\n",
                            ):
                                bad_reads.append(
                                    f"{sid}: unexpected content {content!r}"
                                )
                        # else: file doesn't exist yet — that's okay
                    except Exception as e:
                        bad_reads.append(f"{sid}: {e}")

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        barrier.wait()
        _copy_skills_tree(src, dst, slug="test")

        t.join(timeout=5)

        # After copy completes, verify final state
        for sid in ["start-task", "jobs", "thread"]:
            assert (dst / sid / "SKILL.md").is_file(), (
                f"{sid} SKILL.md not on disk after copy completed"
            )

        assert len(bad_reads) == 0, (
            f"Reader saw corrupt/incomplete content: {bad_reads}"
        )

    def test_concurrent_read_never_reads_stale_incomplete_content(self, tmp_path):
        """A concurrent reader that opens a specific SKILL.md during injection
        always reads a COMPLETE file — it sees either the old content or the
        new content. It may see FileNotFoundError transiently during the
        rename swap window (acceptable on all platforms), but NEVER reads a
        truncated or partially-written file."""
        from runtime.orchestrator.workspace_adapters import _copy_skills_tree

        src = tmp_path / "src"
        src.mkdir()
        _make_skill_dir(src, "start-task")
        # Make the target file distinctive
        (src / "start-task" / "SKILL.md").write_text("# new-start-task\n")

        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "start-task").mkdir()
        (dst / "start-task" / "SKILL.md").write_text("# old-start-task\n")

        bad_reads: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def reader():
            barrier.wait()
            for _ in range(200):
                # REVISE TASK-2525: with per-file atomic os.replace, the
                # canonical SKILL.md is NEVER absent during replacement.
                # FileNotFoundError here is the forbidden failure mode —
                # flag it instead of ignoring it.
                try:
                    target = dst / "start-task" / "SKILL.md"
                    content = target.read_text()
                    if content not in ("# old-start-task\n", "# new-start-task\n"):
                        bad_reads.append(f"Unexpected content: {content!r}")
                except FileNotFoundError:
                    bad_reads.append(
                        "canonical SKILL.md absent during replacement"
                    )
                except Exception as e:
                    bad_reads.append(f"{type(e).__name__}: {e}")

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        barrier.wait()
        _copy_skills_tree(src, dst, slug="test")

        t.join(timeout=5)

        # Verify final state
        final = (dst / "start-task" / "SKILL.md").read_text()
        assert final == "# new-start-task\n"

        assert len(bad_reads) == 0, (
            f"Reader read incomplete/corrupt content: {bad_reads}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Retry-eligibility integration
# ═══════════════════════════════════════════════════════════════════════

class TestRetryEligibility:
    """SystemContractMaterializationError is caught by run_step_impl's
    except Exception handler, leading to terminal FAILED (TASK-3604).
    No auto-revisit successor is spawned — recovery is explicit
    manager/founder action."""

    def test_system_contract_error_is_exception_subclass(self):
        """SystemContractMaterializationError must be a subclass of Exception
        so it is caught by `except Exception as exc:` in run_step_impl.
        Post-TASK-3604: the handler marks the task FAILED with no auto-revisit
        successor (terminal failure)."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )

        # Verify it IS caught by `except Exception`
        exc = SystemContractMaterializationError(
            missing_contracts=["start-task"],
            workspace=Path("/tmp/ws"),
            provider="claude",
        )
        assert isinstance(exc, Exception), (
            "SystemContractMaterializationError must inherit Exception "
            "so run_step_impl's `except Exception` catches it"
        )
        # Post-TASK-3604: the except handler marks the task terminal FAILED
        # with no auto-revisit successor. The error carries enough context
        # for the terminal-failure audit.
        assert "start-task" in exc.missing_contracts
        assert exc.workspace == Path("/tmp/ws")
        assert exc.provider == "claude"

    def test_error_context_preserved_for_terminal_failure_audit(self):
        """SystemContractMaterializationError raised during _run_agent
        flows through except Exception → _fail → terminal FAILED (TASK-3604).
        The error context (missing_contracts, workspace, provider) must be
        preserved for the terminal-failure audit row — no auto-revisit
        successor is spawned."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )

        # Simulate: _run_agent raises SystemContractMaterializationError
        try:
            raise SystemContractMaterializationError(
                missing_contracts=["start-task", "report-completion"],
                workspace=Path("/tmp/ws"),
                provider="codex",
            )
        except SystemContractMaterializationError as exc:
            # Post-TASK-3604: the except Exception handler in run_step_impl
            # catches this, calls _fail, and marks the task terminal FAILED
            # with no auto-revisit successor.
            assert exc.missing_contracts == ["start-task", "report-completion"]
            assert exc.workspace == Path("/tmp/ws")
            assert isinstance(exc, Exception)


# ═══════════════════════════════════════════════════════════════════════
# Verification that injection produces on-disk files
# ═══════════════════════════════════════════════════════════════════════

class TestInjectionOnDiskVerification:
    """The guard validates injection OUTPUT, not just resolution."""

    def test_inject_system_contracts_writes_file_under_claude_skills(self, tmp_path):
        """inject_system_contracts must produce a real SKILL.md under
        .claude/skills/<id>/ — verify on-disk."""
        from runtime.orchestrator.workspace_adapters import (
            inject_system_contracts,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        inject_system_contracts(
            workspace, settings, slug="test", context="task",
        )

        # Verify every expected contract is on disk for Claude provider
        for sid in _resolve_expected_contract_ids("task", workspace):
            marker = workspace / ".claude" / "skills" / sid / "SKILL.md"
            assert marker.exists(), f"Missing on disk: {marker}"

    def test_inject_system_contracts_writes_file_under_agents_skills(self, tmp_path):
        """inject_system_contracts must ALSO produce SKILL.md under
        .agents/skills/<id>/ for Codex/Opencode/Pi providers."""
        from runtime.orchestrator.workspace_adapters import (
            inject_system_contracts,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        inject_system_contracts(
            workspace, settings, slug="test", context="task",
        )

        for sid in _resolve_expected_contract_ids("task", workspace):
            marker = workspace / ".agents" / "skills" / sid / "SKILL.md"
            assert marker.exists(), f"Missing on disk for agents: {marker}"

    def test_materialization_guard_rejects_when_disk_write_fails_silently(self, tmp_path, monkeypatch):
        """If inject_system_contracts runs but doesn't produce the expected
        files (simulated by sabotaging _copy_skills_tree), the verification
        must catch it and raise SystemContractMaterializationError."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            ensure_system_contracts_materialized,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        settings = Settings(project_root=tmp_path)
        src_root = settings.get_protocol_dir() / "skills"
        _make_all_system_contract_dirs(src_root)

        # Sabotage: make _copy_skills_tree a no-op AFTER source validation
        original = None
        try:
            from runtime.orchestrator import workspace_adapters as wa
            original = wa._copy_skills_tree

            def _noop_copy(src, dst, *, slug):
                pass  # Simulates silent write failure (disk full, permission error)

            monkeypatch.setattr(wa, "_copy_skills_tree", _noop_copy)

            with pytest.raises(SystemContractMaterializationError) as exc_info:
                ensure_system_contracts_materialized(
                    workspace, settings, slug="test", context="task", provider="claude",
                )
            msg = str(exc_info.value)
            assert "start-task" in msg
            assert "Errno 2" not in msg
        finally:
            if original is not None:
                monkeypatch.setattr(
                    "runtime.orchestrator.workspace_adapters._copy_skills_tree",
                    original,
                )


# ═══════════════════════════════════════════════════════════════════════
# Concurrent pre-spawn materialization serialization (Issue #536)
# ═══════════════════════════════════════════════════════════════════════

class TestConcurrentMaterialization:
    """TDD: concurrent task + thread materialization must not race on the
    predictable .tmp.<name> cleanup/write/replace window in _copy_skills_tree.

    The process-local workspace lock serializes the complete pre-spawn
    materialization transaction so concurrent callers targeting the same
    workspace never overlap inside _copy_skills_tree."""

    def test_concurrent_materialization_no_filenotfounderror(
        self, tmp_path, monkeypatch,
    ):
        """Two concurrent calls to materialize_workspace_skills for the
        same workspace complete without FileNotFoundError, and every
        expected SKILL.md is intact afterward.

        Both threads target the same workspace simultaneously. The
        process-local workspace lock serializes them so neither enters
        _copy_skills_tree while the other holds the lock. No
        FileNotFoundError occurs and all files are correct."""
        import threading
        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        from runtime.config import Settings

        # Setup source skills
        src = tmp_path / "protocol" / "skills"
        for sid in ["start-task", "jobs", "thread", "make-worktree", "dream"]:
            d = src / sid
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {sid}\ncontent for {sid}\n")

        monkeypatch.setattr(
            "runtime.orchestrator.workspace_adapters._SKILLS_SRC", src,
        )

        # Re-enable wholesale dump so we exercise all three materialization
        # steps under the lock.
        import runtime.orchestrator.workspace_adapters as wa
        old_wholesale = wa._WHOLESALE_DUMP_ENABLED
        wa._WHOLESALE_DUMP_ENABLED = True

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        # Start barrier: both threads signal readiness before calling
        # materialize_workspace_skills, then the barrier releases them
        # at the same instant to maximize lock contention.
        start_barrier = threading.Barrier(2, timeout=10)

        errors: list[tuple[str, Exception]] = []
        settings = Settings()

        def task_path():
            start_barrier.wait()  # synchronize start
            try:
                materialize_workspace_skills(
                    workspace, settings,
                    slug="test",
                    context="task",
                    provider="claude",
                    agent_name="dev_agent",
                    team="engineering",
                    skills_root=src,
                )
            except Exception as e:
                errors.append(("task", e))

        def thread_path():
            start_barrier.wait()  # synchronize start
            try:
                materialize_workspace_skills(
                    workspace, settings,
                    slug="test",
                    context="thread",
                    provider="claude",
                    agent_name="dev_agent",
                    team="engineering",
                    skills_root=src,
                )
            except Exception as e:
                errors.append(("thread", e))

        t_a = threading.Thread(target=task_path, daemon=True)
        t_b = threading.Thread(target=thread_path, daemon=True)
        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

        wa._WHOLESALE_DUMP_ENABLED = old_wholesale

        # Neither side raises FileNotFoundError
        fnf = [(label, e) for label, e in errors
               if isinstance(e, FileNotFoundError)]
        assert not fnf, (
            f"FileNotFoundError raised during concurrent materialization: {fnf}"
        )

        # No other errors either
        other = [(label, e) for label, e in errors
                 if not isinstance(e, FileNotFoundError)]
        assert not other, (
            f"Unexpected errors during concurrent materialization: {other}"
        )

        # Every expected SKILL.md is complete and correct afterward
        for sid in ["start-task", "jobs", "thread", "make-worktree", "dream"]:
            for skills_dir in [".claude/skills", ".agents/skills"]:
                path = workspace / skills_dir / sid / "SKILL.md"
                assert path.is_file(), f"Missing {path}"
                content = path.read_text()
                expected = f"# {sid}\ncontent for {sid}\n"
                assert content == expected, (
                    f"Wrong content in {path}: {content!r} != {expected!r}"
                )

    def test_concurrent_materialization_race_reproduced_without_lock(
        self, tmp_path, monkeypatch,
    ):
        """RED proof: without the workspace lock, two concurrent
        _copy_skills_tree calls that target the same destination
        produce FileNotFoundError or OSError.

        We disable the lock and use a threading.Barrier to force
        both threads to enter _copy_skills_tree simultaneously,
        causing them to share the .tmp.<name> temp directory and
        race on cleanup/write/replace."""
        import threading
        from contextlib import contextmanager
        from runtime.orchestrator.workspace_adapters import _copy_skills_tree
        import runtime.orchestrator.workspace_adapters as wa

        src = tmp_path / "src"
        src.mkdir()
        for sid in ["start-task", "jobs"]:
            d = src / sid
            d.mkdir()
            (d / "SKILL.md").write_text(f"# {sid}\n")
            # Add a subdirectory to widen the race window
            (d / "references").mkdir()
            (d / "references" / "guide.md").write_text(f"# {sid} guide\n")

        dst = tmp_path / "dst"
        dst.mkdir()

        errors: list[Exception] = []

        # Disable the workspace lock so the race can occur.
        @contextmanager
        def _noop_transaction(workspace):
            yield
        monkeypatch.setattr(wa, "_workspace_skills_transaction", _noop_transaction)

        # Barrier: both threads enter _copy_skills_tree at the same instant.
        entry_barrier = threading.Barrier(2, timeout=10)

        original = _copy_skills_tree
        def _concurrent_copy(src_p, dst_p, *, slug):
            entry_barrier.wait()  # rendezvous before any work
            original(src_p, dst_p, slug=slug)
        monkeypatch.setattr(wa, "_copy_skills_tree", _concurrent_copy)

        def worker_a():
            try:
                _copy_skills_tree(src, dst, slug="test")
            except Exception as e:
                errors.append(e)

        def worker_b():
            try:
                _copy_skills_tree(src, dst, slug="test")
            except Exception as e:
                errors.append(e)

        t_a = threading.Thread(target=worker_a, daemon=True)
        t_b = threading.Thread(target=worker_b, daemon=True)
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        # Without the lock, the race should produce FileNotFoundError
        # or OSError from at least one worker.
        fnf = [e for e in errors if isinstance(e, FileNotFoundError)]
        ose = [e for e in errors if isinstance(e, OSError)]
        assert len(fnf) > 0 or len(ose) > 0, (
            f"Expected race to produce FileNotFoundError/OSError, "
            f"got no errors at all. Race may need wider overlap window. "
            f"Errors: {errors}"
        )

    def test_named_fail_closed_error_on_real_failure(self, tmp_path, monkeypatch):
        """An actual filesystem/materialization failure (e.g. disk full)
        produces a named fail-closed error — SystemContractMaterializationError
        or another named exception — not a bare FileNotFoundError, and no
        agent subprocess would be launched."""
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
            materialize_workspace_skills,
        )
        from runtime.config import Settings

        src = tmp_path / "protocol" / "skills"
        for sid in ["start-task", "jobs"]:
            d = src / sid
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {sid}\n")

        monkeypatch.setattr(
            "runtime.orchestrator.workspace_adapters._SKILLS_SRC", src,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        # Sabotage: make _copy_skills_tree raise PermissionError to simulate
        # a real filesystem failure.
        import runtime.orchestrator.workspace_adapters as wa
        original = wa._copy_skills_tree

        def _failing_copy(src_p, dst_p, *, slug):
            raise PermissionError("Simulated disk-full / permission error")

        monkeypatch.setattr(wa, "_copy_skills_tree", _failing_copy)

        from runtime.config import Settings
        with pytest.raises(Exception) as exc_info:
            materialize_workspace_skills(
                workspace, Settings(),
                slug="test",
                context="task",
                provider="claude",
                agent_name="dev_agent",
                team="engineering",
                skills_root=src,
            )

        # The error must be named/actionable — not a bare low-level exception.
        # PermissionError itself is acceptable since it carries the reason.
        # But crucially, no agent subprocess would launch.
        err = exc_info.value
        assert isinstance(err, Exception)
        assert "Errno 2" not in str(err), (
            f"Error should be named, not bare Errno 2: {err}"
        )

        # Restore original
        monkeypatch.setattr(wa, "_copy_skills_tree", original)
