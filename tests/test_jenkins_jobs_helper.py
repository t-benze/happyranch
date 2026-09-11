from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


HELPER = Path(__file__).parents[1] / "scripts" / "jenkins_jobs.py"
SPEC = importlib.util.spec_from_file_location("jenkins_jobs", HELPER)
assert SPEC and SPEC.loader
jenkins = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = jenkins
SPEC.loader.exec_module(jenkins)


def test_job_path_encodes_context_and_folder_names() -> None:
    assert jenkins.job_path("team folder/release one") == "/job/team%20folder/job/release%20one"
    assert jenkins.controller_url("https://ci.example.test/jenkins/", "team folder/release one") == "https://ci.example.test/jenkins/job/team%20folder/job/release%20one"


def test_rejects_unsafe_names_and_urls() -> None:
    for value in ("../x", "a//b", "a/%2f/b", "", "/absolute"):
        try:
            jenkins.job_path(value)
        except jenkins.ValidationError:
            pass
        else:
            raise AssertionError(value)
    for value in ("http://ci.example.test", "https://user@ci.example.test", "https://ci.example.test/a/../b"):
        try:
            jenkins.validate_controller(value, allow_http=False)
        except jenkins.ValidationError:
            pass
        else:
            raise AssertionError(value)


def test_parameter_selection_rejects_undeclared_or_unsupported() -> None:
    metadata = {"property": [{"parameterDefinitions": [
        {"name": "branch", "type": "hudson.model.StringParameterDefinition"},
        {"name": "choice", "type": "hudson.model.ChoiceParameterDefinition"},
    ]}]}
    assert jenkins.prepare_parameters(metadata, {"branch": "main", "choice": "x"}) == {"branch": "main", "choice": "x"}
    for params in ({"missing": "x"}, {"branch": "x", "choice": "x", "secret": "no"}):
        try:
            jenkins.prepare_parameters(metadata, params)
        except jenkins.ValidationError:
            pass
        else:
            raise AssertionError(params)


def test_receipt_intent_blocks_duplicate_submission(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    receipt = jenkins.open_receipt(path, "https://ci.example.test", "example")
    assert receipt["phase"] == "intent"
    try:
        jenkins.open_receipt(path, "https://ci.example.test", "example")
    except jenkins.ReceiptError:
        pass
    else:
        raise AssertionError("must not overwrite intent")


def test_url_validation_refuses_cross_origin_identity() -> None:
    origin = jenkins.Controller("https://ci.example.test/jenkins")
    assert origin.checked("https://ci.example.test/jenkins/queue/item/7/", "/queue/item/7/")
    for url in ("https://evil.example/jenkins/queue/item/7/", "https://ci.example.test/queue/item/7/", "https://ci.example.test/jenkins/queue/item/%2f7/"):
        try:
            origin.checked(url, "/queue/item/")
        except jenkins.ValidationError:
            pass
        else:
            raise AssertionError(url)


def test_terminal_result_mapping() -> None:
    assert jenkins.outcome({"building": False, "result": "SUCCESS"}) == "SUCCESS"
    assert jenkins.outcome({"building": False, "result": "FAILURE"}) == "FAILURE"
    assert jenkins.outcome({"building": False, "result": "SOMETHING"}) == "UNSUPPORTED_RESULT:SOMETHING"
