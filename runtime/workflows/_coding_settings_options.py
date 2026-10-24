"""Bounded configuration selectors, never repository observations or grants."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from runtime.workflows._coding_release_history import _json_work
from runtime.workflows._coding_settings_records import SettingsOptions, SettingsRepositoryChoice, account
from runtime.workflows.coding_saved_records import VALUE_LIMIT, _canonical, _check, _hash, _json

if TYPE_CHECKING:
    from runtime.infrastructure.workflow_coding_schema import _CodingInventory
    from runtime.workflows.authority import AuthorityAdmissionCapture
    from runtime.workflows.coding_settings import CodingSettingsStore


def project_options(store: CodingSettingsStore, conn: sqlite3.Connection,
                    capture: AuthorityAdmissionCapture | None, inventory: _CodingInventory) -> dict:
    """Consume only the actual yielded capture and this complete local inventory.

    The caller owns authority/team/profile/publisher/DB locks and ONE read
    transaction/allowance. No file/Git reads, lease, publication or nested read.
    """
    budget = inventory.budget
    release = None
    if inventory.imported is not None:
        row = inventory.imported.rows[2][1]
        release = dict(identity=row['namespace'] + '/' + row['template_name'],
                       version=row['version'], definition_digest=row['definition_digest'])
        account(release, budget)
    pin, choices = None, {}
    if capture is not None:
        # Reserve all validator/decode/canonical/projection intermediates before
        # the existing current-admission validator reconstructs the snapshot.
        raw = capture.ready.snapshot_bytes
        _json_work(raw, budget, copies=6, passes=4)
        from runtime.workflows.authority import WorkflowAuthorityError
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        try:
            store.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            capture = None  # Retained valid settings/import remain readable.
        if capture is not None:
            snapshot = _json(raw, VALUE_LIMIT)
            _check(snapshot['org_slug'] == store.org.slug)
            pin = dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                       snapshot_digest=capture.ready.snapshot_digest)
            account(pin, budget)
            # Current authenticated configuration only, never the saved Mapping.
            # Traverse every configured row, including duplicate references.
            for agent in snapshot['agents']:
                budget.step()
                for key, origin in agent['repos'].items():
                    budget.step()
                    _check(type(key) is str and type(origin) is str and bool(origin))
                    _check(len(origin) * 4 <= budget.remaining_bytes)
                    budget.consumed += len(origin) * 4  # UTF8/hash source before encode.
                    digest = _hash(origin.encode('utf8'))
                    identity_source = [store.org.slug, key, digest]
                    account(identity_source, budget)
                    choice = dict(repository_id='workflow-repository:' + _hash(_canonical(identity_source)),
                                  repo_key=key, origin_digest=digest)
                    account(choice, budget)
                    SettingsRepositoryChoice.model_validate(choice)
                    _check(choice['repository_id'] not in choices or choices[choice['repository_id']] == choice)
                    choices[choice['repository_id']] = choice
                    _check(len(choices) <= 1024)
    result = dict(format='workflow-coding-settings-options@1', org_slug=store.org.slug,
                  release=release, authority=pin, repository_choices=list(choices.values()),
                  object_formats=('sha1', 'sha256'))
    # account accepts JSON values; reserve the serialized list representation.
    account({**result, 'object_formats': ['sha1', 'sha256']}, budget)
    SettingsOptions.model_validate(result)
    return result
