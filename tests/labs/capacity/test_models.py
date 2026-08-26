"""Unit tests for lab models: synthetic run-id and resource naming rules.

Every rerun must use unique synthetic IDs and every disposable resource
(containers, networks, volumes, state dirs) must be namespaced by the run
id so deterministic cleanup can never touch anything outside the lab.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models import (
    RUN_ID_RE,
    client_container_name,
    headscale_container_name,
    make_run_id,
    network_name,
    state_dir_name,
    validate_run_id,
    volume_name,
)


def test_make_run_id_shape():
    run_id = make_run_id(datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc), "ab12")
    assert run_id == "cap-20260826T120000Z-ab12"
    assert RUN_ID_RE.match(run_id)


def test_make_run_id_unique_for_rand():
    base = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    a = make_run_id(base, "aa11")
    b = make_run_id(base, "bb22")
    assert a != b


def test_validate_run_id_accepts_canonical():
    validate_run_id("cap-20260826T120000Z-ab12")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "cap-",
        "cap-20260826T120000Z-ab1",       # too-short rand
        "cap-20260826T120000Z-ab123",      # too-long rand
        "CAP-20260826T120000Z-ab12",       # wrong prefix case
        "cap-20260826T120000Z-AB12",       # uppercase rand
        "cap-2026-08-26T120000Z-ab12",     # bad ts separators
        "cap-20260826T120000Z-AB12!",      # illegal chars
        "other-20260826T120000Z-ab12",     # wrong prefix
    ],
)
def test_validate_run_id_rejects(bad):
    with pytest.raises(ValueError):
        validate_run_id(bad)


def test_resource_names_are_namespaced():
    run_id = "cap-20260826T120000Z-ab12"
    assert headscale_container_name(run_id, 1) == "hs-cap-20260826T120000Z-ab12-c1"
    assert headscale_container_name(run_id, 4) == "hs-cap-20260826T120000Z-ab12-c4"
    assert client_container_name(run_id, 1, 5) == "cl-cap-20260826T120000Z-ab12-c1-n5"
    assert network_name(run_id) == "net-cap-20260826T120000Z-ab12"
    assert volume_name(run_id, 2) == "vol-cap-20260826T120000Z-ab12-c2"
    assert state_dir_name(run_id) == "st-cap-20260826T120000Z-ab12"


def test_resource_names_embed_only_valid_chars():
    import re

    run_id = "cap-20260826T120000Z-ab12"
    for name in (
        headscale_container_name(run_id, 1),
        client_container_name(run_id, 1, 5),
        network_name(run_id),
        volume_name(run_id, 2),
        state_dir_name(run_id),
    ):
        # Docker names/networks/volumes: [a-zA-Z0-9][a-zA-Z0-9_.-]*
        assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name), name
