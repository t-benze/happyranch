"""HappyRanch — unified CLI for the multi-agent tourism organization.

Thin entry point: {main} + {build_parser}. Every `happyranch` subcommand lives in a
per-family module under `cli.commands`; `build_parser` wires them by calling each
module's `register(sub)`. Handler functions and shared helpers are re-exported
below so existing `from cli.main import <symbol>` call sites keep resolving.
"""
from __future__ import annotations

import argparse
import sys

from cli._shared import (  # noqa: F401  (re-export for back-compat / patch targets)
    _fetch_available_orgs,
    _fmt_ts,
    _ok,
    resolve_org_slug,
)
from cli.client.client import (  # noqa: F401  (cli.main.OpcClient must stay the one shared class)
    DaemonNotRunning,
    DaemonStateInconsistent,
    OpcClient,
)
from cli.commands import (
    agents,
    artifacts,
    assistant,
    dreams,
    jobs,
    kb,
    learning,
    pr_ci,
    runtime,
    tasks,
    threads,
    work_hours,
)
from cli.commands.dreams import (  # noqa: F401
    _complete_payload_from_file as _dream_complete_payload_from_file,
    cmd_dreams_complete,
    cmd_dreams_list,
    cmd_dreams_show,
    cmd_dreams_status,
)
from cli.commands.work_hours import (  # noqa: F401
    cmd_work_hours_list,
    cmd_work_hours_show,
    cmd_work_hours_spawn,
    cmd_work_hours_status,
)
from cli.commands.runtime import (  # noqa: F401  (re-export for back-compat)
    cmd_init,
    cmd_orgs,
    cmd_orgs_init,
    cmd_orgs_unload,
    cmd_runtime,
    cmd_use,
    cmd_web,
)
from cli.commands.assistant import (  # noqa: F401  (re-export for back-compat)
    cmd_assistant_attach,
    cmd_assistant_init,
    cmd_assistant_register,
    cmd_assistant_status,
)
from cli.commands.tasks import (  # noqa: F401  (re-export for back-compat)
    _completion_payload_from_file,
    _stream_task_events,
    cmd_audit,
    cmd_cancel,
    cmd_details,
    cmd_progress,
    cmd_recall,
    cmd_report_completion,
    cmd_resolve_escalation,
    cmd_revisit,
    cmd_run,
    cmd_tail,
    cmd_tasks,
    cmd_tokens,
)
from cli.commands.agents import (  # noqa: F401  (re-export for back-compat)
    _manage_agent_payload_from_file,
    _manage_repo_payload_from_file,
    cmd_approve_agent,
    cmd_enrollments,
    cmd_init_agent,
    cmd_manage_agent,
    cmd_manage_repo,
    cmd_reject_agent,
)
from cli.commands.jobs import (  # noqa: F401  (re-export for back-compat)
    _jobs_submit_payload_from_file,
    _register_jobs_verbs,
    cmd_jobs_list,
    cmd_jobs_output,
    cmd_jobs_reject,
    cmd_jobs_run,
    cmd_jobs_show,
    cmd_jobs_stop,
    cmd_jobs_submit,
    cmd_jobs_tail,
    cmd_jobs_wait,
)
from cli.commands.learning import (  # noqa: F401  (re-export for back-compat)
    _learning_client,
    _read_yaml_payload,
    cmd_learning,
    cmd_learning_add,
    cmd_learning_get,
    cmd_learning_list,
    cmd_learning_promote,
    cmd_learning_reindex,
    cmd_learning_search,
    cmd_learning_update,
)
from cli.commands.kb import (  # noqa: F401  (re-export for back-compat)
    _read_markdown_payload,
    cmd_kb_add,
    cmd_kb_delete,
    cmd_kb_get,
    cmd_kb_list,
    cmd_kb_reindex,
    cmd_kb_search,
    cmd_kb_update,
)
from cli.commands.threads import (  # noqa: F401  (re-export for back-compat)
    cmd_threads_archive,
    cmd_threads_compose,
    cmd_threads_decline,
    cmd_threads_dispatch,
    cmd_threads_extend,
    cmd_threads_forward,
    cmd_threads_invite,
    cmd_threads_list,
    cmd_threads_reply,
    cmd_threads_resume,
    cmd_threads_send,
    cmd_threads_show,
    cmd_threads_tui,
)
from cli.commands.artifacts import (  # noqa: F401  (re-export for back-compat)
    cmd_artifacts_get,
    cmd_artifacts_list,
    cmd_artifacts_put,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="happyranch",
        description="HappyRanch — multi-agent tourism organization CLI",
    )
    sub = parser.add_subparsers(dest="command")

    runtime.register(sub)
    tasks.register(sub)
    agents.register(sub)
    jobs.register(sub)
    learning.register(sub)
    kb.register(sub)
    artifacts.register(sub)
    assistant.register(sub)
    dreams.register(sub)
    work_hours.register(sub)
    threads.register(sub)
    pr_ci.register(sub)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
