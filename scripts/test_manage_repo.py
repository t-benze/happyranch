"""Focused manage-repo HTTP/CLI and Git regressions, outside retired units."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, nullcontext
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import httpx

from cli.commands import agents as cli_agents
from runtime.config import Settings
from runtime.daemon.routes import agents
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.context_builder import ContextBuilder


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True,
                            capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


class ManageRepoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git_environment = patch.dict("os.environ", {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        })
        self.git_environment.start()
        self.addCleanup(self.git_environment.stop)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.seed.mkdir()
        git(self.root, "init", "--bare", str(self.origin))
        git(self.seed, "init", "-b", "main")
        (self.seed / "file").write_text("initial\n")
        git(self.seed, "add", "file")
        git(self.seed, "commit", "-m", "initial")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        git(self.seed, "push", "-u", "origin", "main")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.paths = OrgPaths(root=self.root / "org-runtime")
        self.workspace = self.paths.workspaces_dir / "worker"
        self.workspace.mkdir(parents=True)
        (self.workspace / "repos").mkdir()
        self.clone = self.workspace / "repos" / "sample"
        git(self.workspace, "clone", str(self.origin), str(self.clone))
        self.linked = self.clone / ".claude" / "worktrees" / "task with spaces"
        git(self.clone, "worktree", "add", "-b", "task", str(self.linked), "HEAD")
        self.paths.agents_dir.mkdir(parents=True)
        self.canonical = self.paths.agents_dir / "worker.md"
        self.save_config({"sample": str(self.origin)})
        self.org = SimpleNamespace(root=self.paths.root, slug="synthetic",
                                   settings=Settings(project_root=self.root), teams_lock=asyncio.Lock())
        self.builder = ContextBuilder(self.org.settings, self.paths, slug=self.org.slug)
        self.interval = SimpleNamespace(validation=nullcontext, canonical_change=nullcontext,
                                        native_disposition=SimpleNamespace(uncertain=False))
        self.active = False
        self.bootstrap_calls = 0

        @asynccontextmanager
        async def writer(*args, **kwargs):
            self.active = True
            try:
                yield self.interval
            finally:
                self.active = False

        def bootstrap(*args, **kwargs):
            if not self.active:
                raise AssertionError("writer released before bootstrap")
            self.bootstrap_calls += 1

        self.patches = [patch.object(agents, "_consumer_writer_interval", writer),
                        patch.object(agents, "ContextBuilder", return_value=self.builder),
                        patch.object(self.builder, "ensure_workspace_ready", side_effect=bootstrap)]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = FastAPI()

        @self.app.post("/repos")
        async def endpoint(body: agents.ManageRepoBody) -> dict:
            return await agents.manage_repo("synthetic", "worker", body, self.org)

    def save_config(self, repos: dict[str, str]) -> None:
        self.canonical.write_text(render_agent_text(AgentDef(
            name="worker", team="engineering", role="worker", executor="claude",
            allow_rules=(), repos=repos, enrolled_by=None, enrolled_at_task=None,
            enrolled_at=None, system_prompt="Synthetic worker")))

    async def request(self, action: str, url: str | None = None) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                    base_url="http://synthetic") as client:
            return await client.post("/repos", json={"action": action, "repo_name": "sample",
                                                    "url": url or str(self.origin)})

    async def refused(self, action: str, reason: str) -> None:
        before = self.canonical.read_bytes()
        head = git(self.linked, "rev-parse", "HEAD") if self.linked.exists() else None
        result = await self.request(action)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn(str(self.linked), result.json()["detail"])
        self.assertIn(reason, result.json()["detail"])
        self.assertEqual(self.canonical.read_bytes(), before)
        self.assertTrue((self.clone / ".git").is_dir())
        if head is not None:
            self.assertEqual(git(self.linked, "rev-parse", "HEAD"), head)
        self.assertEqual(self.bootstrap_calls, 0)
        self.assertFalse(self.active)

    async def test_dirty_linked_worktree_refused_before_config_write(self) -> None:
        for leaf in ("file", "untracked", "submodule-local", "submodule-global", "submodule-ignore"):
            for action in ("remove", "update"):
                with self.subTest(leaf=leaf, action=action):
                    # Independent clones keep a destructive regression in one
                    # subcase from masking the remaining preservation cases.
                    case = ManageRepoTests()
                    case.setUp()
                    try:
                        if leaf.startswith("submodule-"):
                            git(case.linked, "-c", "protocol.file.allow=always", "submodule", "add",
                                str(case.origin), "vendor")
                            git(case.linked, "commit", "-am", "add submodule")
                            git(case.linked, "push", "origin", "HEAD:refs/heads/task")
                            if leaf == "submodule-global":
                                global_config = case.root / "gitconfig"
                                global_config.write_text("[diff]\n\tignoreSubmodules = all\n")
                                case.enterContext(patch.dict("os.environ", {"GIT_CONFIG_GLOBAL": str(global_config)}))
                            else:
                                key = "submodule.vendor.ignore" if leaf == "submodule-ignore" else "diff.ignoreSubmodules"
                                git(case.linked, "config", key, "all")
                            path = case.linked / "vendor" / "file"
                            path.write_text("unsaved\n")
                            self.assertEqual(git(case.linked, "status", "--porcelain", "--untracked-files=all"), "")
                            self.assertIn("M file", git(path.parent, "status", "--porcelain"))
                            self.assertEqual(git(case.linked, "rev-list", "HEAD", "--not", "--remotes"), "")
                        else:
                            path = case.linked / leaf
                            path.write_text("unsaved\n")
                        await case.refused(action, "uncommitted or untracked")
                        self.assertEqual(path.read_text(), "unsaved\n")
                    finally:
                        case.doCleanups()

    async def test_unpushed_linked_worktree_refused(self) -> None:
        for detached in (False, True):
            with self.subTest(detached=detached):
                if detached:
                    git(self.linked, "checkout", "--detach")
                (self.linked / "file").write_text(f"local {detached}\n")
                git(self.linked, "commit", "-am", "local")
                for action in ("remove", "update"):
                    await self.refused(action, "not reachable from any remote-tracking ref")

    async def test_clean_pushed_linked_worktree_allowed(self) -> None:
        for action in ("remove", "update"):
            with self.subTest(action=action):
                # Each operation deliberately deletes the whole old clone.
                if not self.linked.exists():
                    git(self.workspace, "clone", str(self.origin), str(self.clone))
                    git(self.clone, "worktree", "add", "-b", "task", str(self.linked), "origin/task")
                (self.linked / "file").write_text(action + "\n")
                git(self.linked, "commit", "-am", action)
                git(self.linked, "push", "origin", "HEAD:refs/heads/task")
                self.save_config({"sample": str(self.origin)})
                result = await self.request(action)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertFalse(self.linked.exists())
                self.assertFalse(self.interval.native_disposition.uncertain)

    async def test_git_failure_refuses_and_preserves_config(self) -> None:
        for metadata in ("missing", "cyclic", "dangling", "lookup-error"):
            for action in ("remove", "update"):
                with self.subTest(metadata=metadata, action=action):
                    case = ManageRepoTests()
                    case.setUp()
                    try:
                        edited = case.linked / "file"
                        edited.write_text("unsaved\n")
                        gitdir = case.clone / ".git"
                        if metadata != "lookup-error":
                            gitdir.rename(case.clone / "saved-git-metadata")
                            if metadata in ("cyclic", "dangling"):
                                gitdir.symlink_to(".git" if metadata == "cyclic" else "absent-git-metadata")
                        real_stat = os.stat

                        def stat(path, *args, **kwargs):
                            if metadata == "lookup-error" and Path(path) == gitdir:
                                raise PermissionError("synthetic unreadable Git metadata")
                            return real_stat(path, *args, **kwargs)

                        before = case.canonical.read_bytes()
                        with patch("os.stat", stat):
                            result = await case.request(action)
                        self.assertEqual(result.status_code, 409, result.text)
                        self.assertIn(str(case.clone), result.json()["detail"])
                        self.assertIn("cannot verify linked worktrees", result.json()["detail"])
                        self.assertEqual(case.canonical.read_bytes(), before)
                        self.assertEqual(edited.read_text(), "unsaved\n")
                        self.assertEqual(case.bootstrap_calls, 0)
                        self.assertFalse(case.active)

                    finally:
                        case.doCleanups()

        real_run = subprocess.run
        for command in ("worktree", "status", "rev-list"):
            for failure in ("exit", "timeout", "oserror"):
                with self.subTest(command=command, failure=failure):
                    def run(args, *positional, **kwargs):
                        if command in args:
                            self.assertGreater(kwargs["timeout"], 0)
                            self.assertLessEqual(kwargs["timeout"], 5)
                            if failure == "timeout":
                                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
                            if failure == "oserror":
                                raise OSError("synthetic Git unavailable")
                            if kwargs.get("check"):
                                raise subprocess.CalledProcessError(128, args, output="", stderr="synthetic failure")
                            return subprocess.CompletedProcess(args, 128, stdout="", stderr="synthetic failure")
                        return real_run(args, *positional, **kwargs)
                    with patch("subprocess.run", side_effect=run):
                        for action in ("remove", "update"):
                            # Discovery cannot identify linked paths on a list failure.
                            before = self.canonical.read_bytes()
                            result = await self.request(action)
                            self.assertEqual(result.status_code, 409, result.text)
                            self.assertIn(str(self.clone if command == "worktree" else self.linked), result.text)
                            self.assertEqual(self.canonical.read_bytes(), before)
                            self.assertTrue(self.linked.exists())

    async def test_all_offending_paths_and_reasons_reported(self) -> None:
        other = self.root / "external worktree"
        git(self.clone, "worktree", "add", "--detach", str(other), "HEAD")
        (other / "untracked").write_text("unsaved\n")
        (self.linked / "file").write_text("local\n")
        git(self.linked, "commit", "-am", "local")
        for action in ("remove", "update"):
            before = self.canonical.read_bytes()
            response = await self.request(action)
            self.assertEqual(response.status_code, 409, response.text)
            detail = response.json()["detail"]
            self.assertIn(str(other), detail)
            self.assertIn(str(self.linked), detail)
            self.assertIn("uncommitted or untracked", detail)
            self.assertIn("not reachable from any remote-tracking ref", detail)
            self.assertEqual(self.canonical.read_bytes(), before)

    async def test_missing_worktree_refuses(self) -> None:
        missing = self.linked.with_name("relocated")
        self.linked.rename(missing)
        for action in ("remove", "update"):
            await self.refused(action, "missing or prunable")

    async def test_failed_pull_returns_false_and_logs_stderr(self) -> None:
        (self.clone / "local").write_text("local\n")
        git(self.clone, "add", "local")
        git(self.clone, "commit", "-m", "local")
        (self.seed / "remote").write_text("remote\n")
        git(self.seed, "add", "remote")
        git(self.seed, "commit", "-m", "remote")
        git(self.seed, "push", "origin", "main")
        with self.assertLogs("runtime.orchestrator.context_builder", level="WARNING") as logs:
            self.assertFalse(self.builder.clone_repo(self.workspace, "sample", str(self.origin)))
        self.assertIn("fatal:", "\n".join(logs.output))

    async def test_failed_clone_or_pull_is_http_failure_and_cli_nonzero(self) -> None:
        for action in ("add", "update"):
            with self.subTest(action=action):
                self.save_config({} if action == "add" else {"sample": str(self.origin)})
                # Existing-clone add exercises a failed pull; update a failed clone.
                url = str(self.root / "missing-origin.git")
                if action == "add":
                    git(self.clone, "remote", "set-url", "origin", url)
                response = await self.request(action, url)
                self.assertEqual(response.status_code, 502, response.text)
                self.assertIn("frontmatter entry WAS saved", response.json()["detail"])
                self.assertIn("clone/pull failed", response.json()["detail"])
                self.assertIn(url, self.canonical.read_text())
                self.assertTrue(self.interval.native_disposition.uncertain)
                self.assertFalse(self.active)
                self.assertEqual(self.bootstrap_calls, 1 if action == "add" else 2)
                args = argparse.Namespace(org="synthetic", from_file=None, agent="worker",
                                          action=action, repo_name="sample", url=url)
                client = SimpleNamespace(post=lambda *args, **kwargs: response)
                with patch.object(cli_agents.OpcClient, "from_env", return_value=client), \
                        patch("sys.stdout", new_callable=io.StringIO) as output:
                    with self.assertRaises(SystemExit) as exit_context:
                        cli_agents.cmd_manage_repo(args)
                self.assertEqual(exit_context.exception.code, 1)
                self.assertIn("502", output.getvalue())
                self.assertIn("frontmatter entry WAS saved", output.getvalue())
                self.assertNotIn("ok:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
