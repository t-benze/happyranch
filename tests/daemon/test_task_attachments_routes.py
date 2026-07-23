"""Tests for task attachment routes (THR-109)."""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient


def _upload_file_bytes(
    client: TestClient,
    file_bytes: bytes,
    filename: str = "test.png",
    content_type: str = "image/png",
    agent: str = "founder",
) -> dict:
    """Helper to upload a task attachment."""
    r = client.post(
        "/api/v1/orgs/alpha/tasks/attachments",
        files={"file": (filename, file_bytes, content_type)},
        params={"agent": agent},
    )
    return r.json()


class TestUploadTaskAttachment:
    def test_uploads_png(self, tmp_home, app):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks/attachments",
            files={"file": ("mockup.png", b"\x89PNG\x0d\x0a\x1a\x0ahello", "image/png")},
            params={"agent": "founder"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "storage_key" in body
        assert body["display_name"] == "mockup.png"
        assert body["size_bytes"] == 13
        assert body["content_type"] == "image/png"
        assert body["uploaded_by"] == "founder"

    def test_rejects_too_large(self, tmp_home, app):
        from runtime.infrastructure.task_attachment_store import MAX_TASK_ATTACHMENT_BYTES

        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks/attachments",
            files={"file": ("big.bin", b"x" * (MAX_TASK_ATTACHMENT_BYTES + 1), "application/octet-stream")},
            params={"agent": "founder"},
        )
        assert r.status_code == 413

    def test_uploads_pdf(self, tmp_home, app):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks/attachments",
            files={"file": ("spec.pdf", b"%PDF-1.4 test", "application/pdf")},
            params={"agent": "dev_agent"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["content_type"] == "application/pdf"

    def test_rejects_unknown_types(self, tmp_home, app):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks/attachments",
            files={"file": ("virus.exe", b"malware", "application/x-msdownload")},
            params={"agent": "founder"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "unsupported_attachment_type"


class TestTaskCreateWithAttachments:
    def test_create_task_with_pre_uploaded_files(self, tmp_home, app, org_state):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        # Upload an attachment first.
        r = client.post(
            "/api/v1/orgs/alpha/tasks/attachments",
            files={"file": ("mockup.png", b"\x89PNG\x0d\x0a\x1a\x0ahello", "image/png")},
            params={"agent": "founder"},
        )
        assert r.status_code == 200
        storage_key = r.json()["storage_key"]

        # Create task referencing the attachment.
        r = client.post(
            "/api/v1/orgs/alpha/tasks",
            json={
                "brief": "test task with attachment",
                "attachments": [{"storage_key": storage_key, "display_name": "mockup.png"}],
            },
        )
        assert r.status_code == 200
        task_id = r.json()["task_id"]

        # Verify the attachment is linked to the task.
        attachments = org_state.db.list_task_attachments(task_id)
        assert len(attachments) == 1
        assert attachments[0].storage_key == storage_key
        assert attachments[0].display_name == "mockup.png"

    def test_rejects_missing_storage_key(self, tmp_home, app):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks",
            json={
                "brief": "test",
                "attachments": [{"storage_key": "nonexistent", "display_name": "x.png"}],
            },
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "task_attachment_not_found"

    def test_rejects_too_many_attachments(self, tmp_home, app):
        from runtime.infrastructure.task_attachment_store import MAX_TASK_ATTACHMENTS_PER_TASK

        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        refs = []
        for i in range(MAX_TASK_ATTACHMENTS_PER_TASK + 1):
            refs.append({"storage_key": f"ta-{i:04d}", "display_name": f"file{i}.png"})

        r = client.post(
            "/api/v1/orgs/alpha/tasks",
            json={"brief": "test", "attachments": refs},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "too_many_attachments"


class TestListTaskAttachments:
    def test_lists_attachments(self, tmp_home, app, org_state):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        # Create a task with attachments via the direct DB path.
        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        org_state.db.insert_task(TaskRecord(
            id="TASK-ATT-001", brief="test", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-ATT-001", ordinal=0,
            storage_key="ta-list-1", display_name="a.png",
            size_bytes=100, content_type="image/png", uploaded_by="founder",
        )
        org_state.db.insert_task_attachment(
            task_id="TASK-ATT-001", ordinal=1,
            storage_key="ta-list-2", display_name="b.pdf",
            size_bytes=200, content_type="application/pdf", uploaded_by="founder",
        )

        r = client.get("/api/v1/orgs/alpha/tasks/TASK-ATT-001/attachments")
        assert r.status_code == 200
        attachments = r.json()["attachments"]
        assert len(attachments) == 2
        assert attachments[0]["display_name"] == "a.png"
        assert attachments[1]["display_name"] == "b.pdf"

    def test_lists_includes_inherited_always(self, tmp_home, app, org_state):
        """Under seq25, list always returns own + ancestor attachments."""
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        # Root task with an attachment.
        org_state.db.insert_task(TaskRecord(
            id="TASK-INH-ROOT", brief="root", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-INH-ROOT", ordinal=0,
            storage_key="ta-inh-root", display_name="root.png",
            size_bytes=100, content_type="image/png", uploaded_by="founder",
        )
        # Child task with its own attachment.
        org_state.db.insert_task(TaskRecord(
            id="TASK-INH-CHILD", brief="child", team="engineering",
            parent_task_id="TASK-INH-ROOT", created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-INH-CHILD", ordinal=0,
            storage_key="ta-inh-child", display_name="child.png",
            size_bytes=200, content_type="image/png", uploaded_by="founder",
        )

        # Child listing should return both own + ancestor.
        r = client.get("/api/v1/orgs/alpha/tasks/TASK-INH-CHILD/attachments")
        assert r.status_code == 200
        attachments = r.json()["attachments"]
        names = {a["display_name"] for a in attachments}
        assert "child.png" in names
        assert "root.png" in names

    def test_list_unknown_task_returns_404(self, tmp_home, app):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})
        r = client.get("/api/v1/orgs/alpha/tasks/TASK-NONEXIST/attachments")
        assert r.status_code == 404

    def test_list_cross_org_access_denied(self, tmp_home, app, org_state):
        """Cross-org access must be denied by the existing org-scoped context."""
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        org_state.db.insert_task(TaskRecord(
            id="TASK-ORG-001", brief="test", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-ORG-001", ordinal=0,
            storage_key="ta-org-1", display_name="test.png",
            size_bytes=100, content_type="image/png", uploaded_by="founder",
        )

        # Access via a different org slug must be denied.
        r = client.get("/api/v1/orgs/beta/tasks/TASK-ORG-001/attachments")
        assert r.status_code == 404  # Task doesn't exist in beta org

        # Access to the correct org's task should work.
        r = client.get("/api/v1/orgs/alpha/tasks/TASK-ORG-001/attachments")
        assert r.status_code == 200

    def test_list_empty_task(self, tmp_home, app, org_state):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        org_state.db.insert_task(TaskRecord(
            id="TASK-EMPTY", brief="empty", team="engineering",
            created_at=now, updated_at=now,
        ))

        r = client.get("/api/v1/orgs/alpha/tasks/TASK-EMPTY/attachments")
        assert r.status_code == 200
        assert r.json()["attachments"] == []


class TestDownloadTaskAttachment:
    def test_downloads_file(self, tmp_home, app, org_state):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        org_state.db.insert_task(TaskRecord(
            id="TASK-DL-001", brief="test", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-DL-001", ordinal=0,
            storage_key="ta-dl-1", display_name="test.txt",
            size_bytes=12, content_type="text/plain", uploaded_by="founder",
        )
        # Write bytes to the file store.
        from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
        from runtime.orchestrator._paths import OrgPaths
        store = TaskAttachmentStore(OrgPaths(org_state.root).task_attachments_dir)
        store.put("ta-dl-1", b"hello world!")

        r = client.get("/api/v1/orgs/alpha/tasks/TASK-DL-001/attachments/ta-dl-1")
        assert r.status_code == 200
        assert r.content == b"hello world!"
        assert "text/plain" in r.headers["content-type"]

    def test_cross_org_download_denied(self, tmp_home, app, org_state):
        """Cross-org access must be denied by the existing org-scoped context."""
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        org_state.db.insert_task(TaskRecord(
            id="TASK-DL-CROSS", brief="test", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-DL-CROSS", ordinal=0,
            storage_key="ta-dl-cross", display_name="test.txt",
            size_bytes=12, content_type="text/plain", uploaded_by="founder",
        )
        from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
        from runtime.orchestrator._paths import OrgPaths
        store = TaskAttachmentStore(OrgPaths(org_state.root).task_attachments_dir)
        store.put("ta-dl-cross", b"secret")

        # Cross-org access must be denied.
        r = client.get("/api/v1/orgs/beta/tasks/TASK-DL-CROSS/attachments/ta-dl-cross")
        assert r.status_code == 404

        # Correct org access must work.
        r = client.get("/api/v1/orgs/alpha/tasks/TASK-DL-CROSS/attachments/ta-dl-cross")
        assert r.status_code == 200

    def test_no_requester_task_identity_accepted(self, tmp_home, app, org_state):
        """Under seq25, no requester task/session identity is accepted or
        required for read access. Any auth'd same-org bearer can download."""
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {_read_test_token()}"})

        from datetime import datetime, timezone
        from runtime.models import TaskRecord

        now = datetime.now(timezone.utc)
        # Create a task and attachment.
        org_state.db.insert_task(TaskRecord(
            id="TASK-NOREQ", brief="test", team="engineering",
            created_at=now, updated_at=now,
        ))
        org_state.db.insert_task_attachment(
            task_id="TASK-NOREQ", ordinal=0,
            storage_key="ta-noreq", display_name="test.txt",
            size_bytes=6, content_type="text/plain", uploaded_by="founder",
        )
        from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
        from runtime.orchestrator._paths import OrgPaths
        store = TaskAttachmentStore(OrgPaths(org_state.root).task_attachments_dir)
        store.put("ta-noreq", b"public")

        # The bearer token has NO task/session identity — it's a generic
        # org-scoped auth token. The download must succeed without any
        # requester-task proof.
        r = client.get("/api/v1/orgs/alpha/tasks/TASK-NOREQ/attachments/ta-noreq")
        assert r.status_code == 200
        assert r.content == b"public"


def _read_test_token() -> str:
    from runtime.daemon import paths as paths_mod
    return paths_mod.read_token()
