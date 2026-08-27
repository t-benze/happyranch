"""Unit tests for labs.tenant_isolation.harness.cellspec — headscale v0.25
cell config generation (pinned upstream schema facts).

Merge unit B (THR-097, TASK-5792). Each cell gets an independent config with
map-form ``prefixes.v4/v6``, MagicDNS disabled with a lab base_domain, a
file-mode policy path, an sqlite database, a per-cell Noise key, and an
optional shared DERP region. The schema facts below are verified against the
pinned headscale v0.25.1 ``config-example.yaml`` (upstream, read-only).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from labs.tenant_isolation.harness.cellspec import (
    headscale_config_text,
    validate_config_schema,
)
from labs.tenant_isolation.harness.models import build_lab_spec


def _cell_a() -> object:
    spec = build_lab_spec("run-cfg-1", Path("/tmp/lab"), 38000, 990)
    return spec.cell("a")


def test_config_has_v025_map_form_prefixes() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    assert "prefixes:" in text
    assert "  v4: 100.64.0.0/24" in text
    assert "  v6: fd7a:115c:a1e0::/48" in text
    assert "  allocation: sequential" in text
    validate_config_schema(text)


def test_config_disables_magic_dns_with_lab_base_domain() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    assert "magic_dns: false" in text
    assert "base_domain: lab.invalid" in text
    # Deprecated override_local_dns must never be emitted.
    assert "override_local_dns" not in text


def test_config_binds_loopback_only() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    assert f"server_url: http://127.0.0.1:{cell.control_port}" in text
    assert f"listen_addr: 127.0.0.1:{cell.control_port}" in text
    assert "0.0.0.0" not in text


def test_config_pins_policy_path_and_database_per_cell() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    assert "mode: file" in text
    # Identity paths must be container-mapped (the state dir is bind-mounted
    # at /var/lib/headscale inside the cell container).
    assert "path: /var/lib/headscale/policy.json" in text
    assert "type: sqlite3" in text
    assert "path: /var/lib/headscale/db.sqlite" in text
    assert "private_key_path: /var/lib/headscale/noise_private.key" in text
    # Host paths must never leak into the container config.
    assert str(cell.state_dir) not in text


def test_container_path_mapping() -> None:
    from labs.tenant_isolation.harness.cellspec import container_path

    cell = _cell_a()
    assert container_path(cell.key_path, cell.state_dir) == "/var/lib/headscale/noise_private.key"
    assert container_path(cell.db_path, cell.state_dir) == "/var/lib/headscale/db.sqlite"
    assert container_path(cell.policy_path, cell.state_dir) == "/var/lib/headscale/policy.json"


def test_config_cells_differ_only_in_identity_fields() -> None:
    spec = build_lab_spec("run-cfg-2", Path("/tmp/lab"), 38000, 990)
    ta = headscale_config_text(spec.cell("a"), spec.cell("a").policy_path)
    tb = headscale_config_text(spec.cell("b"), spec.cell("b").policy_path)
    assert ta != tb
    assert f":{spec.cell('a').control_port}" in ta
    assert f":{spec.cell('b').control_port}" in tb


def test_config_derp_server_shared_region() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path, derp_enabled=True)
    assert "derp:" in text
    assert "server:" in text
    assert "enabled: true" in text
    assert f"region_id: {cell.derp_region_id}" in text
    assert 'region_code: "lab"' in text
    # headscale v0.25.1 requires a STUN listener and a DERP private key for
    # the embedded server, and the region must be auto-added so the initial
    # DERPMap is never empty (an empty DERPMap aborts startup).
    assert f'stun_listen_addr: "127.0.0.1:{cell.stun_port}"' in text
    assert "private_key_path: /var/lib/headscale/derp_server_private.key" in text
    assert "automatically_add_embedded_derp_region: true" in text
    validate_config_schema(text)


def test_config_without_derp_map_is_invalid() -> None:
    """Regression/mutation: headscale v0.25.1 refuses to boot with an empty
    DERPMap ("initial DERPMap is empty, Headscale requires at least one
    entry"). A cell config without the embedded DERP block must therefore
    FAIL schema validation (fail closed before launch), so a future edit that
    disables embedded DERP is caught at preflight, not on the lab runner."""
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path, derp_enabled=False)
    assert "derp:" not in text
    with pytest.raises(AssertionError):
        validate_config_schema(text)


def test_config_derp_stun_ports_are_loopback_and_per_cell() -> None:
    spec = build_lab_spec("run-cfg-3", Path("/tmp/lab"), 38000, 990)
    a, b = spec.cells
    assert a.stun_port != b.stun_port
    for cell in (a, b):
        text = headscale_config_text(cell, cell.policy_path)
        assert f'stun_listen_addr: "127.0.0.1:{cell.stun_port}"' in text
        validate_config_schema(text)


def test_validate_config_schema_rejects_deprecated_shapes() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    # Simulate the deprecated/absent keys that must never pass.
    mutated = text.replace("  v4: 100.64.0.0/24", "  - 100.64.0.0/24")
    with pytest.raises(AssertionError):
        validate_config_schema(mutated)
    with pytest.raises(AssertionError):
        validate_config_schema(text.replace("magic_dns: false", "magic_dns: true"))


def test_validate_config_schema_rejects_missing_policy_path() -> None:
    cell = _cell_a()
    text = headscale_config_text(cell, cell.policy_path)
    with pytest.raises(AssertionError):
        validate_config_schema(text.replace("path: ", "path_missing: "))
