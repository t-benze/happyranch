"""Protected historical issuer inventory, with no issuance or provisioning owner."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import stat

from runtime.remote_access.installer_io import InstallerError, _FILE_FLAGS

INSTALLER_STATE = "var/lib/happyranch-remote-access"
HOME_ISSUER = INSTALLER_STATE + "/home-issuer"
_ISSUER_RECEIPT = "home-issuer-publication.json"


class LegacyIssuerReadback:
    """Read exact original records and pin their existing directory/lock."""

    @staticmethod
    def _headscale_no_pending(parent: int, name: str) -> None:
        try:
            os.stat(f".{name}.pending", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise InstallerError("publication-uncertain")

    def _issuer_receipt(self) -> dict | None:
        try:
            with self._directory(INSTALLER_STATE) as parent:
                self._headscale_no_pending(parent, "home-issuer")
                self._headscale_no_pending(parent, _ISSUER_RECEIPT)
                data = self._read_file(parent, _ISSUER_RECEIPT, owner=self.root_owner, mode=0o600)
        except FileNotFoundError:
            return None
        record = self._json(data)
        if (record.keys() != {"version", "directory", "uid", "gid", "device", "inode"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["directory"] != "/" + HOME_ISSUER
                or any(type(record[k]) is not int or record[k] < 0 for k in ("uid", "gid", "device", "inode"))
                or (record["uid"], record["gid"]) != self.root_owner):
            raise InstallerError("issuer receipt")
        return record

    @staticmethod
    def _issuer_signature(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _issuer_members(self, directory: int, *, db_retired: bool = False) -> dict[str, tuple[tuple[int, ...], bytes]]:
        names = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > 2 or entry.name not in {"invite-issuer.json", "invite-issuer.lock"}:
                    raise InstallerError("issuer residue")
        if names and names != {"invite-issuer.json", "invite-issuer.lock"}:
            raise InstallerError("issuer residue")
        members = {}
        for name in sorted(names):
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if name == "invite-issuer.lock":
                fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
                            or (info.st_uid, info.st_gid) != self.root_owner
                            or stat.S_IMODE(info.st_mode) != 0o600
                            or self._issuer_signature(info) != self._issuer_signature(before)):
                        raise InstallerError("issuer lock")
                finally:
                    os.close(fd)
                data = b""
            else:
                data = self._read_file(directory, name, owner=self.root_owner, mode=0o600)
                record = self._json(data)
                if (len(data) > 4096
                        or record.keys() != {"operation_id", "binding_digest", "not_before", "expires_at", "outcome"}
                        or any(type(record[k]) is not str or re.fullmatch(pattern, record[k]) is None
                               for k, pattern in (("operation_id", "[0-9a-f]{32}"), ("binding_digest", "[0-9a-f]{64}")))
                        or type(record["outcome"]) is not str
                        or record["outcome"] not in ({"issued", "refused", "expired", "encoding", "pairing", "recovered"}
                                                    | ({"minting", "uncertain"} if db_retired else set()))
                        or any(type(record[k]) not in (int, float) or not 0 <= record[k] < 253402300300
                               or not math.isfinite(record[k]) for k in ("not_before", "expires_at"))
                        or json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() != data):
                    raise InstallerError("issuer journal")
            if self._issuer_signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != self._issuer_signature(before):
                raise InstallerError("issuer changed")
            members[name] = self._issuer_signature(before), data
        return members

    def _issuer_inventory(self, *, cleanup: bool = False, db_retired: bool = False) -> tuple[dict | None, bool]:
        receipt = self._issuer_receipt()
        present = False
        try:
            with self._directory(INSTALLER_STATE) as parent:
                os.stat("home-issuer", dir_fd=parent, follow_symlinks=False)
                present = True
        except FileNotFoundError:
            pass
        if not present:
            # Retained cleanup receipts never license recreating an old directory.
            if receipt is not None and not cleanup:
                raise InstallerError("issuer directory missing") from None
            return receipt, False
        with self._directory(HOME_ISSUER) as directory:
            info = os.fstat(directory)
            if info.st_nlink != 2:
                raise InstallerError("issuer directory")
            members = self._issuer_members(directory, db_retired=db_retired)
            if "invite-issuer.lock" in members:
                lock = os.open("invite-issuer.lock", _FILE_FLAGS, dir_fd=directory)
                try:
                    if self._issuer_signature(os.fstat(lock)) != members["invite-issuer.lock"][0]:
                        raise InstallerError("issuer lock changed")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(lock)
        if receipt is None:
            if cleanup or members:
                raise InstallerError("issuer directory unreceipted")
        elif (receipt["device"], receipt["inode"]) != (info.st_dev, info.st_ino):
            raise InstallerError("issuer directory changed")
        return receipt, True
