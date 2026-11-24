"""Retained literal historical renderer; no live publication or activation owner.

Run: uv run --frozen pytest scripts/test_home_live_config.py -q
"""
from __future__ import annotations

import json
import pytest
from runtime.remote_access import legacy_home_readback as h
from runtime.remote_access.legacy_home_receipt import CONFIG_MEMBERS, ActivationRefused
from scripts.home_legacy_fixture import legacy_admission_fixture

@pytest.fixture
def venue(tmp_path, monkeypatch):
    return legacy_admission_fixture(tmp_path, monkeypatch, ready=False)


def configs(v: object) -> dict[str, bytes]:
    return {path: v.path(path).read_bytes() for path in CONFIG_MEMBERS}


def expected_live(v: object, prepared: dict[str, bytes]) -> dict[str, bytes]:
    r = v.prepared
    # Independent literal wire expectation, not a production renderer/helper.
    addition = (',"ControlURL":"https://control.happyranch.invalid:8443",'
        '"control_tls":{"dial_ip":"' + r.dial_ip + '","ca_sha256":"' + r.ca_der_sha256 + '"},'
        '"home_binding":{"home_id":"home","control_plane_id":"control",'
        '"connector_id":"connector","activation_generation":7}').encode()
    sidecar = prepared[CONFIG_MEMBERS[1]]
    end = sidecar.rindex(b"}")
    return {CONFIG_MEMBERS[0]: prepared[CONFIG_MEMBERS[0]],
            CONFIG_MEMBERS[1]: sidecar[:end] + addition + sidecar[end:]}


def test_renderer_exact_bytes(venue: object) -> None:
    v = venue
    before = configs(v)
    rendered = h.render_home_live_configs(*before.values(), v.prepared)
    assert rendered == expected_live(v, before)
    assert h.render_home_live_configs(*before.values(), v.prepared) == rendered
    assert configs(v) == before
    raw = json.loads(rendered[CONFIG_MEMBERS[1]])
    assert set(raw) - set(json.loads(before[CONFIG_MEMBERS[1]])) == {"ControlURL", "control_tls", "home_binding"}
    assert raw["home_binding"] == dict(home_id="home", control_plane_id="control", connector_id="connector",
                                       activation_generation=7)
    assert type(raw["home_binding"]["activation_generation"]) is int


@pytest.mark.parametrize("case", ["extra", "url", "tls", "binding", "duplicate", "connector-extra"])
def test_renderer_extra_fields_refuse(venue: object, case: str) -> None:
    v = venue
    connector, sidecar = configs(v).values()
    if case == "connector-extra":
        raw = json.loads(connector)
        raw["unparsed"] = "foreign"
        connector = json.dumps(raw).encode()
    elif case == "duplicate":
        sidecar = b'{"Profile":"linux-managed-identity-v1",' + sidecar[1:]
    else:
        raw = json.loads(sidecar)
        raw[{"extra": "unparsed", "url": "ControlURL", "tls": "control_tls", "binding": "home_binding"}[case]] = None
        sidecar = json.dumps(raw).encode()
    with pytest.raises(ActivationRefused):
        h.render_home_live_configs(connector, sidecar, v.prepared)
