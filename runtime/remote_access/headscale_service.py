"""Historical Headscale policy/constants and closed retired startup command."""
from __future__ import annotations

import json
import sys


ACCOUNT = "happyranch-headscale"
UNIT = "happyranch-headscale.service"
NODE_USER = "happyranch-node"
INVITE_USER = "happyranch-invite"
POLICY_PATH = "/etc/happyranch/headscale/policy.hujson"
RECEIPT = "headscale-account.json"
CUSTODY = ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale")
INSTALLER_STATE = "var/lib/happyranch-remote-access"
PUBLICATION = "headscale-publication.json"
CONFIG_FILES = ("config.yaml", "derp.yaml")
LEAF_FILES = ("server.crt", "server.key")
CONTROL_CA = "etc/happyranch/control-ca.pem"
EMPTY_CA = "etc/happyranch/empty-ca-directory"
HOME_CA = INSTALLER_STATE + "/home-ca"
MATERIALIZER = "opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca"


class HeadscaleServiceError(RuntimeError):
    """Secret-free refusal; no native exception text is retained."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)






def render_policy() -> bytes:
    """v0.25.1 hscontrol/policy/acls_types.go: accept/proto/src/dst, no defaults.

    Only the dedicated invite user's devices may initiate TCP to Linux-node
    devices on connector port 443. All other traffic has no accepting rule.
    Database user creation and minter injection belong to activation.
    """
    return (json.dumps({"acls": [{"action": "accept", "proto": "tcp",
                                "src": [INVITE_USER], "dst": [NODE_USER + ":443"]}]},
                       sort_keys=True, indent=2) + "\n").encode()






def main() -> int:
    if sys.argv[1:] != ["wait-ready"]:
        return 2
    from runtime.remote_access.home_retirement import MESSAGE
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
