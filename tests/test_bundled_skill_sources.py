from __future__ import annotations

import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import (
    _compute_dir_hash,
    materialize_workspace_skills,
)
from runtime.skills.skill_md import frontmatter_admission_violations
from runtime.skills.sources import bundled_skills_dir
from runtime.skills.system_contracts import list_system_contracts


def _bundled_source_findings(root: Path) -> list[tuple[Path, str, str]]:
    """Structural-first admission findings for every ``**/SKILL.md`` under root.

    Read-only over release-owned sources: the same allowed top-level key set the
    custom-skill validator enforces. A source with no frontmatter has no
    top-level keys and passes this allowed-set-only check.
    """
    findings: list[tuple[Path, str, str]] = []
    for source in sorted(root.rglob("SKILL.md")):
        text = source.read_text(encoding="utf-8")
        for code, message in frontmatter_admission_violations(text):
            findings.append((source, code, message))
    return findings


def _insert_frontmatter_line(text: str, line: str) -> str:
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index] == "---":
            lines.insert(index, line)
            return "\n".join(lines)
    raise AssertionError("source has no closing frontmatter fence")


def _mutated_source(tmp_path: Path, transform) -> Path:
    root = tmp_path / "sources"
    shutil.copytree(bundled_skills_dir() / "create-skill", root)
    skill = root / "SKILL.md"
    skill.write_text(transform(skill.read_text(encoding="utf-8")), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("HAPPYRANCH_PROTOCOL_DIR", raising=False)
    monkeypatch.delenv("HAPPYRANCH_PROJECT_ROOT", raising=False)


def test_release_contains_every_declared_system_contract() -> None:
    root = bundled_skills_dir()
    settings = Settings()
    assert settings.get_bundled_skills_dir() == root
    for contract in list_system_contracts():
        declared = settings.project_root / contract.source_path
        assert declared == root / contract.id / "SKILL.md"
        assert declared.is_file()
    assert (root / "make-worktree" / "worktree_guard.py").is_file()


def test_explicit_package_root_never_falls_back_to_this_checkout(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path / "missing-release")
    root = settings.get_bundled_skills_dir()
    assert root == tmp_path / "missing-release/runtime/skills/bundled"
    assert not root.exists()


@pytest.mark.parametrize("source", ["constructor", "environment", "yaml"])
def test_nondefault_legacy_source_requires_explicit_migration(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs: dict[str, str] = {}
    if source == "constructor":
        kwargs["protocol_dir"] = "custom-contracts"
    elif source == "environment":
        monkeypatch.setenv("HAPPYRANCH_PROTOCOL_DIR", "custom-contracts")
    else:
        home = tmp_path / "daemon-home"
        home.mkdir()
        (home / "config.yaml").write_text("protocol_dir: custom-contracts\n")
    with pytest.raises(ValueError, match="protocol_dir overrides are retired"):
        Settings(**kwargs)


def test_explicit_legacy_default_is_inert() -> None:
    assert Settings(protocol_dir="protocol").get_bundled_skills_dir() == bundled_skills_dir()


def test_start_task_source_is_generic_and_preserves_callback_contract(
    tmp_path: Path,
) -> None:
    """The real projected generic skill keeps callback mechanics, not engineering policy."""
    source = bundled_skills_dir() / "start-task" / "SKILL.md"
    workspace = tmp_path / "workspace"
    settings = Settings()
    materialize_workspace_skills(
        workspace,
        settings,
        slug="test",
        context="task",
        provider="codex",
        agent_name="dev_agent",
        team="engineering",
        skills_root=tmp_path / "no-managed-skills",
    )
    body = (workspace / ".agents" / "skills" / "start-task" / "SKILL.md").read_text()

    assert body == source.read_text()
    assert "Follow applicable task and role instructions for verification and review." in body
    assert "Report incomplete work and blockers with concrete evidence" in body
    assert "After the initial scope/progress checkpoint" in body
    assert "Include verification evidence fields when the applicable task or role" in body
    assert "happyranch report-completion --org {ORG_SLUG} --from-file" in body
    assert '"session_id": "<session_id>"' in body
    assert '"decision": {"action": "delegate"' in body
    assert "conditionally requires `manager_self_evaluation`" in body
    assert "Do not invent policy wording, clause identifiers, a canonical phrase" in body
    assert "Engineering frontend readiness gate" not in body
    assert "After initial Native Impact Evidence" not in body
    assert "TASK-5522" not in body
    assert "per-PR merge gate" not in body
    assert "scripts/local_ci.sh all" not in body
    assert "reviewer APPROVE + qa PASS" not in body
    assert "Running full web suite" not in body
    assert "Web suite green" not in body


def test_repository_scoped_local_ci_guidance_matches_ci_and_receipt_contract() -> None:
    """Keep engineering CI/receipt policy out of the generic projected skill."""
    checkout = Path(__file__).resolve().parents[1]
    guidance = (checkout / "docs" / "local-ci.md").read_text()
    workflow = (checkout / ".github" / "workflows" / "ci.yml").read_text()
    normalized_guidance = " ".join(guidance.split())

    assert '{"command":"scripts/local_ci.sh all","exit_code":0}' in guidance
    assert "failed, skipped, or other-target outcomes" in normalized_guidance
    for check in (
        "Python units on 3.14",
        "Web CI on Node 24",
        "Linux Canonical Store Validation (Ubuntu)",
        "macOS Canonical Store Validation (macOS 15)",
    ):
        assert check in normalized_guidance
    assert "Python 3.12/3.13/3.14" in normalized_guidance
    assert "nightly integration" in normalized_guidance
    assert "local `all` does not replace canonical-store validation" in normalized_guidance
    assert "The local `all` target covers the Python and Web commands only" in normalized_guidance
    assert "does **not** run the canonical-store validations" in guidance
    assert "Linux Canonical Store Validation (Ubuntu)" in workflow
    assert "macOS Canonical Store Validation (macOS 15)" in workflow


def test_source_relocation_preserves_hash_but_member_mutation_does_not(tmp_path: Path) -> None:
    source = bundled_skills_dir() / "make-worktree"
    relocated = tmp_path / "other-release" / "make-worktree"
    shutil.copytree(source, relocated)
    assert _compute_dir_hash(source) == _compute_dir_hash(relocated)
    member = relocated / "worktree_guard.py"
    member.write_bytes(member.read_bytes() + b"\n# changed release member\n")
    assert _compute_dir_hash(source) != _compute_dir_hash(relocated)


def test_assistant_discovery_uses_stable_source_marker() -> None:
    from runtime.system_assistant import _KNOWLEDGE_SOURCES, _source_knowledge_root

    root = _source_knowledge_root()
    assert (root / "runtime/config.py").is_file()
    assert not (root / "protocol").exists()
    for source, _target in _KNOWLEDGE_SOURCES:
        assert not source.startswith("protocol/")
        assert (root / source).is_file()


def test_frozen_release_data_contains_skills_and_assistant_knowledge(tmp_path: Path) -> None:
    """Evaluate the real packaging recipe without running a native compiler."""
    from runtime.system_assistant import _KNOWLEDGE_SOURCES

    checkout = Path(__file__).resolve().parents[1]
    build_root = tmp_path / "release-source"
    (build_root / "packaging").mkdir(parents=True)
    for name in ("runtime", "docs", "skills", "README.md", "pyproject.toml"):
        (build_root / name).symlink_to(checkout / name)
    spec = build_root / "packaging/daemon.spec"
    shutil.copyfile(checkout / "packaging/daemon.spec", spec)
    analyses: list[dict] = []

    def analysis(_entrypoints: list[str], **kwargs: object) -> SimpleNamespace:
        analyses.append(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs["datas"])

    runpy.run_path(str(spec), init_globals={
        "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })
    assert len(analyses) == 2  # daemon and CLI share the same release assets
    for result in analyses:
        shipped: dict[str, Path] = {}
        for source_name, destination in result["datas"]:
            source = Path(source_name)
            assert source.exists(), source
            if source.is_dir():
                for member in source.rglob("*"):
                    if member.is_file():
                        shipped[str(Path(destination) / member.relative_to(source))] = member
            else:
                shipped[str(Path(destination) / source.name)] = source
        for source_rel, _target in _KNOWLEDGE_SOURCES:
            assert f"runtime/system_knowledge/{source_rel}" in shipped
        for member in bundled_skills_dir().rglob("*"):
            if member.is_file():
                assert f"runtime/skills/bundled/{member.relative_to(bundled_skills_dir())}" in shipped
        assert not any(key.startswith("protocol/") for key in shipped)


# ── THR-262 bundled-source admission guard (C3b) ────────────────────────
#
# The guard parses every release-owned runtime/skills/bundled/**/SKILL.md and
# enforces the SAME closed top-level key set the custom-skill validator uses.
# The real corpus is never edited: each regression below copies one real
# bundled source tree to tmp_path and mutates exactly one file. Separately
# tracked SKILL.md copies under runtime/skills/{manage-agent,manage-repo,
# reflection}/, skills/happyranch/ and tests/fixtures/** are NOT this source
# directory and are excluded from this particular guard (O-1).


def test_real_bundled_corpus_uses_only_the_admission_allowlist() -> None:
    """P1: the unmodified real corpus passes (smoke only)."""
    assert _bundled_source_findings(bundled_skills_dir()) == []


@pytest.mark.parametrize("line,code,key", [
    ("vendor-x: 1", "admission_field_not_allowed", "vendor-x"),          # N1
    ("future-field: \"\"", "admission_field_not_allowed", "future-field"),  # N2
    ("allowed-tools: []", "admission_field_not_allowed", "allowed-tools"),  # N3
    ("hooks: {}", "admission_field_not_allowed", "hooks"),               # N4
])
def test_bundled_disallowed_top_level_key_fails(
    tmp_path: Path, line: str, code: str, key: str
) -> None:
    root = _mutated_source(tmp_path, lambda text: _insert_frontmatter_line(text, line))
    findings = _bundled_source_findings(root)
    assert len(findings) == 1, findings
    source, actual_code, message = findings[0]
    assert actual_code == code
    assert source.name == "SKILL.md" and source.parent == root
    assert key in message


def test_bundled_malformed_frontmatter_fails_without_admission_findings(
    tmp_path: Path,
) -> None:
    """N5: malformed-first; no admission finding follows a structural one."""
    root = _mutated_source(
        tmp_path, lambda text: text.replace("name: create-skill", "name: [unterminated")
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["skill_md_malformed_frontmatter"]


def test_bundled_duplicate_name_fails_not_last_wins(tmp_path: Path) -> None:
    """N6: duplicate-aware; never last-wins."""
    root = _mutated_source(
        tmp_path, lambda text: _insert_frontmatter_line(text, "name: create-skill")
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["frontmatter_duplicate_key"]
    assert findings[0][2].find("name") != -1


def test_bundled_duplicate_disallowed_key_with_empty_last_value_fails(
    tmp_path: Path,
) -> None:
    """N7: a duplicate empty grant cannot become a valid empty grant."""
    root = _mutated_source(
        tmp_path,
        lambda text: _insert_frontmatter_line(
            _insert_frontmatter_line(text, "allowed-tools: []"), "allowed-tools: []"
        ),
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["frontmatter_duplicate_key"]
    assert "allowed-tools" in findings[0][2]


def test_bundled_merge_key_is_rejected_in_copied_real_source(tmp_path: Path) -> None:
    """R2: a present ``<<`` merge declaration is admission-invalid by presence
    in the copied real source, exactly as the custom-skill route rejects it."""
    root = _mutated_source(
        tmp_path, lambda text: _insert_frontmatter_line(text, "<<: {}")
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["admission_field_not_allowed"]
    assert "<<" in findings[0][2]


def test_bundled_duplicate_merge_declarations_fail_as_one_duplicate(
    tmp_path: Path,
) -> None:
    """R2/R3 repair in copied real source: duplicate identity is the original
    root order before merge flattening, so two ``<<: {}`` declarations are one
    ``frontmatter_duplicate_key`` naming ``<<``, never two admission findings."""
    root = _mutated_source(
        tmp_path,
        lambda text: _insert_frontmatter_line(
            _insert_frontmatter_line(text, "<<: {}"), "<<: {}"
        ),
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["frontmatter_duplicate_key"]
    assert "<<" in findings[0][2]


def test_bundled_merge_override_reports_excluded_key_not_duplicate_name(
    tmp_path: Path,
) -> None:
    """R2/R3 repair in copied real source: a single ``<<`` merge that contributes
    a key the document already declares once is admission-invalid by presence,
    never a fabricated duplicate of that key."""
    root = _mutated_source(
        tmp_path,
        lambda text: _insert_frontmatter_line(text, "<<: {name: create-skill}"),
    )
    findings = _bundled_source_findings(root)
    assert [code for _, code, _ in findings] == ["admission_field_not_allowed"]
    assert "<<" in findings[0][2]


def _frontmatter_rewrite(text: str, frontmatter: str) -> str:
    """Replace the real source's frontmatter block with ``frontmatter``,
    keeping the real body bytes after the closing fence."""
    lines = text.split("\n")
    closing = lines.index("---", 1)
    return "\n".join(["---", frontmatter, "---", *lines[closing + 1:]])


def _frontmatter_removed(text: str) -> str:
    """Drop the real source's frontmatter entirely (absent-frontmatter case)."""
    lines = text.split("\n")
    closing = lines.index("---", 1)
    return "\n".join(lines[closing + 1:])


# P2/P3/P4: each positive case transforms a COPIED REAL create-skill source
# (never a synthetic document) so the guard is exercised against the shipping
# corpus shape. The real corpus itself is never mutated.
_BUNDLED_POSITIVES = {
    "P2-name-description-only": lambda text: _frontmatter_rewrite(
        text, "name: create-skill\ndescription: d"
    ),
    "P3-absent-frontmatter": _frontmatter_removed,
    "P4-string-metadata": lambda text: _insert_frontmatter_line(
        text, 'metadata: {a: "b"}'
    ),
}


@pytest.mark.parametrize("case", sorted(_BUNDLED_POSITIVES))
def test_bundled_positive_fixtures_pass(tmp_path: Path, case: str) -> None:
    """P2/P3/P4 applied to a copied real source still pass the guard, and the
    copied source keeps the real corpus body bytes (only the declared surface
    changes)."""
    real = (bundled_skills_dir() / "create-skill" / "SKILL.md").read_text(encoding="utf-8")
    root = _mutated_source(tmp_path, _BUNDLED_POSITIVES[case])
    assert _bundled_source_findings(root) == []
    mutated = (root / "SKILL.md").read_text(encoding="utf-8")
    assert mutated != real
    # The real corpus is untouched by any positive transformation.
    assert (bundled_skills_dir() / "create-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == real


def test_bundled_guard_radius_excludes_separately_tracked_copies() -> None:
    """O-1: the guard's source directory is exactly the release-owned tree."""
    root = bundled_skills_dir().resolve()
    assert root.name == "bundled" and root.parent.name == "skills"
    for excluded in ("manage-agent", "manage-repo", "reflection"):
        other = (root.parent / excluded).resolve()
        assert other != root and not other.is_relative_to(root)
