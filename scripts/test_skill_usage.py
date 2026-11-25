"""Focused synthetic skill observations, outside the retired Python suite."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.infrastructure.database import Database
from runtime.orchestrator.executors import ExecutorResult
from runtime.skills import usage


class SkillUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        home = patch.object(Path, "home", return_value=self.root)
        home.start()
        self.addCleanup(home.stop)
        self.db = Database(self.root / "org.db")
        self.addCleanup(self.db.close)
        self.prompt = "Synthetic invocation sess-current"
        project = re.sub(r"[^a-zA-Z0-9]", "-", str(self.workspace))
        self.transcript = self.root / ".claude" / "projects" / project / "provider-current.jsonl"
        self.transcript.parent.mkdir(parents=True)

    def user(self, prompt: str) -> dict:
        return {"type": "user", "sessionId": "provider-current", "message": {"content": prompt}}

    def skill(self, slug: str, tool_id: str) -> dict:
        return {"type": "assistant", "sessionId": "provider-current", "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": slug}}
        ]}}

    def write(self, events: list[dict]) -> None:
        self.transcript.write_text("".join(json.dumps(e) + "\n" for e in events))

    def capture(self, provider: str = "claude", events: list[dict] | None = None) -> usage.SkillUseCapture:
        return usage.SkillUseCapture(provider, self.workspace, self.prompt,
                                     "".join(json.dumps(e) + "\n" for e in (events or [])))

    def record(self, capture: usage.SkillUseCapture, **kwargs: object) -> None:
        fields = dict(org_slug="synthetic-org", agent_name="agent-permanent", task_id="TASK-synthetic",
                      session_context="task", session_id="sess-current", executor=capture.provider,
                      provider_id="provider-current")
        fields.update(kwargs)
        usage._record_skill_use(self.db.db_path, capture, **fields)

    def rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            return conn.execute("SELECT skill_slug, signal_kind, session_id FROM skill_use_events ORDER BY id").fetchall()

    def command(self, ident: str, command: str, exit_code: int = 0) -> dict:
        return {"type": "item.completed", "item": {"id": ident, "type": "command_execution",
                "command": command, "status": "completed", "exit_code": exit_code}}

    def test_claude_window_custom_attribution_and_reparse(self) -> None:
        # Historical and later turns share the provider file, never the invocation.
        self.write([self.user("old"), self.skill("old-skill", "tool-old"), self.user(self.prompt),
                    self.skill("custom-note", "tool-current"), {"type": "file-history-snapshot", "snapshot": {}},
                    self.user("later"),
                    self.skill("later-skill", "tool-later")])
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,policy_class,origin_kind,created_at,created_by) "
                         "VALUES ('CS-synthetic','synthetic-org','custom-note','Note','standard_operational','human','now','founder')")
        for context in ("task", "thread", "wake", "dream"):
            for _ in range(2):
                self.record(self.capture(), session_context=context, session_id=f"sess-{context}",
                            task_id="TASK-synthetic" if context == "task" else None)
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            rows = conn.execute("SELECT org_slug,agent_name,task_id,session_context,session_id,executor,"
                                "skill_slug,skill_source,custom_skill_id,signal_kind,provider_session_id,source_ref "
                                "FROM skill_use_events ORDER BY id").fetchall()
        self.assertEqual(rows, [
            ("synthetic-org", "agent-permanent", "TASK-synthetic" if c == "task" else None, c,
             f"sess-{c}", "claude", "custom-note", "custom", "CS-synthetic", "invoked",
             "provider-current", "claude:provider-current:tool-current")
            for c in ("task", "thread", "wake", "dream")
        ])

    def test_codex_opens_bundled_unknown_and_rejects_mentions(self) -> None:
        events = [{"type": "thread.started", "thread_id": "provider-current"},
                  self.command("item-1", f"cat {self.workspace}/.agents/skills/start-task/SKILL.md"),
                  self.command("item-2", f"/bin/zsh -lc 'sed -n 1,20p {self.workspace}/.claude/skills/external-note/SKILL.md'"),
                  self.command("item-3", f"cat {self.workspace}/.agents/skills/failed/SKILL.md", 1),
                  self.command("item-4", f"echo {self.workspace}/.agents/skills/mentioned/SKILL.md"),
                  self.command("item-5", "cat /elsewhere/.agents/skills/foreign/SKILL.md"),
                  self.command("item-6", f"false; cat {self.workspace}/.agents/skills/compound/SKILL.md"),
                  self.command("item-7", f"cat {self.workspace}/.agents/skills/../unsafe/SKILL.md"),
                  self.command("item-8", f"cat # {self.workspace}/.agents/skills/commented/SKILL.md")]
        capture = self.capture("codex", events)
        self.record(capture)
        self.record(capture)
        self.assertEqual(self.rows(), [("start-task", "opened_skill_file", "sess-current"),
                                      ("external-note", "opened_skill_file", "sess-current")])
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT skill_source FROM skill_use_events ORDER BY id").fetchall(),
                             [("bundled",), ("unknown",)])

    def test_claude_production_dimensions_record_invocation(self) -> None:
        self.prompt = "Synthetic daemon prompt " + "p" * 310_000
        large = {"type": "file-history-snapshot", "snapshot": {"padding": "m" * 310_000}}
        result = {"type": "user", "sessionId": "provider-current", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool-read", "content": "r" * 310_000}
        ]}}
        self.write([self.user(self.prompt), *[large] * 3,
                    *[{"type": "file-history-snapshot", "snapshot": {}}] * 50,
                    self.skill("start-task", "tool-current"), *[result] * 4])
        data = self.transcript.read_bytes()
        self.assertGreater(len(data), 2_400_000)
        self.assertLess(len(data), 2_600_000)
        self.assertEqual(sum(len(line) > 300_000 for line in data.splitlines()), 8)
        self.record(self.capture())
        self.assertEqual(self.rows(), [("start-task", "invoked", "sess-current")])

    def test_codex_alternate_cwd_rejects_relative_and_accepts_absolute(self) -> None:
        relative = ".agents/skills/start-task/SKILL.md"
        other = self.root / "other-directory"
        unrelated = other / relative
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("Synthetic unrelated skill\n")
        expected = self.workspace / relative
        self.assertFalse(expected.exists())
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,policy_class,origin_kind,created_at,created_by) "
                         "VALUES ('CS-original','synthetic-org','start-task','Start','standard_operational','human','now','founder')")
        read = subprocess.run(["cat", relative], cwd=other, capture_output=True, text=True, check=True)
        self.assertEqual(read.stdout, "Synthetic unrelated skill\n")
        started = {"type": "thread.started", "thread_id": "provider-current"}
        self.record(self.capture("codex", [started, self.command("item-relative", f"cat {relative}")]))
        self.assertEqual(self.rows(), [])

        # Even an existing workspace skill must not override an explicit cwd.
        expected.parent.mkdir(parents=True)
        expected.write_text("Synthetic expected skill\n")
        for field in ("cwd", "workdir"):
            for value in (str(other), "relative-directory", None, 42, ""):
                with self.subTest(field=field, value=value):
                    event = self.command("item-explicit", f"cat {relative}")
                    event["item"][field] = value
                    self.record(self.capture("codex", [started, event]))
                    self.assertEqual(self.rows(), [])
        event = self.command("item-event-cwd", f"cat {relative}")
        event["cwd"] = str(other)
        self.record(self.capture("codex", [started, event]))
        self.assertEqual(self.rows(), [])

        read = subprocess.run(["cat", str(expected)], cwd=other, capture_output=True, text=True, check=True)
        self.assertEqual(read.stdout, "Synthetic expected skill\n")
        event = self.command("item-absolute", f"cat {expected}")
        event["item"]["workdir"] = str(other)
        self.record(self.capture("codex", [started, event]))
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT skill_slug,skill_source,custom_skill_id,signal_kind "
                                          "FROM skill_use_events").fetchall(),
                             [("start-task", "custom", "CS-original", "opened_skill_file")])

    def test_codex_named_read_variants(self) -> None:
        relative = ".agents/skills/start-task/SKILL.md"
        expected = self.workspace / relative
        expected.parent.mkdir(parents=True)
        expected.write_text("Synthetic skill\n")
        commands = [
            f"cat {relative}", f"cat -- './{relative}'", f"/usr/bin/zsh -lc 'cat {relative}'",
            f"sed -n '1,20p' {relative}", f"sed -n '1,20p;40,60p' {relative}",
            f"sed -n -e '1,20p' -e '40p' -- {relative}",
            f"head {relative}", f"head -n 20 {relative}", f"head -n20 {relative}",
            f"head -20 {relative}", f"head --lines=20 {relative}",
            f"head -c 20 {relative}", f"tail --bytes=20 {relative}",
            f"tail {relative}", f"tail -n +20 {relative}", f"tail -20 {relative}",
            f"tail -n20 {relative}", f"nl {relative}", f"nl -ba {relative}", f"nl -b a {relative}",
        ]
        started = {"type": "thread.started", "thread_id": "provider-current"}
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                event = self.command(f"item-read-{index}", command)
                if index % 2:
                    event["item"]["workdir"] = str(self.workspace)
                capture = self.capture("codex", [started, event])
                self.record(capture)
                self.record(capture)
        with closing(sqlite3.connect(self.db.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT skill_slug,signal_kind,source_ref FROM skill_use_events ORDER BY id").fetchall(),
                             [("start-task", "opened_skill_file", f"codex:item-read-{i}") for i in range(len(commands))])

    def test_codex_multi_slug_command_identity_and_dedup(self) -> None:
        for root, slug in ((".agents", "start-task"), (".claude", "start-task"), (".agents", "make-worktree")):
            skill = self.workspace / root / "skills" / slug / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Synthetic skill\n")
        capture = self.capture("codex", [
            {"type": "thread.started", "thread_id": "provider-current"},
            self.command("item-multiple", "cat .agents/skills/start-task/SKILL.md "
                         ".agents/skills/make-worktree/SKILL.md .claude/skills/start-task/SKILL.md "
                         ".agents/skills/start-task/SKILL.md"),
        ])
        self.assertEqual([(s.slug, s.source_ref) for s in usage.parse_skill_use(capture, "provider-current")],
                         [("start-task", "codex:item-multiple"), ("make-worktree", "codex:item-multiple")])
        self.record(capture)
        self.record(capture)
        with closing(sqlite3.connect(self.db.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT skill_slug,signal_kind,source_ref FROM skill_use_events ORDER BY id").fetchall(),
                             [(s, "opened_skill_file", "codex:item-multiple") for s in ("start-task", "make-worktree")])

    def test_codex_search_listing_and_shell_mentions_do_not_count(self) -> None:
        path = str(self.workspace / ".agents/skills/start-task/SKILL.md")
        commands = [f"rg -n name {path}", f"grep name {path}", f"ls {path}",
                    f"find {self.workspace} -name SKILL.md", f"echo {path}",
                    f"cat {path} | head -20", f"false; cat {path}", f"true && cat {path}",
                    f"cat # {path}", f"cat $(echo {path})", f"cat {path} > out",
                    f"sed -n '1e cat {path}' {path}", f"sed -n '1,20w {path}' {path}",
                    f"head -n 0 {path}", f"tail -f {path}", "cat r5/start-task/SKILL.md",
                    "cat .agents/skills/*/SKILL.md", "cat .agents/skills/../start-task/SKILL.md",
                    "cat .agents/skills/missing/SKILL.md"]
        started = {"type": "thread.started", "thread_id": "provider-current"}
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                self.record(self.capture("codex", [started, self.command(f"item-negative-{index}", command)]))
                with closing(sqlite3.connect(self.db.db_path)) as conn:
                    self.assertEqual(conn.execute("SELECT skill_slug FROM skill_use_events WHERE source_ref=?",
                                                  (f"codex:item-negative-{index}",)).fetchall(), [])

    def test_bad_sources_discard_entire_batch(self) -> None:
        valid = [self.user(self.prompt), self.skill("start-task", "tool-current")]
        for case in ("missing", "malformed", "oversized", "long-line", "many-lines", "symlink",
                     "parent-symlink", "hardlink", "fifo", "provider-mismatch", "ambiguous-prompt", "signal-bound", "deadline"):
            with self.subTest(case=case):
                self.write(valid)
                original_parent = None
                if case == "missing":
                    self.transcript.unlink()
                elif case == "malformed":
                    with self.transcript.open("a") as out:
                        out.write("{malformed\n")
                elif case == "oversized":
                    with self.transcript.open("ab") as out:
                        out.truncate(usage.MAX_BYTES + 1)
                elif case == "long-line":
                    with self.transcript.open("a") as out:
                        out.write(json.dumps({"x": "x" * usage.MAX_LINE_BYTES}) + "\n")
                elif case == "many-lines":
                    with self.transcript.open("a") as out:
                        out.write("{}\n" * usage.MAX_LINES)
                elif case in {"symlink", "hardlink"}:
                    target = self.root / f"{case}.jsonl"
                    self.transcript.rename(target)
                    if case == "symlink":
                        self.transcript.symlink_to(target)
                    else:
                        os.link(target, self.transcript)
                elif case == "parent-symlink":
                    original_parent = self.transcript.parent.with_name("saved-project")
                    self.transcript.parent.rename(original_parent)
                    self.transcript.parent.symlink_to(original_parent, target_is_directory=True)
                elif case == "fifo":
                    self.transcript.unlink()
                    os.mkfifo(self.transcript)
                elif case == "provider-mismatch":
                    self.write(valid + [{**self.skill("wrong", "tool-wrong"), "sessionId": "other-provider"}])
                elif case == "ambiguous-prompt":
                    self.write(valid + [self.user(self.prompt)])
                elif case == "signal-bound":
                    self.write(valid + [self.skill("start-task", f"tool-{i}") for i in range(usage.MAX_SIGNALS)])
                try:
                    if case == "deadline":
                        with patch.object(usage, "MAX_SECONDS", 0), self.assertRaises(ValueError):
                            self.record(self.capture())
                    elif case == "long-line":
                        # Even a valid prefix is discarded: an oversized record
                        # could conceal a user boundary or provider mismatch.
                        with self.assertRaisesRegex(ValueError, "skill use line byte bound"):
                            self.record(self.capture())
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            self.record(self.capture())
                    self.assertEqual(self.rows(), [])
                finally:
                    if original_parent is not None:
                        self.transcript.parent.unlink()
                        original_parent.rename(self.transcript.parent)
                    if self.transcript.exists() or self.transcript.is_symlink():
                        self.transcript.unlink()
        for data in ("{broken", "x" * (usage.MAX_BYTES + 1)):
            with self.subTest(codex=len(data)), self.assertRaises(ValueError):
                self.record(usage.SkillUseCapture("codex", self.workspace, self.prompt, data))
        self.assertEqual(self.rows(), [])

    def test_executor_retains_events_before_tail_and_preserves_accounting(self) -> None:
        from runtime.orchestrator.executors import _run_command, _parse_codex_session_id, _parse_codex_usage

        events = [{"type": "thread.started", "thread_id": "provider-current"},
                  self.command("item-read", f"head -n 20 {self.workspace}/.agents/skills/start-task/SKILL.md"),
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "x" * 3000}},
                  {"type": "turn.completed", "usage": {"input_tokens": 30, "cached_input_tokens": 5,
                                                       "output_tokens": 7}}]
        stdout = "".join(json.dumps(e) + "\n" for e in events)
        process = SimpleNamespace(returncode=0, communicate=lambda **kwargs: (stdout, ""))
        result = _run_command(
            ["synthetic-provider"], self.workspace, "sess-current", 5, input_text=self.prompt,
            usage_parser=_parse_codex_usage, session_id_parser=_parse_codex_session_id,
            provider="codex", running=SimpleNamespace(process=process),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.agent_session_id, "provider-current")
        self.assertEqual(result.token_usage.input_tokens, 25)
        self.assertEqual(result.token_usage.output_tokens, 7)
        self.assertNotIn("SKILL.md", result.stdout_tail)
        self.assertNotIn(self.prompt, repr(result))
        self.assertIsNotNone(result.skill_use_capture)
        self.record(result.skill_use_capture)
        self.assertEqual(self.rows(), [("start-task", "opened_skill_file", "sess-current")])

    def test_observer_failure_does_not_change_result_or_wait(self) -> None:
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        result = ExecutorResult(True, 4, "sess-current", agent_session_id="provider-current",
                                skill_use_capture=self.capture())
        before = asdict(result)

        def blocked_failure(*args: object, **kwargs: object) -> None:
            entered.set()
            release.wait(2)
            finished.set()
            raise ValueError("synthetic-private-transcript")

        with patch.object(usage, "_record_skill_use", side_effect=blocked_failure), self.assertLogs(usage.logger) as logs:
            usage.observe_skill_use(self.db, result, org_slug="synthetic-org", agent_name="agent-permanent",
                                    task_id=None, session_context="thread", executor="claude")
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(finished.is_set())  # Caller returned while observer remains blocked.
                self.assertEqual(asdict(result), before)
            finally:
                release.set()
                # Synchronize only the test: production never joins observers.
                for thread in threading.enumerate():
                    if thread.name == "skill-use-observer":
                        thread.join(1)
        self.assertIn("ValueError", " ".join(logs.output))
        self.assertNotIn("synthetic-private-transcript", " ".join(logs.output))
        self.assertEqual(self.rows(), [])

    def test_legacy_upgrade_preserves_rows_and_converges(self) -> None:
        self.db.close()
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            conn.execute("DROP TABLE skill_use_events")
            conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,policy_class,origin_kind,created_at,created_by) "
                         "VALUES ('CS-legacy','synthetic-org','legacy','Legacy','standard_operational','human','old','founder')")
            conn.execute("INSERT INTO custom_skill_events (skill_id,event_type,actor,created_at) "
                         "VALUES ('CS-legacy','created','founder','old')")
            conn.execute("INSERT INTO custom_skill_versions (id,skill_id,content_hash,content_artifact_key,"
                         "created_at,author_kind,author_identity) VALUES "
                         "(11,'CS-legacy','synthetic-hash','synthetic-key','old','human','founder')")
            conn.execute("INSERT INTO custom_skill_materializations (skill_id,agent_name,task_id,session_context,"
                         "session_id,version_id,content_hash,success,created_at) VALUES "
                         "('CS-legacy','agent-permanent','TASK-legacy','task','sess-legacy',11,'synthetic-hash',1,'old')")
            conn.execute("CREATE TABLE unrelated_legacy (skill_slug TEXT, payload BLOB)")
            conn.execute("INSERT INTO unrelated_legacy VALUES ('sentinel',x'0001FF')")
            before = conn.execute("SELECT name,sql FROM sqlite_schema WHERE name IN "
                                  "('custom_skill_events','custom_skill_materializations') ORDER BY name").fetchall()
        shapes = []
        for _ in range(2):
            reopened = Database(self.db.db_path)
            reopened.close()
            with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
                shapes.append(conn.execute("SELECT type,name,sql FROM sqlite_schema WHERE tbl_name='skill_use_events' "
                                           "ORDER BY type,name").fetchall())
                self.assertEqual(conn.execute("SELECT name,sql FROM sqlite_schema WHERE name IN "
                                              "('custom_skill_events','custom_skill_materializations') ORDER BY name").fetchall(), before)
                self.assertEqual(conn.execute("SELECT skill_id,event_type FROM custom_skill_events").fetchall(),
                                 [("CS-legacy", "created")])
                self.assertEqual(conn.execute("SELECT skill_id,session_id,success FROM custom_skill_materializations").fetchall(),
                                 [("CS-legacy", "sess-legacy", 1)])
                self.assertEqual(conn.execute("SELECT * FROM unrelated_legacy").fetchall(), [("sentinel", b"\0\1\xff")])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM skill_use_events").fetchone(), (0,))
                insert = ("INSERT INTO skill_use_events (org_slug,skill_slug,skill_source,agent_name,"
                          "session_context,session_id,executor,signal_kind,observed_at,source_ref) "
                          "VALUES ('synthetic-org','legacy','unknown','agent-permanent','thread',"
                          "'sess-legacy','claude','invoked','now','ref-legacy')")
                conn.execute(insert)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(insert)
                conn.rollback()
        self.assertEqual(shapes[0], shapes[1])
        fresh = Database(self.root / "fresh.db")
        fresh.close()
        with closing(sqlite3.connect(self.root / "fresh.db")) as conn, conn:
            self.assertEqual(shapes[0], conn.execute("SELECT type,name,sql FROM sqlite_schema WHERE tbl_name='skill_use_events' "
                                                   "ORDER BY type,name").fetchall())


if __name__ == "__main__":
    unittest.main()
