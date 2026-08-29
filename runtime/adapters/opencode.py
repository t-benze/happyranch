"""First-party adapter for OpenCode (THR-107 Phase 1 / D2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates opencode-specific argv
construction behind :meth:`build_argv`.
"""

from __future__ import annotations


class OpencodeAdapter:
    """First-party adapter for opencode CLI's ``run`` subcommand.

    Encapsulates argv construction: binary, run subcommand, optional model
    flag, optional resume ``-s <id>``, ``--dir`` workspace, and ``--format
    json``. The prompt body is NOT an argv element — the caller delivers it
    via stdin (``input_text``), the TASK-6080/THR-200 large-prompt-safe
    transport proven live on opencode 1.18.25 (a 606,099-byte prompt
    through a pipe; the argv form fails at the kernel single-argument
    limit). ``opencode run`` with no positional message reads the sole user
    prompt from stdin. Every element and order is pinned to the Phase-0
    cmd-baseline contract minus the positional prompt.
    """

    def build_argv(
        self,
        cli_path: str,
        workspace: str,
        prompt: str,
        model: str | None = None,
        model_arg: list[str] | None = None,
        resume_session_id: str | None = None,
    ) -> list[str]:
        """Build the argv list for an opencode subprocess launch.

        Args:
            cli_path: Resolved absolute path to the ``opencode`` binary.
            workspace: Absolute workspace path as a string (``str(workspace)``).
            prompt: Full prompt text including session-lifetime preamble.
                Delivered via stdin (``input_text``), never argv (THR-200).
            model: Agent model id to inject, or None for CLI default.
            model_arg: Model arg template ``[flag, placeholder]`` or None.
            resume_session_id: Session id to continue via ``-s <id>``
                (verified live on opencode 1.18.25: the SAME ``sessionID``
                is re-emitted after continuation and the prompt is read from
                stdin on the resume form too).
        """
        cmd: list[str] = [cli_path, "run"]
        if model and model_arg:
            for elem in model_arg:
                cmd.append(elem.replace("{model}", model))
        if resume_session_id:
            cmd += ["-s", resume_session_id]
        cmd += [
            "--dir",
            workspace,
            "--format",
            "json",
        ]
        return cmd
