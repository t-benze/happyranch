"""Tests for the task-attachment private file store (THR-109)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from runtime.infrastructure.task_attachment_store import (
    MAX_TASK_ATTACHMENT_BYTES,
    TaskAttachmentStore,
    TaskAttachmentTooLarge,
    TaskAttachmentInvalidName,
    TaskAttachmentUnsupportedType,
    TaskAttachmentNotFound,
    sanitize_display_name,
    resolve_content_type,
    is_allowed_content_type,
)


class TestDisplayNameSanitization:
    def test_valid_names(self):
        assert sanitize_display_name("mockup.png") == "mockup.png"
        assert sanitize_display_name("design-doc.pdf") == "design-doc.pdf"
        assert sanitize_display_name("test_file.txt") == "test_file.txt"
        assert sanitize_display_name("a") == "a"
        assert sanitize_display_name("x" * 200) == "x" * 200

    def test_rejects_empty(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("")

    def test_rejects_too_long(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("x" * 201)

    def test_rejects_slash(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("path/to/file.png")

    def test_rejects_backslash(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("path\\file.png")

    def test_rejects_control_chars(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("file\x00.png")

    def test_rejects_null(self):
        with pytest.raises(TaskAttachmentInvalidName):
            sanitize_display_name("\x7f")


class TestContentTypeResolution:
    def test_allowed_types(self):
        assert resolve_content_type("file.png", "image/png") == "image/png"
        assert resolve_content_type("file.jpg", "image/jpeg") == "image/jpeg"
        assert resolve_content_type("file.jpeg", "image/jpeg") == "image/jpeg"
        assert resolve_content_type("file.gif", "image/gif") == "image/gif"
        assert resolve_content_type("file.webp", "image/webp") == "image/webp"
        assert resolve_content_type("file.pdf", "application/pdf") == "application/pdf"
        assert resolve_content_type("file.txt", "text/plain") == "text/plain"
        assert resolve_content_type("file.md", "text/markdown") == "text/markdown"
        assert resolve_content_type("file.csv", "text/csv") == "text/csv"
        assert resolve_content_type("file.json", "application/json") == "application/json"

    def test_normalizes_jpg_to_jpeg(self):
        assert resolve_content_type("file.jpg", "image/jpg") == "image/jpeg"

    def test_rejects_disallowed_type(self):
        with pytest.raises(TaskAttachmentUnsupportedType):
            resolve_content_type("malware.exe", "application/x-msdownload")

    def test_rejects_empty_type_with_unknown_extension(self):
        with pytest.raises(TaskAttachmentUnsupportedType):
            resolve_content_type("file.unknown", None)

    def test_falls_back_to_extension(self):
        assert resolve_content_type("screenshot.png", None) == "image/png"
        assert resolve_content_type("data.csv", None) == "text/csv"
        assert resolve_content_type("notes.md", None) == "text/markdown"


class TestTaskAttachmentStore:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield TaskAttachmentStore(Path(tmp))

    def test_put_and_read(self, store):
        content = b"hello world"
        size = store.put("ta-0001", content)
        assert size == len(content)
        assert store.read("ta-0001") == content

    def test_read_missing(self, store):
        with pytest.raises(TaskAttachmentNotFound):
            store.read("nonexistent")

    def test_too_large(self, store):
        content = b"x" * (MAX_TASK_ATTACHMENT_BYTES + 1)
        with pytest.raises(TaskAttachmentTooLarge):
            store.put("ta-0001", content)

    def test_max_size_passes(self, store):
        content = b"x" * MAX_TASK_ATTACHMENT_BYTES
        size = store.put("ta-0001", content)
        assert size == MAX_TASK_ATTACHMENT_BYTES

    def test_delete(self, store):
        store.put("ta-0001", b"data")
        store.delete("ta-0001")
        with pytest.raises(TaskAttachmentNotFound):
            store.read("ta-0001")

    def test_delete_missing_noop(self, store):
        store.delete("nonexistent")  # should not raise

    def test_overwrite(self, store):
        store.put("ta-0001", b"old")
        store.put("ta-0001", b"new")
        assert store.read("ta-0001") == b"new"

    def test_put_to_subdirectory(self, store):
        """Storage keys can contain path separators."""
        content = b"data"
        store.put("task/TASK-001/mockup.png", content)
        assert store.read("task/TASK-001/mockup.png") == content
