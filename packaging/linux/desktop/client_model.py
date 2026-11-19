"""Native session lifecycle. GTK owns this model; blocking work never runs on GTK.

No secret is a snapshot field. Tokens fence every worker, timer and runtime load.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import threading
import time

_spec = importlib.util.spec_from_file_location("client_session", Path(__file__).with_name("client_session.py"))
_native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_native)
_spec = importlib.util.spec_from_file_location("client_store", Path(__file__).with_name("client_store.py"))
_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store)


def timestamp(value: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plain(value: str, limit: int = 256) -> str:
    if (type(value) is not str or not value or len(value.encode("utf-8")) > limit
            or re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", value)):
        raise ValueError("invalid native text")
    return value


class ClientModel:
    def __init__(self, prefix: Path, ui: object) -> None:
        self.prefix, self.ui = prefix, ui
        self.token = 0
        self.document = None
        self.session = None
        self.stage, self.runtime, self.access = "idle", "unknown", "unknown"
        self.observation, self.observed = "known", timestamp()
        self.expiry = None
        self.deadline = 0.0
        self.target = ""
        self.hold, self.deferred = False, []
        self.cleanup = "notApplicable"
        self.reading = False
        self.health_finish = None
        self.confirmation = 0
        self.confirmation_document = None
        self.confirming_target, self.host_prompt_document = False, None
        self.load_succeeded = False
        self.workers = set()
        self.worker_lock = threading.Lock()
        self.store = _store.SecretStore(_native)
        self.records = {}
        self.saved_access, self.saved_observation = "unknown", "unavailable"
        self.active_ref = None
        self.save_intent = False
        self.saving, self.save_stop = False, None
        self.storage_busy = False

    def snapshot(self) -> dict:
        return dict(stage=self.stage, runtime=self.runtime, savedAccess=self.saved_access,
                    route="unknown", observedAt=self.observed, observation=self.observation,
                    computers=self.computers(), activeComputerRef=self.active_ref,
                    expiresAt=timestamp(self.expiry) if self.expiry else None, comparisonPhrase=None)

    def action(self, effect: str = "none") -> dict:
        return dict(effect=effect, hostDenial="unknown", hostCleanup="unknown", localCleanup=self.cleanup,
                    networkCleanup="unknown", inviteState="unknown", expiresAt=None, host=None,
                    client=self.snapshot())

    def changed(self, stage: str | None = None) -> None:
        if stage:
            self.stage = stage
        self.observed = timestamp()
        self.ui.changed()

    def valid(self, token: int) -> bool:
        return token == self.token

    def work(self, token: int, operation: object, done: object, *, settle: bool = False) -> None:
        def worker() -> None:
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc) if isinstance(exc, (_native.ClientError, _store.StoreError)) else "unavailable"
            def deliver() -> bool:
                if self.valid(token) or settle:
                    if self.hold and self.valid(token) and not settle:
                        self.defer(lambda: done(result, error))
                    else:
                        done(result, error)
                return False
            self.ui.idle(deliver)
            with self.worker_lock:
                self.workers.discard(threading.current_thread())
        thread = threading.Thread(target=worker, daemon=True)
        with self.worker_lock:
            self.workers.add(thread)
        thread.start()

    def defer(self, continuation: object) -> None:
        if len(self.deferred) >= 8:
            self.fail("outcomeUnknown")
        else:
            self.deferred.append(continuation)

    def accept(self, document: object) -> None:
        self.token += 1
        token = self.token
        self.document = document
        self.load_succeeded = False
        self.expiry, self.cleanup = None, "notApplicable"
        self.observation = "known"
        self.active_ref, self.save_intent = None, False
        self.saved_access = "notSaved"
        # Reserve across paste, worker decoding and the later target response.
        self.confirming_target = True
        self.changed("validating")
        def pasted(text: str | None) -> None:
            if not self.valid(token):
                return
            if text is None:
                self.stop()
                return
            def decoded(invite: object, error: str | None) -> None:
                if error:
                    self.fail("invalidInvite")
                    return
                try:
                    self.target = plain(invite.home_id)
                    self.expiry = invite.expires_at
                    self.deadline = time.monotonic() + max(0, min(300, self.expiry - time.time()))
                    self.ui.target(invite, lambda consent, save=False: self.consent(token, invite, consent, save))
                except (ValueError, UnicodeError):
                    self.fail("invalidInvite")
            self.work(token, lambda: _native.decode_invite(text), decoded)
        self.ui.paste(pasted)

    def consent(self, token: int, invite: object, consent: bool, save: bool = False) -> None:
        if not self.valid(token):
            return
        self.confirming_target = False
        if not consent:
            self.stop()
            return
        self.approval_timeout(token)
        if not self.valid(token):
            return
        self.save_intent = save is True
        self.saved_access = "notSaved" if self.save_intent else "none"
        self.session = _native.ClientSession(self.prefix)
        session = self.session
        self.changed("joining")
        self.work(token, lambda: session.join(invite, save_intent=True) if self.save_intent else session.join(invite),
                  lambda _r, e: self.joined(token, e))

    def joined(self, token: int, error: str | None) -> None:
        if error:
            self.fail("networkUnavailable")
            return
        self.changed("pairing")
        self.work(token, self.session.pair, lambda result, e: self.paired(token, result, e))

    def paired(self, token: int, result: object, error: str | None) -> None:
        if error:
            self.fail("newInviteRequired")
            return
        try:
            phrase, expiry = result
            plain(phrase)
            if not time.time() < expiry <= self.expiry:
                raise ValueError("expiry")
            self.expiry = expiry
            self.deadline = min(self.deadline, time.monotonic() + expiry - time.time())
            self.ui.waiting(phrase, expiry, self.target)
        except (TypeError, ValueError, UnicodeError):
            self.fail("newInviteRequired")
            return
        self.changed("waitingApproval")
        self.approval_timeout(token)
        self.poll(token)

    def approval_expired(self, token: int) -> bool:
        if not self.valid(token):
            return True
        if (self.expiry is not None and self.stage in ("validating", "joining", "pairing", "waitingApproval")
                and (time.time() >= self.expiry or time.monotonic() >= self.deadline)):
            self.fail("expired")
            return True
        return False

    def approval_timeout(self, token: int) -> bool:
        if (not self.approval_expired(token)
                and self.stage in ("validating", "joining", "pairing", "waitingApproval")):
            remaining = min(self.expiry - time.time(), self.deadline - time.monotonic())
            self.ui.later(max(1, int(remaining * 1000) + 1), lambda: self.approval_timeout(token))
        return False

    def poll(self, token: int) -> bool:
        if self.approval_expired(token):
            return False
        if self.hold:
            self.defer(lambda: self.poll(token))
        else:
            self.work(token, self.session.complete, lambda result, e: self.completed(token, result, e))
        return False

    def completed(self, token: int, result: object, error: str | None) -> None:
        if self.approval_expired(token):
            return
        if error:
            self.fail("newInviteRequired")
        elif not result:
            # C23: a denial-shaped reply cannot prove rejection rather than pending.
            self.ui.later(1000, lambda: self.poll(token))
        else:
            self.ui.dismiss_waiting()
            if self.save_intent:
                self.save(token, opening=True)
            else:
                self.open(token)

    def open(self, token: int) -> None:
        self.changed("opening")
        self.work(token, self.session.open_runtime, lambda url, e: self.opened(token, url, e))

    def computers(self) -> list | None:
        if self.saved_observation != "known" and not self.records:
            return None
        # Human trust/identity stays in native confirmation; web gets opaque refs.
        return [dict(computerRef=ref, label="Saved computer " + str(i + 1))
                for i, ref in enumerate(self.records)]

    def discover(self, finish: object) -> None:
        if (self.storage_busy or self.saving or self.hold
                or self.stage not in ("idle", "failed", "connected", "opening")):
            finish(None, "busy", "none")
            return
        self.storage_busy = True
        def found(result: object, error: str | None) -> None:
            self.storage_busy = False
            self.records, state, self.saved_observation = result if not error else ({}, "unknown", "unavailable")
            if not self.session:
                self.saved_access = state
            self.changed()
            finish(dict(computers=self.computers(), savedAccess=state, observation=self.saved_observation), None, "none")
        self.work(self.token, self.store.list, found, settle=True)

    def save(self, token: int, *, opening: bool = False, finish: object = None) -> None:
        if self.saving or self.storage_busy or self.hold or not self.session or not self.save_intent:
            if finish:
                finish(None, "stateChanged", "none")
            return
        self.saving = True
        previous_stage = self.stage
        self.changed("saving")
        session = self.session
        def write() -> object:
            record = session.saved_record()
            self.store.save(record)
            return record
        def saved(record: object, error: str | None) -> None:
            self.saving = False
            self.saved_access = ("needsPermission" if error in ("keychainLocked", "keychainDenied")
                                 else "notSaved" if error else "saved")
            if not error:
                self.active_ref = _store.reference(record)
                self.records[self.active_ref] = record
                self.saved_observation = "known"
            if self.save_stop is not None:
                stop_finish, failed = self.save_stop
                self.save_stop = None
                self.stop(finish=stop_finish, failed=failed)
            elif self.valid(token):
                continuation = lambda: self.open(token) if opening else self.changed(previous_stage)
                if self.hold:
                    self.defer(continuation)
                else:
                    continuation()  # Custody failure keeps a usable session.
            if finish:
                finish(self.action("unknown" if error else "confirmed"),
                       error if error in ("keychainLocked", "keychainDenied") else "saveFailed" if error else None,
                       "unknown" if error else "confirmed")
        self.work(token, write, saved, settle=True)  # retain outcome after Cancel

    def connect_saved(self, ref: str, document: object, finish: object) -> None:
        if (self.session or self.storage_busy or self.hold or self.ui.prompt
                or self.stage not in ("idle", "failed") or self.cleanup == "incomplete"):
            finish(None, "busy", "none")
            return
        record = self.records.get(ref)
        if record is None:
            finish(None, "stateChanged", "none")
            return
        self.token += 1
        token = self.token
        self.document, self.active_ref = document, ref
        self.expiry, self.load_succeeded = None, False
        self.target = plain(record.home_id)
        self.changed("validating")
        def consent(yes: bool) -> None:
            if not self.valid(token):
                return
            if not yes:
                self.stop()
                return
            self.save_intent = True
            self.session = _native.ClientSession(self.prefix)
            session = self.session
            self.changed("joining")
            def resumed(_r: object, error: str | None) -> None:
                if error:
                    if error in ("keychainLocked", "keychainDenied"):
                        self.saved_access = "needsPermission"
                    self.fail(error if error in ("keychainLocked", "keychainDenied") else
                              "identityChanged" if error == "identityMismatch" else "savedUnavailable")
                else:
                    self.open(token)
            self.work(token, lambda: session.restore(self.store.load(record)), resumed)
        self.ui.saved_target(record, False, consent)
        finish(self.action(), None, "none")

    def remove_saved(self, ref: str, document: object, finish: object) -> None:
        if self.session or self.storage_busy or self.hold or self.ui.prompt or self.stage not in ("idle", "failed"):
            finish(None, "busy", "none")
            return
        record = self.records.get(ref)
        if record is None:
            finish(None, "stateChanged", "none")
            return
        token = self.token
        self.document = document
        self.confirmation += 1
        confirmation = self.confirmation
        self.confirmation_document, self.hold = document, True
        def confirmed(yes: bool) -> None:
            if not self.valid(token) or confirmation != self.confirmation:
                finish(None, "stateChanged", "none")
                return
            self.hold, self.confirmation_document = False, None
            if not yes:
                finish(self.action(), None, "none")
                return
            self.storage_busy = True
            def removed(_r: object, error: str | None) -> None:
                self.storage_busy = False
                if error:
                    self.saved_access, self.cleanup = "removalIncomplete", "incomplete"
                else:
                    self.records.pop(ref, None)
                    if self.active_ref == ref:
                        self.active_ref = None
                    self.saved_access = "saved" if self.records else "none"
                    self.cleanup = "confirmed"
                self.changed()
                finish(self.action("unknown" if error else "confirmed"),
                       "removalIncomplete" if error else None, "unknown" if error else "confirmed")
            self.work(token, lambda: self.store.delete(record), removed, settle=True)
        self.ui.saved_target(record, True, confirmed)

    def opened(self, token: int, url: str | None, error: str | None) -> None:
        if error:
            self.fail("runtimeUnavailable")
            return
        self.ui.open_runtime(url, token)
        self.ui.later(30000, lambda: self.load_timeout(token))

    def load_timeout(self, token: int) -> bool:
        if self.valid(token) and self.stage == "opening" and not self.load_succeeded:
            self.loaded(token, False)
        return False

    def loaded(self, token: int, success: bool) -> None:
        if not self.valid(token) or self.stage not in ("opening", "connected", "saving"):
            return
        if self.stage == "saving" and success:
            return
        if success:
            self.load_succeeded = True
        if self.hold:
            self.defer(lambda: self.loaded(token, success))
        elif not success:
            self.fail("runtimeUnavailable")
        else:
            self.runtime, self.access = "available", "allowed"
            self.changed("connected")

    def confirm_stop(self, finish: object, document: object = None) -> None:
        # Native runtime controls bypass Requests, including its mutation slot.
        if self.host_prompt_document is not None:
            finish(None, "busy", "none")
            return
        token = self.token
        self.confirmation_document = self.document if document is None else document
        self.confirmation += 1
        confirmation = self.confirmation
        self.hold = True
        def confirmed(yes: bool) -> None:
            if not self.valid(token) or confirmation != self.confirmation:
                finish(None, "stateChanged", "none")
                return
            if self.approval_expired(token):
                finish(None, "stateChanged", "none")
                return
            self.confirmation_document = None
            self.hold = False
            if yes:
                self.deferred = []
                self.stop(finish=finish)
            else:
                continuations, self.deferred = self.deferred, []
                finish(self.action(), None, "none")
                for continuation in continuations:
                    if self.valid(token):
                        continuation()
        self.ui.confirm_stop(self.target, confirmed)

    def fail(self, code: str) -> None:
        self.stop(failed=code)

    def stop(self, finish: object = None, failed: str | None = None) -> None:
        self.confirming_target = False
        if self.saving:
            if self.save_stop is None:
                self.token += 1
                self.hold, self.deferred = False, []
                self.save_stop = (finish, failed)
                self.ui.dismiss()
                self.ui.storage_wait()
                self.changed("stopping")  # storage write/readback safe point
            return
        self.token += 1  # Invalidate before stop dispatch, including late approval/load.
        token = self.token
        session, self.session = self.session, None
        if self.health_finish:
            self.health_finish(None, "stateChanged", "none")
            self.health_finish = None
        self.hold, self.deferred, self.reading = False, [], False
        self.confirmation_document = None
        self.expiry, self.target = None, ""
        self.runtime, self.access = "unknown", "unknown"
        self.ui.dismiss()
        self.ui.close_runtime()
        self.changed("stopping")
        def stopped(_result: object, error: str | None) -> None:
            self.cleanup = "incomplete" if error else "confirmed"
            self.changed("failed" if failed or error else "idle")
            if failed or error:
                self.ui.error(failed or "outcomeUnknown")
            if finish:
                finish(self.action("unknown" if error else "confirmed"),
                       "outcomeUnknown" if error else None, "unknown" if error else "confirmed")
        self.work(token, session.stop if session else lambda: None, stopped)

    def detach(self, document: object) -> None:
        if self.document == document or (self.hold and self.confirmation_document == document):
            if self.document == document:
                self.document = None
            self.confirmation += 1
            self.confirmation_document = None
            if self.stage not in ("connected", "idle", "failed", "stopping"):
                self.stop()
            elif self.hold:
                token = self.token
                continuations, self.deferred = self.deferred, []
                self.hold = False
                self.ui.dismiss()
                for continuation in continuations:
                    if self.valid(token):
                        continuation()

    def health(self, finish: object) -> None:
        if self.reading or self.stage not in ("opening", "connected") or self.hold:
            finish(None, "busy" if self.reading or self.hold else "stateChanged", "none")
            return
        self.reading = True
        self.health_finish = finish
        token = self.token
        def observed(_result: object, error: str | None) -> None:
            self.reading = False
            self.health_finish = None
            self.access = {"revoked": "revokedConfirmed", "accessUnavailable": "accessDenied"}.get(error, "unknown") if error else "allowed"
            self.observation = "unavailable" if error else "known"
            if error:
                self.runtime = "offline" if error == "daemonUnavailable" else "unknown"
                session, self.session = self.session, None
                self.token += 1
                self.changed("stopping")
                self.ui.close_runtime()
                def stopped(_r: object, cleanup_error: str | None) -> None:
                    self.cleanup = "incomplete" if cleanup_error else "confirmed"
                    self.changed("failed")
                self.work(self.token, session.stop, stopped)
            else:
                self.changed()
            finish(dict(access=self.access, runtime=self.runtime, route="unknown", observedAt=self.observed,
                        observation=self.observation), None, "none")
        self.work(token, self.session.health, observed)
