"""Fixed bundled-daemon dispatch, before normal daemon startup."""
from __future__ import annotations


def main(argv: list[str]) -> int:
    """Reuse the source parsers; never accept a module or script to execute."""
    if argv[:1] == ["installer"]:
        from runtime.remote_access.launchd_installer import main as installer_main

        return installer_main(argv[1:])
    from runtime.remote_access.cli import main as connector_main

    return connector_main(argv)
