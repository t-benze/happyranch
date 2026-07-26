"""First-party adapter for Codex CLI (THR-107 Phase 1 / D2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates Codex-specific argv construction
behind :meth:`build_argv`.
"""

from __future__ import annotations


class CodexAdapter:
    """First-party adapter for Codex CLI's ``exec`` subcommand.

    Encapsulates argv construction: binary, exec subcommand, optional model
    flag, sandbox configuration, json flag, and stdin prompt indicator.
    Every element and order is pinned to the Phase-0 cmd-baseline contract.
    """

    def build_argv(
        self,
        cli_path: str,
        sandbox_mode: str,
        model: str | None = None,
        model_arg: list[str] | None = None,
    ) -> list[str]:
        """Build the argv list for a Codex subprocess launch.

        Args:
            cli_path: Resolved absolute path to the ``codex`` binary.
            sandbox_mode: ``--sandbox`` value from Settings.
            model: Agent model id to inject, or None for CLI default.
            model_arg: Model arg template ``[flag, placeholder]`` or None.
        """
        cmd: list[str] = [cli_path, "exec"]
        if model and model_arg:
            for elem in model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "--sandbox",
            sandbox_mode,
            "-c",
            "sandbox_workspace_write.network_access=true",
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        return cmd
