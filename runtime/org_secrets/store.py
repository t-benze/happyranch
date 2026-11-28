"""Atomic encrypted org store, metadata receipts and process-local key cache."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from runtime.org_secrets.keyring import OSKeyring
from runtime.org_secrets.names import (
    MAX_SECRETS, MAX_TOTAL_BYTES, SecretError, validate_name, validate_value,
)

_STORES: dict[Path, OrgSecrets] = {}
_STORES_LOCK = threading.RLock()
_MAX_STORE_BYTES = 2 * 1024 * 1024
_RECEIPTS = 256


def store_for(org_root: Path) -> OrgSecrets:
    root = org_root.resolve()
    with _STORES_LOCK:
        if root not in _STORES:
            _STORES[root] = OrgSecrets(root)
        return _STORES[root]


class OrgSecrets:
    def __init__(self, org_root: Path) -> None:
        self.root = org_root.resolve()
        self.directory = self.root / 'secrets'
        self.keyring = OSKeyring(self.root)
        self._key: bytes | None = None
        self._keyring_available = False
        self._mutex = threading.RLock()
        self.probe()  # startup read only; never allocates a key or store

    @contextmanager
    def _locked(self):
        """No-follow fixed lock inode, shared by independent writers/restarts."""
        with self._mutex:
            try:
                self.directory.mkdir(mode=0o700, exist_ok=True)
                directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fchmod(directory, 0o700)
                    fd = os.open('store.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise SecretError('store_unavailable', 503)
                        os.fchmod(fd, 0o600)
                        deadline = time.monotonic() + 2
                        while True:
                            try:
                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                break
                            except BlockingIOError:
                                if time.monotonic() >= deadline:
                                    raise SecretError('store_unavailable', 503) from None
                                time.sleep(0.01)
                        yield directory
                    finally:
                        os.close(fd)
                finally:
                    os.close(directory)
            except SecretError:
                raise
            except Exception:
                raise SecretError('store_unavailable', 503) from None

    def _read(self, directory: int) -> dict:
        try:
            fd = os.open('store.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {'version': 1, 'revision': 0, 'records': {}, 'operations': {}}
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _MAX_STORE_BYTES:
                raise ValueError()
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'rb', closefd=False) as source:
                doc = json.loads(source.read(_MAX_STORE_BYTES + 1))
            if (set(doc) != {'version', 'revision', 'records', 'operations'} or doc['version'] != 1
                    or type(doc['revision']) is not int or doc['revision'] < 1
                    or not isinstance(doc['records'], dict) or len(doc['records']) > MAX_SECRETS
                    or not isinstance(doc['operations'], dict) or len(doc['operations']) > _RECEIPTS):
                raise ValueError()
            for name, record in doc['records'].items():
                validate_name(name)
                if (set(record) != {'name', 'updated_at', 'revision', 'nonce', 'ciphertext'}
                        or record['name'] != name or not isinstance(record['updated_at'], str)
                        or type(record['revision']) is not int or not 1 <= record['revision'] <= doc['revision']
                        or not isinstance(record['nonce'], str) or not isinstance(record['ciphertext'], str)):
                    raise ValueError()
            return doc
        except Exception:
            raise SecretError('store_unavailable', 503) from None
        finally:
            os.close(fd)

    def _write(self, directory: int, doc: dict) -> None:
        encoded = json.dumps(doc, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
        if len(encoded) > _MAX_STORE_BYTES:
            raise SecretError('store_unavailable', 503)
        # mkstemp starts 0600; only already encrypted records enter this buffer.
        fd, path = tempfile.mkstemp(prefix='.store-', dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as target:
                target.write(encoded)
                target.flush()
                os.fsync(target.fileno())
            os.replace(path, 'store.json', dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _aad(self, record: dict) -> bytes:
        return json.dumps([str(self.root), record['name'], record['updated_at'], record['revision']], separators=(',', ':')).encode()

    def _values(self, doc: dict) -> dict[str, str]:
        if not doc['records']:
            return {}
        if self._key is None:
            raise SecretError('key_locked', 423)
        try:
            result = {}
            for name, record in doc['records'].items():
                raw = AESGCM(self._key).decrypt(base64.b64decode(record['nonce'], validate=True),
                    base64.b64decode(record['ciphertext'], validate=True), self._aad(record))
                result[name] = validate_value(raw.decode('utf-8'))
            return result
        except Exception:
            self._key = None
            self._keyring_available = False
            raise SecretError('key_locked', 423) from None

    def probe(self) -> str:
        """Retain an already loaded key even if the login keyring relocks."""
        with self._mutex:
            if self._key is None:
                try:
                    self._key = self.keyring.read()
                    # A reachable, empty keyring is available for first New.
                    reachable = True
                except SecretError:
                    reachable = False
            else:
                reachable = True
            self._keyring_available = reachable
            if not reachable:
                return 'locked'
            if not self.directory.exists():
                return 'available'
            try:
                with self._locked() as directory:
                    doc = self._read(directory)
                    if doc['revision'] and self._key is None:
                        return 'locked'
                    self._values(doc)
                return 'available'
            except SecretError:
                return 'locked'

    def metadata(self) -> dict:
        # Metadata/Delete never require another OS keyring read or prompt.
        state = 'available' if self._keyring_available else 'locked'
        with self._locked() as directory:
            doc = self._read(directory)
            if doc['revision'] and self._key is None:
                state = 'locked'
            try:
                self._values(doc)
            except SecretError:
                state = 'locked'
            return {'key_state': state, 'revision': doc['revision'], 'secrets': [
                {key: record[key] for key in ('name', 'updated_at', 'revision')}
                for _, record in sorted(doc['records'].items())]}

    def current_values(self) -> dict[str, str]:
        """Slice B hook: coherent current values in memory; never a route."""
        with self._locked() as directory:
            return self._values(self._read(directory))

    def ready(self) -> bool:
        try:
            self.current_values()
            return True
        except SecretError:
            return False

    def operation(self, operation_id: str) -> dict:
        operation_id = validate_operation_id(operation_id)
        with self._locked() as directory:
            receipt = self._read(directory)['operations'].get(operation_id)
            return {'operation_id': operation_id, 'outcome': 'committed', **receipt} if receipt else {
                'operation_id': operation_id, 'outcome': 'unknown'}

    def mutate(self, action: str, name: str, value: object, operation_id: str) -> dict:
        name = validate_name(name)
        operation_id = validate_operation_id(operation_id)
        if action != 'delete':
            value = validate_value(value)
        with self._locked() as directory:
            doc = self._read(directory)
            if operation_id in doc['operations']:
                raise SecretError('operation_reused', 409)
            match = next((n for n in doc['records'] if n.casefold() == name.casefold()), None)
            if action == 'create' and match is not None:
                raise SecretError('secret_exists', 409)
            if action != 'create' and match is None:
                raise SecretError('secret_not_found', 404)
            revision = doc['revision'] + 1
            if action == 'delete':
                del doc['records'][match]
                result = {'name': name, 'deleted': True}
            else:
                if self._key is None:
                    try:
                        self._key = self.keyring.read()
                        if self._key is None:
                            if doc['revision']:
                                raise SecretError('key_locked', 423)
                            self._key = self.keyring.create()
                        self._keyring_available = True
                    except Exception:
                        raise SecretError('key_locked', 423) from None
                values = self._values(doc)
                values[name] = value
                if len(values) > MAX_SECRETS or sum(len(n.encode()) + len(v.encode()) + 2 for n, v in values.items()) > MAX_TOTAL_BYTES:
                    raise SecretError('environment_budget_exceeded', 413)
                result = {'name': name, 'updated_at': datetime.now(timezone.utc).isoformat()}
                record = {**result, 'revision': revision}
                nonce = os.urandom(12)
                ciphertext = AESGCM(self._key).encrypt(nonce, value.encode(), self._aad(record))
                doc['records'][name] = {**record, 'nonce': base64.b64encode(nonce).decode(),
                    'ciphertext': base64.b64encode(ciphertext).decode()}
            receipt = {**result, 'revision': revision, 'action': action}
            doc['revision'] = revision
            doc['operations'][operation_id] = receipt
            while len(doc['operations']) > _RECEIPTS:
                oldest = min(doc['operations'], key=lambda key: doc['operations'][key]['revision'])
                del doc['operations'][oldest]
            self._write(directory, doc)
            return {**result, 'revision': revision, 'operation_id': operation_id}


def validate_operation_id(value: object) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, AttributeError):
        raise SecretError('invalid_operation_id') from None
