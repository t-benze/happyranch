"""Customer-owned-network address resolver/validation tests (THR-097 Unit 3A).

Proves: strict bind-address validation (wildcard/loopback/multicast/
reserved/broadcast refused), explicit-mode resolution, ride-installed
tailscale-mode resolution with fail-closed behavior on missing CLI / bad
output / multiple addresses, and secret-free error prose.
"""
from __future__ import annotations

import pytest

from runtime.remote_access.network import (
    ExplicitAddressResolver,
    NetworkAddressError,
    NetworkConfig,
    TailscaleCliResolver,
    resolve_customer_network_address,
    validate_customer_network_address,
)


class TestValidation:
    @pytest.mark.parametrize(
        "address",
        [
            "0.0.0.0",
            "::",
            "127.0.0.1",
            "127.8.8.8",
            "224.0.0.1",
            "255.255.255.255",
            "169.254.0.1",
            "not-an-ip",
            "300.1.1.1",
            "",
        ],
    )
    def test_refuses_bad_addresses(self, address: str) -> None:
        with pytest.raises(NetworkAddressError):
            validate_customer_network_address(address)

    @pytest.mark.parametrize(
        "address",
        [
            "100.64.0.1",
            "100.101.102.103",
            "192.168.1.50",
            "10.0.0.7",
            "172.16.5.5",
        ],
    )
    def test_accepts_customer_network_addresses(self, address: str) -> None:
        validate_customer_network_address(address)  # no exception


class TestExplicitMode:
    def test_explicit_resolves_validated_address(self) -> None:
        config = NetworkConfig(mode="explicit", address="100.64.0.5")
        assert resolve_customer_network_address(config) == "100.64.0.5"

    def test_explicit_missing_address_fails_closed(self) -> None:
        config = NetworkConfig(mode="explicit", address=None)
        with pytest.raises(NetworkAddressError, match="address"):
            resolve_customer_network_address(config)

    def test_explicit_loopback_refused(self) -> None:
        config = NetworkConfig(mode="explicit", address="127.0.0.1")
        with pytest.raises(NetworkAddressError, match="loopback"):
            resolve_customer_network_address(config)


class TestTailscaleMode:
    def test_resolves_single_tailnet_ip(self) -> None:
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "100.64.0.9\n")
        assert resolver.resolve() == "100.64.0.9"

    def test_missing_cli_fails_closed(self, monkeypatch) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)
        resolver = TailscaleCliResolver("tailscale")
        with pytest.raises(NetworkAddressError, match="tailscale"):
            resolver.resolve()

    def test_multiple_addresses_fail_closed(self) -> None:
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "100.64.0.9\n100.64.0.10\n")
        with pytest.raises(NetworkAddressError, match="exactly one"):
            resolver.resolve()

    def test_empty_output_fails_closed(self) -> None:
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "")
        with pytest.raises(NetworkAddressError):
            resolver.resolve()

    def test_unexpected_output_fails_closed(self) -> None:
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "bogus\n")
        with pytest.raises(NetworkAddressError):
            resolver.resolve()

    def test_loopback_from_cli_fails_closed(self) -> None:
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "127.0.0.1\n")
        with pytest.raises(NetworkAddressError):
            resolver.resolve()

    def test_config_validation(self) -> None:
        with pytest.raises(NetworkAddressError, match="mode"):
            NetworkConfig(mode="wireguard").validate()
        NetworkConfig(mode="tailscale", tailscale_cli="tailscale").validate()
        with pytest.raises(NetworkAddressError, match="tailscale_cli"):
            NetworkConfig(mode="tailscale", tailscale_cli=" ").validate()


class TestNoSecrets:
    def test_errors_never_contain_address_payload(self) -> None:
        # The resolver error prose is category-level; it never embeds raw
        # command output beyond the stable message.
        resolver = TailscaleCliResolver("tailscale", runner=lambda argv: "100.64.0.9\n")
        assert resolver.resolve() == "100.64.0.9"
        with pytest.raises(NetworkAddressError) as exc_info:
            TailscaleCliResolver("tailscale", runner=lambda argv: "secret-token\n").resolve()
        assert "secret-token" not in str(exc_info.value)
