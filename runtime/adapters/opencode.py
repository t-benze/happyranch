"""First-party adapter for OpenCode (THR-107 Phase 1 / D2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates opencode-specific argv
construction behind :meth:`build_argv`.
"""

from __future__ import annotations


class OpencodeAdapter:
    """First-party adapter for opencode CLI's ``run`` subcommand.

    Encapsulates argv construction: binary, run subcommand, optional model
    flag, --dir workspace, --format json, and positional prompt. Every
    element and order is pinned to the Phase-0 cmd-baseline contract.
    """

    def build_argv(
        self,
        cli_path: str,
        workspace: str,
        prompt: str,
        model: str | None = None,
        model_arg: list[str] | None = None,
    ) -> list[str]:
        """Build the argv list for an opencode subprocess launch.

        Args:
            cli_path: Resolved absolute path to the ``opencode`` binary.
            workspace: Absolute workspace path as a string (``str(workspace)``).
            prompt: Full prompt text including session-lifetime preamble.
            model: Agent model id to inject, or None for CLI default.
            model_arg: Model arg template ``[flag, placeholder]`` or None.
        """
        cmd: list[str] = [cli_path, "run"]
        if model and model_arg:
            for elem in model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "--dir",
            workspace,
            "--format",
            "json",
            prompt,
        ]
        return cmd
