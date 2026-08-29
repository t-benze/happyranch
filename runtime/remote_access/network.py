"""Customer-owned-network address discovery and validation (THR-097 Unit 3A).

The Supported-DIY adapter binds ONLY to an address on the customer's OWN
network — never a wildcard, never a public/wildcard interface, and never a
bare plaintext concrete address. The customer-owned network is the
ENCRYPTED tailscale mode ONLY (TASK-6039 reviewer [CRITICAL] finding 1):

- **tailscale mode (default; the only mode)** — ride-installed system
  Tailscale (THR-034 pattern: the customer runs their own Tailscale/
  headscale and is logged in). The resolver invokes the ``tailscale`` CLI
  (``tailscale ip -4``) and returns the node's tailnet IPv4 (the ``100.x``
  address) — traffic to that address traverses the customer's own
  WireGuard-encrypted tailnet (authenticated, encrypted transport). No
  embedded tsnet, no new dependency, no Network Extension.

The former ``explicit`` concrete-address mode is REMOVED and fails closed:
it bound the plaintext connector listener to an arbitrary LAN/public
interface with no authenticated encrypted transport, breaching the fixed
no-plaintext-service-path invariant. A leftover ``address`` or
``mode: explicit`` in an operator config is refused with a clear
migration message — the customer-owned network bind address is resolved
exclusively from the encrypted tailscale mode.

Resolution failure (CLI missing, not logged in, no IPv4, malformed output,
ambiguous results) fails closed — no listener. The resolved address is
never logged beyond its role as a bind target; it is not a secret, but it
is also never echoed into diagnostics as if it were the network boundary.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


class NetworkAddressError(Exception):
    """Customer-network address could not be resolved/validated (fail
    closed: no listener)."""


@dataclass(frozen=True)
class NetworkConfig:
    """Customer-owned-network configuration (secret-free).

    ``mode`` is ALWAYS ``tailscale`` (ride-installed CLI; the encrypted
    customer-owned transport — the ONLY supported mode). ``tailscale_cli``
    overrides the CLI path (default: ``tailscale`` on PATH). The legacy
    ``address`` field is retained ONLY so an old config carrying an
    explicit-mode artifact fails closed with a clear message instead of a
    load-time crash; any value set fails validation.
    """

    mode: str = "tailscale"
    tailscale_cli: str = "tailscale"
    address: str | None = None

    def validate(self) -> None:
        if self.mode != "tailscale":
            raise NetworkAddressError(
                "network mode must be 'tailscale' — the customer-owned "
                "network is the encrypted tailnet transport only; the "
                "explicit plaintext concrete-address mode was removed "
                "(fail closed, no plaintext service path)"
            )
        if self.address is not None:
            raise NetworkAddressError(
                "explicit network address is not supported: the "
                "customer-owned network bind address is resolved "
                "exclusively from the encrypted tailscale mode "
                "(tailscale ip -4)"
            )
        if self.tailscale_cli and not self.tailscale_cli.strip():
            raise NetworkAddressError("tailscale_cli must be non-empty")


class CustomerNetworkResolver(Protocol):
    def resolve(self) -> str: ...


class TailscaleCliResolver:
    """Ride-installed Tailscale: resolve the local node's tailnet IPv4 via
    the system ``tailscale ip -4`` command (the customer's OWN Tailscale/
    headscale — no embedded tsnet, no new dependency).

    Fail-closed: CLI missing, non-zero exit, empty output, multiple
    addresses, or an address that fails validation => ``NetworkAddressError``
    (never a listener on a guessed address).
    """

    def __init__(self, cli: str = "tailscale", *, runner: Callable[[list[str]], str] | None = None) -> None:
        self._cli = cli
        # ``runner`` is a test seam; production (None) uses subprocess. When a
        # runner is injected the CLI-existence probe is bypassed (the seam
        # owns command execution).
        self._runner = runner or self._default_run

    @staticmethod
    def _default_run(argv: list[str]) -> str:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        ).stdout.strip()

    def resolve(self) -> str:
        if not self._cli:
            raise NetworkAddressError("tailscale CLI not configured")
        if self._runner is None and shutil.which(self._cli) is None:
            raise NetworkAddressError(
                "tailscale CLI not found — the customer-owned network "
                "requires the customer's own Tailscale/headscale client "
                "installed and logged in"
            )
        try:
            output = self._runner([self._cli, "ip", "-4"])
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkAddressError("tailscale ip failed") from exc
        addresses = [line.strip() for line in output.splitlines() if line.strip()]
        if len(addresses) != 1:
            raise NetworkAddressError(
                "tailscale did not resolve exactly one IPv4 address (is the "
                "customer network logged in?)"
            )
        address = addresses[0]
        validate_customer_network_address(address)
        return address


def resolve_customer_network_address(config: NetworkConfig) -> str:
    """Resolve the bind address for a customer-owned network config. The
    encrypted tailscale mode is the ONLY mode; any failure raises
    ``NetworkAddressError`` (fail closed, no listener)."""
    config.validate()
    return TailscaleCliResolver(config.tailscale_cli).resolve()


def validate_customer_network_address(address: str) -> None:
    """Strict validation of a customer-network bind address. Refuses
    wildcards, loopback, multicast, broadcast, and non-IPv4 strings —
    a connector must listen only on a concrete address on the customer's
    own network (the tailnet address resolved by tailscale mode)."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise NetworkAddressError("invalid network address") from exc
    if parsed.version != 4:
        raise NetworkAddressError("customer network bind address must be IPv4")
    if parsed.is_unspecified:  # 0.0.0.0
        raise NetworkAddressError("wildcard bind address forbidden")
    if parsed.is_loopback:  # 127.0.0.0/8
        raise NetworkAddressError("loopback bind address forbidden for the customer network")
    if parsed.is_multicast:  # 224.0.0.0/4
        raise NetworkAddressError("multicast bind address forbidden")
    if parsed.is_link_local:  # 169.254.0.0/16 (APIPA)
        raise NetworkAddressError("link-local bind address forbidden")
    if parsed.is_reserved or str(parsed) == "255.255.255.255":
        raise NetworkAddressError("reserved/broadcast bind address forbidden")
