"""Native session lifecycle. GTK owns this model; blocking work never runs on GTK.

No secret is a snapshot field. Tokens fence every worker, timer and runtime load.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sys
import threading
import time
import uuid

_spec = importlib.util.spec_from_file_location("client_session", Path(__file__).with_name("client_session.py"))
_native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_native)
_spec = importlib.util.spec_from_file_location("client_store", Path(__file__).with_name("client_store.py"))
_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store)
_startup = sys.modules.get("_happyranch_startup_selection")
if _startup is None:
    _spec = importlib.util.spec_from_file_location("_happyranch_startup_selection",
                                                  Path(__file__).with_name("app_startup_selection.py"))
    _startup = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_startup)
    sys.modules["_happyranch_startup_selection"] = _startup


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
        self._cleanup = "notApplicable"
        self.session_cleanups = {}
        self.reading = False
        self.health_finish = None
        self.confirmation = 0
        self.confirmation_document = None
        self.confirming_target, self.host_prompt_document = False, None
        self.workers = set()
        self.worker_lock = threading.Lock()
        self.store = _store.SecretStore(_native)
        self.records = {}
        self.saved_access, self.saved_observation = "unknown", "unavailable"
        self.active_ref = None
        self.save_intent = False
        self.saving, self.save_stop = False, None
        self.storage_busy = False
        self.main = self.bridge_types = None
        self.active_session = self.active_target = None
        self.active_unsaved = False
        self.startup_selection_saved = True
        self.active_projection = None
        self.switch_candidate = self.candidate_target = self.candidate_error = None
        self.candidate_stop = threading.Event()
        self.owner_snapshot = lambda: self.main.owner_snapshot() if self.main is not None else None

    @property
    def cleanup(self) -> str:
        # Only this session's successful stop may settle its owned result.
        outcomes = self.session_cleanups.values()
        if self._cleanup == "incomplete" or "incomplete" in outcomes:
            return "incomplete"
        return "unknown" if "unknown" in outcomes else self._cleanup

    def cleanup_finished(self, session: object, error: str | None) -> None:
        if session is not None:
            if error:
                self.session_cleanups[session] = "incomplete"
            else:
                self.session_cleanups.pop(session, None)

    def attach_main(self, main: object, bridge_types: object) -> None:
        """Attach the same native owner before a fresh Main document is loaded."""
        if self.main is not None and self.main is not main:
            main.owner_uncertain |= self.main.owner_uncertain
            if self.switch_candidate and self.switch_candidate["phase"] not in ("failed", "cancelled"):
                self.stop()
            self.main.client_dispatch = None
        self.main, self.bridge_types = main, bridge_types
        # Recreating Main is initial attachment of the SAME committed target.
        # No document or transfers exist in the new view yet. Voluntary target
        # changes below always use select_target and its owner/non-GET gate.
        if self.active_target is not None:
            main.daemon = bridge_types.RemoteDaemon(self.active_session, ready=True,
                target_id=self.active_target.target_id, name=self.active_target.name)
            self.active_target = main.daemon
        main.client_dispatch = self.dispatch
        self.publish_candidate()

    def publish_candidate(self) -> None:
        if self.main is None or self.main.closed:
            return
        if self.switch_candidate is not None:
            phase = {"validating": "signing_in", "joining": "signing_in", "pairing": "awaiting_approval",
                     "waitingApproval": "awaiting_approval", "failed": "failed"}.get(self.stage)
            if phase:
                self.switch_candidate["phase"] = phase
            if self.expiry is not None:
                self.switch_candidate["deadline"] = timestamp(self.expiry)
        self.main.candidate = dict(self.switch_candidate) if self.switch_candidate else None
        self.main.access_unsaved = self.active_unsaved
        self.main.emit_state()

    def dispatch(self, command: str, document: object, finish: object, *, computerRef: str = "") -> None:
        """Only AppScheme's admitted Main-document hook calls this dispatcher."""
        if command == "client.status":
            finish(self.snapshot())
        elif command == "client.savedComputers":
            self.discover(finish)
        elif command == "client.health":
            self.health(finish)
        elif command == "client.removeSaved":
            self.remove_saved(computerRef, document, finish)
        elif command == "client.retrySave":
            if self.stage == "connected" and self.saved_access != "saved":
                self.save(self.token, finish=finish)
            else:
                finish(None, "stateChanged")
        elif command in ("client.acceptInvite", "client.retry", "client.connectSaved"):
            if (self.session or self.saving or self.storage_busy or self.reading or self.hold or self.ui.prompt
                    or self.host_prompt_document is not None or self.stage not in ("idle", "failed", "connected")
                    or self.cleanup in ("unknown", "incomplete")):
                finish(None, "busy")
                return
            if command == "client.connectSaved" and computerRef not in self.records:
                finish(None, "stateChanged")
                return
            if command == "client.connectSaved":
                self.connect_saved(computerRef, document, finish)
            else:
                self.accept(document)
                finish(self.action())
        elif command == "client.cancelAttempt":
            if self.switch_candidate is None or self.switch_candidate["phase"] in ("failed", "cancelled"):
                finish(None, "stateChanged")
            else:
                self.confirm_stop(finish, document)
        elif command == "client.disconnect":
            if self.active_session is None or self.session is not None:
                finish(None, "stateChanged")
            else:
                self.confirm_stop(finish, document)
        elif command == "client.commitSwitch":
            self.commit_switch(finish)
        else:
            finish(None, "unsupportedCommand")

    def begin_candidate(self) -> None:
        if self.main is not None:
            if self.active_session is not None and self.active_projection is None:
                self.active_projection = (self.save_intent, self.active_ref, self.saved_access,
                    self.stage, self.runtime, self.access, self.observation, self.target)
            self.switch_candidate = {"kind": "remote", "id": uuid.uuid4().hex,
                "name": "远程电脑" if self.main.locale["current"] == "zh-CN" else "Remote computer",
                "phase": "signing_in", "deadline": None}
            self.candidate_target = self.candidate_error = None
            self.candidate_stop = threading.Event()

    def commit_switch(self, finish: object) -> None:
        if not self.valid(self.token):
            finish(None, "documentChanged")
            return
        if self.candidate_error:
            finish(None, self.candidate_error)
            return
        if (not self.switch_candidate or self.switch_candidate["phase"] != "ready_to_switch"
                or self.candidate_target is None or self.session is None or self.saving or self.hold):
            finish(None, "target_starting")
            return
        previous = self.main.candidate, self.main.access_unsaved
        self.main.candidate, self.main.access_unsaved = None, self.saved_access != "saved"
        reason = self.main.select_target(self.candidate_target, owner_saves=self.owner_saves())
        if reason:
            self.main.candidate, self.main.access_unsaved = previous
            finish(None, reason)
            return
        old = self.active_session
        if old is not None and old is not self.session:
            self.session_cleanups[old] = "unknown"
        self.active_session, self.active_target = self.session, self.candidate_target
        self.active_unsaved = self.saved_access != "saved"
        self.active_projection = None
        self.session = self.candidate_target = self.switch_candidate = None
        self.document = None
        self.runtime, self.access = "available", "allowed"
        self.startup_selection_saved = _startup.set(
            "remote:" + self.active_ref if not self.active_unsaved and self.active_ref
            else "remote:unsaved")["persisted"]
        self.changed("connected")
        # Swap/cancellation/event already occurred. Cleanup is independent and
        # cannot roll back an admitted target or claim atomic cleanup success.
        if old is not None and old is not self.active_session:
            def stopped(_result: object, error: str | None) -> None:
                self.cleanup_finished(old, error)
                self.changed()
            self.work(self.token, old.stop, stopped, settle=True)
        finish(self.action("confirmed"))

    def owner_saves(self) -> tuple[str, ...] | None:
        try:
            owners = self.owner_snapshot()
        except Exception:
            return None  # Unsupported/unavailable owner evidence fails closed.
        return owners if type(owners) is tuple and all(type(state) is str for state in owners) else None

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
        self.publish_candidate()
        self.ui.changed()

    def valid(self, token: int) -> bool:
        if (token == self.token and self.main is not None and type(self.document) is int
                and (self.main.closed or self.document != self.main.document)):
            self.document = None
            self.stop()
            return False
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
        self.begin_candidate()
        self.token += 1
        token = self.token
        self.document = document
        self.expiry, self._cleanup = None, "notApplicable"
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
                    def found(result: object, error: str | None) -> None:
                        records, _state, observation = result if not error else ({}, "unknown", "unavailable")
                        record = _native.recovery_candidate(records, invite) if observation == "known" else None
                        if record is None:
                            self.ui.target(invite, lambda yes, save=False: self.consent(token, invite, yes, save))
                        else:
                            self.ui.recovery_target(invite, lambda yes: self.recover(token, record, invite, yes))
                    self.work(token, self.store.list, found)
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

    def recover(self, token: int, record: object, invite: object, consent: bool) -> None:
        if not self.valid(token):
            return
        self.confirming_target = False
        if not consent:
            self.stop()
            return
        self.approval_timeout(token)
        if not self.valid(token):
            return
        self.save_intent = True
        self.saved_access = "saved"
        self.session = _native.ClientSession(self.prefix)
        session = self.session
        self.changed("joining")
        def recovered(_result: object, error: str | None) -> None:
            if not error:
                self.save(token, opening=True)
                return
            # Never spend the invitation automatically after failed validation.
            # Finish cleanup before a distinct normal-join consent dialog.
            def stopped(_r: object, cleanup_error: str | None) -> None:
                if cleanup_error:
                    self.fail("outcomeUnknown")
                    return
                self.session = None
                self.save_intent = False
                self.confirming_target = True
                self.changed("validating")
                self.ui.target(invite, lambda yes, save=False: self.consent(token, invite, yes, save), fresh=True)
            self.work(token, session.stop, stopped)
        self.work(token, lambda: session.recover_address(self.store.load(record), invite), recovered)

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
        if self.main is None or self.main.closed:
            self.fail("runtimeUnavailable")
            return
        session = self.session
        candidate = self.switch_candidate
        if candidate is None:
            # Legacy native controls share the same owner, never a second
            # runtime window once Main owns routing.
            self.switch_candidate = candidate = {"kind": "remote", "id": uuid.uuid4().hex,
                "name": "远程电脑" if self.main.locale["current"] == "zh-CN" else "Remote computer",
                "phase": "signing_in", "deadline": None}
        target = self.bridge_types.RemoteDaemon(session, ready=True,
            target_id=candidate["id"], name=candidate["name"])
        stop = self.candidate_stop
        def prepare() -> object:
            try:
                target.bootstrap(stop)
                return target, None
            except self.bridge_types.SchemeError as error:
                detail = {"code": error.code}
                if error.code == "incompatible":
                    detail["side"] = error.side
                return None, detail
        def prepared(result: object, error: str | None) -> None:
            target, detail = result if not error else (None, {"code": "target_unreachable"})
            if detail:
                self.candidate_error = detail
                self.fail("runtimeUnavailable")
            else:
                self.candidate_target = target
                self.switch_candidate["phase"] = "ready_to_switch"
                self.changed()
        self.work(token, prepare, prepared)

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
        session = self.session or self.active_session
        if self.saving or self.storage_busy or self.hold or not session or not self.save_intent:
            if finish:
                finish(None, "stateChanged", "none")
            return
        self.saving = True
        previous_stage = self.stage
        self.changed("saving")
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
                if session is self.active_session:
                    self.active_unsaved = False
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
        if (self.session or self.storage_busy or self.reading or self.hold or self.ui.prompt
                or self.stage not in (("idle", "failed", "connected") if self.main else ("idle", "failed"))
                or self.cleanup in ("unknown", "incomplete")):
            finish(None, "busy", "none")
            return
        record = self.records.get(ref)
        if record is None:
            finish(None, "stateChanged", "none")
            return
        self.begin_candidate()
        self.token += 1
        token = self.token
        self.document, self.active_ref = document, ref
        self.expiry = None
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
                    self.saved_access, self._cleanup = "removalIncomplete", "incomplete"
                else:
                    self.records.pop(ref, None)
                    if self.active_ref == ref:
                        self.active_ref = None
                    self.saved_access = "saved" if self.records else "none"
                    self._cleanup = "confirmed"
                    self.startup_selection_saved = _startup.clear_reference(ref)["persisted"]
                self.changed()
                finish(self.action("unknown" if error else "confirmed"),
                       "removalIncomplete" if error else None, "unknown" if error else "confirmed")
            self.work(token, lambda: self.store.delete(record), removed, settle=True)
        self.ui.saved_target(record, True, confirmed)

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
        if (self.main is not None and self.session is None and self.active_session is not None
                and (self.switch_candidate is None or self.switch_candidate["phase"] in ("failed", "cancelled"))):
            reason = self.main.select_target(None, owner_saves=self.owner_saves())
            if reason:
                if finish:
                    finish(None, reason, "none")
                return
            self.session, self.active_session = self.active_session, None
            self.active_target, self.active_unsaved = None, False
            self.switch_candidate = self.candidate_error = None
        self.confirming_target = False
        self.candidate_stop.set()
        if self.switch_candidate is not None:
            self.switch_candidate["phase"] = "failed" if failed else "cancelled"
        self.candidate_target = None
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
        self.document = None
        token = self.token
        session, self.session = self.session, None
        if self.health_finish:
            self.health_finish(None, "stateChanged", "none")
            self.health_finish = None
        self.hold, self.deferred, self.reading = False, [], False
        self.confirmation_document = None
        self.expiry, self.target = None, ""
        self.runtime, self.access = "unknown", "unknown"
        self._cleanup = "unknown"
        if session is not None:
            self.session_cleanups[session] = "unknown"
        self.ui.dismiss()
        self.changed("stopping")
        def stopped(_result: object, error: str | None) -> None:
            self.cleanup_finished(session, error)
            if self.valid(token):
                self._cleanup = "incomplete" if error else "confirmed"
                if self.active_session is not None and self.active_projection is not None:
                    (self.save_intent, self.active_ref, self.saved_access, self.stage, self.runtime, self.access,
                     self.observation, self.target) = self.active_projection
                    self.active_projection = None
                    self.changed()
                else:
                    self.changed("failed" if failed or error else "idle")
                if failed or error:
                    self.ui.error(failed or "outcomeUnknown")
            if finish:
                finish(self.action("unknown" if error else "confirmed"),
                       "outcomeUnknown" if error else None, "unknown" if error else "confirmed")
        self.work(token, session.stop if session else lambda: None, stopped, settle=True)

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
        session = self.session or self.active_session
        if session is None:
            self.reading, self.health_finish = False, None
            finish(None, "stateChanged", "none")
            return
        def observed(_result: object, error: str | None) -> None:
            self.reading = False
            self.health_finish = None
            self.access = {"revoked": "revokedConfirmed", "accessUnavailable": "accessDenied"}.get(error, "unknown") if error else "allowed"
            if error == "revoked" and self.active_ref:
                self.startup_selection_saved = _startup.clear_reference(self.active_ref)["persisted"]
            self.observation = "unavailable" if error else "known"
            if error:
                self.runtime = "offline" if error == "daemonUnavailable" else "unknown"
                if session is self.active_session:
                    # Transport loss preserves target identity. Seal native
                    # authority as before; do not silently switch to local.
                    self.active_target.reset()
                    with self.active_target._state:
                        self.active_target.state = "unreachable"
                else:
                    self.session = None
                self.token += 1
                cleanup_token = self.token
                self.changed("stopping")
                self.session_cleanups[session] = "unknown"
                def stopped(_r: object, cleanup_error: str | None) -> None:
                    self.cleanup_finished(session, cleanup_error)
                    if self.valid(cleanup_token):
                        self._cleanup = "incomplete" if cleanup_error else "confirmed"
                        self.changed("failed")
                self.work(cleanup_token, session.stop, stopped, settle=True)
            else:
                self.changed()
            finish(dict(access=self.access, runtime=self.runtime, route="unknown", observedAt=self.observed,
                        observation=self.observation), None, "none")
        self.work(token, session.health, observed)
