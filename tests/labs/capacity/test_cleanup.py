"""Unit tests for residue detection parsers (deterministic cleanup checks)."""

from __future__ import annotations

from cleanup import (
    parse_docker_network_ls,
    parse_docker_ps_json,
    parse_docker_volume_ls,
    parse_pgrep,
    residue_report,
)

RUN = "cap-20260826T120000Z-ab12"

DOCKER_PS = """\
{"Command":"headscale serve","CreatedAt":"2026-08-26 12:00:05","ID":"abc123","Image":"headscale/headscale:0.25","Labels":"lab.run=cap-20260826T120000Z-ab12","Names":"hs-cap-20260826T120000Z-ab12-c1","Ports":"","State":"running"}
{"Command":"containerboot","CreatedAt":"2026-08-26 12:01:00","ID":"def456","Image":"tailscale/tailscale:v1.80.0","Labels":"lab.run=cap-20260826T120000Z-ab12","Names":"cl-cap-20260826T120000Z-ab12-c1-n1","Ports":"","State":"exited"}
"""


def test_parse_docker_ps_json():
    entries = parse_docker_ps_json(DOCKER_PS)
    assert len(entries) == 2
    assert entries[0]["Names"] == "hs-cap-20260826T120000Z-ab12-c1"
    assert entries[0]["State"] == "running"


def test_parse_docker_ps_json_empty():
    assert parse_docker_ps_json("") == []


def test_parse_network_and_volume_ls():
    nets = parse_docker_network_ls("net-cap-20260826T120000Z-ab12\n")
    assert nets == ["net-cap-20260826T120000Z-ab12"]
    vols = parse_docker_volume_ls("vol-cap-20260826T120000Z-ab12-c1\nvol-cap-20260826T120000Z-ab12-c2\n")
    assert vols == [
        "vol-cap-20260826T120000Z-ab12-c1",
        "vol-cap-20260826T120000Z-ab12-c2",
    ]


def test_parse_pgrep():
    assert parse_pgrep("123\n456\n") == [123, 456]
    assert parse_pgrep("") == []
    assert parse_pgrep("garbage\n789\n") == [789]


def test_residue_report_ok():
    report = residue_report(containers=[], networks=[], volumes=[], pids=[], state_entries=[], run_id=RUN)
    assert report["ok"] is True


def test_residue_report_detects_leftovers():
    report = residue_report(
        containers=["hs-cap-20260826T120000Z-ab12-c1"],
        networks=[],
        volumes=["vol-cap-20260826T120000Z-ab12-c1"],
        pids=[42],
        state_entries=["db.sqlite"],
        run_id=RUN,
    )
    assert report["ok"] is False
    assert report["containers"] == ["hs-cap-20260826T120000Z-ab12-c1"]
    assert report["pids"] == [42]


def test_residue_report_filters_unrelated_names():
    # A different run id must never be reported as residue of this run.
    report = residue_report(
        containers=["hs-cap-20260826T120001Z-zz99-c1"],
        networks=[],
        volumes=[],
        pids=[],
        state_entries=[],
        run_id=RUN,
    )
    assert report["ok"] is True
