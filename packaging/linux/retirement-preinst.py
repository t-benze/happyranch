"""Generate incoming control code from the retained canonical retirement sources.

This trusted build helper reads source bytes, never imports supplied payload code.
The closed inline loader cannot borrow an older installed runtime module.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import zlib

# Explicit dependency closure, including callable-local config validators.
# Provisioning/activation/issuer owners deliberately have no bundle entry.
MODULES = """
allowlist audit authorization credentials diy_provider forwarding gateway
helper_control helper_unit home_retirement home_retirement_account
home_retirement_cleanup home_retirement_coordinator home_retirement_cutoff
home_retirement_fence home_retirement_inert home_retirement_inventory
home_retirement_journal home_retirement_operations home_retirement_publication
home_retirement_recovery home_retirement_service home_retirement_transport
home_retirement_transport_journal home_retirement_trust home_retirement_upgrade
httpd identity installer_io installer_lock installer_validation invite_control
invite_v2 lab_provider launchd_agent launchd_files launchd_readiness
legacy_home_receipt legacy_issuer_readback managed_provider models network
normalization pairing policy readiness release_verifier retirement_io revocation
service_manager state state_store streams stripping supervisor systemd_unit
""".split()


def render_preinst(repository: Path) -> str:
    sources = {"runtime.remote_access." + name:
               (repository / "runtime/remote_access" / (name + ".py")).read_text()
               for name in MODULES}
    packed = base64.b64encode(zlib.compress(json.dumps(sources, sort_keys=True).encode(), 9)).decode()
    template = Path(__file__).with_name("retirement-preinst.in.py").read_text()
    return template.replace("'@RETIREMENT_SOURCES@'", repr(packed))
