"""First-party adapter for Pi (THR-107 Phase 1 / D2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates Pi-specific argv construction
behind :meth:`build_argv`.
"""

from __future__ import annotations


class PiAdapter:
    """First-party adapter for Pi's ``-p`` print mode.

    Encapsulates argv construction: binary, optional model flag, print
    flag with prompt, and json output mode. Every element and order is
    pinned to the Phase-0 cmd-baseline contract.
    """

    def build_argv(
        self,
        cli_path: str,
        prompt: str,
        model: str | None = None,
        model_arg: list[str] | None = None,
    ) -> list[str]:
        """Build the argv list for a Pi subprocess launch.

        Args:
            cli_path: Resolved absolute path to the ``pi`` binary.
            prompt: Full prompt text including session-lifetime preamble.
            model: Agent model id to inject, or None for CLI default.
            model_arg: Model arg template ``[flag, placeholder]`` or None.
        """
        cmd: list[str] = [cli_path]
        if model and model_arg:
            for elem in model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "-p",
            prompt,
            "--mode",
            "json",
        ]
        return cmd
