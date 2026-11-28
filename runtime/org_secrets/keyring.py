"""Explicit OS backends; no plugin discovery, subprocess, or file fallback."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import sys

from runtime.org_secrets.names import SecretError


class OSKeyring:
    def __init__(self, org_root: Path) -> None:
        # Distinct runtime installations with the same slug must not share a key.
        self.account = hashlib.sha256(os.fsencode(org_root.resolve())).hexdigest()
        self.service = 'HappyRanch org secrets v1'

    def _linux_collection(self):
        import secretstorage
        connection = secretstorage.dbus_init()
        try:
            # Unlike get_default_collection, this never creates/prompts.
            collection = secretstorage.Collection(connection)
            if collection.is_locked():
                raise SecretError('key_locked', 423)
            return connection, collection
        except BaseException:
            connection.close()
            raise

    def _mac_backend(self):
        from keyring.backends.macOS import Keyring
        backend = Keyring()
        backend.keychain = str(Path.home() / 'Library/Keychains/login.keychain-db')
        return backend

    def read(self) -> bytes | None:
        try:
            if sys.platform == 'linux':
                connection, collection = self._linux_collection()
                try:
                    items = list(collection.search_items({'application': self.service, 'account': self.account}))
                    if len(items) > 1:
                        raise SecretError('key_locked', 423)
                    raw = items[0].get_secret() if items else None
                finally:
                    connection.close()
            elif sys.platform == 'darwin':
                value = self._mac_backend().get_password(self.service, self.account)
                raw = value.encode('ascii') if value is not None else None
            else:
                raise SecretError('key_locked', 423)
            key = base64.b64decode(raw, validate=True) if raw is not None else None
            if key is not None and len(key) != 32:
                raise SecretError('key_locked', 423)
            return key
        except Exception:
            raise SecretError('key_locked', 423) from None

    def create(self) -> bytes:
        """Caller holds the org filesystem lock and has established no store."""
        try:
            existing = self.read()
            if existing is not None:
                return existing  # first-write failure residue is reusable
            key = os.urandom(32)
            encoded = base64.b64encode(key)
            if sys.platform == 'linux':
                connection, collection = self._linux_collection()
                try:
                    collection.create_item(self.service,
                        {'application': self.service, 'account': self.account},
                        encoded, replace=False)
                finally:
                    connection.close()
            elif sys.platform == 'darwin':
                self._mac_backend().set_password(self.service, self.account, encoded.decode('ascii'))
            else:
                raise SecretError('key_locked', 423)
            # A failed/uncertain write never licenses another replacement.
            observed = self.read()
            if observed != key:
                raise SecretError('key_locked', 423)
            return key
        except Exception:
            raise SecretError('key_locked', 423) from None
