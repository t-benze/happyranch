"""First-party adapter for Pi (THR-107 Phase 1 / D2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates Pi-specific argv construction
behind :meth:`build_argv`.
"""

from __future__ import annotations


class PiAdapter:
    """First-party adapter for Pi's ``-p`` print mode.

    Encapsulates argv construction: binary, optional model flag, print
    flag, and json output mode. Every element and order is pinned to the
    Phase-0 cmd-baseline contract except the prompt transport: THR-200
    moved the prompt body OFF argv onto stdin (verified on pi 0.84.2:
    ``-p`` with no message argument reads the sole user prompt from stdin
    as a ``role: user`` / ``type: text`` message). The caller delivers
    the prompt via ``input_text`` on the subprocess.
    """

    def build_argv(
        self,
        cli_path: str,
        prompt: str,
        model: str | None = None,
        model_arg: list[str] | None = None,
        resume_session_id: str | None = None,
    ) -> list[str]:
        """Build the argv list for a Pi subprocess launch.

        Args:
            cli_path: Resolved absolute path to the ``pi`` binary.
            prompt: Full prompt text including session-lifetime preamble.
                Delivered via stdin (``input_text``), never argv (THR-200).
            model: Agent model id to inject, or None for CLI default.
            model_arg: Model arg template ``[flag, placeholder]`` or None.
            resume_session_id: Session UUID to continue via ``--session <id>``
                (verified live on pi 0.84.2: the same ``session.id`` header is
                re-emitted after continuation). ``--session`` FAILS when the
                id is missing — the exact eviction signature the thread runner
                needs. ``--session-id`` would silently create a fresh session
                (message omission) and is never used on the thread path.
        """
        cmd: list[str] = [cli_path]
        if model and model_arg:
            for elem in model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "-p",
            "--mode",
            "json",
        ]
        if resume_session_id:
            cmd += ["--session", resume_session_id]
        return cmd
