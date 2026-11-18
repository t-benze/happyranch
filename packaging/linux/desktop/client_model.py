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
        self.workers = set()
        self.worker_lock = threading.Lock()

    def snapshot(self) -> dict:
        return dict(stage=self.stage, runtime=self.runtime, savedAccess="notSaved" if self.session else "none",
                    route="unknown", observedAt=self.observed, observation=self.observation,
                    computers=None, activeComputerRef=None,
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

    def work(self, token: int, operation: object, done: object) -> None:
        def worker() -> None:
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc) if isinstance(exc, _native.ClientError) else "unavailable"
            def deliver() -> bool:
                if self.valid(token):
                    if self.hold:
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
        self.expiry, self.cleanup = None, "notApplicable"
        self.observation = "known"
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
                    self.ui.target(invite, lambda consent: self.consent(token, invite, consent))
                except (ValueError, UnicodeError):
                    self.fail("invalidInvite")
            self.work(token, lambda: _native.decode_invite(text), decoded)
        self.ui.paste(pasted)

    def consent(self, token: int, invite: object, consent: bool) -> None:
        if not self.valid(token):
            return
        if not consent:
            self.stop()
            return
        if time.time() >= self.expiry or time.monotonic() >= self.deadline:
            self.fail("expired")
            return
        self.session = _native.ClientSession(self.prefix)
        session = self.session
        self.changed("joining")
        self.work(token, lambda: session.join(invite), lambda _r, e: self.joined(token, e))

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
        self.poll(token)

    def poll(self, token: int) -> bool:
        if not self.valid(token):
            return False
        if self.hold:
            self.defer(lambda: self.poll(token))
        elif time.time() >= self.expiry or time.monotonic() >= self.deadline:
            self.fail("expired")
        else:
            self.work(token, self.session.complete, lambda result, e: self.completed(token, result, e))
        return False

    def completed(self, token: int, result: object, error: str | None) -> None:
        if error:
            self.fail("newInviteRequired")
        elif not result:
            # C23: a denial-shaped reply cannot prove rejection rather than pending.
            self.ui.later(1000, lambda: self.poll(token))
        else:
            self.ui.dismiss_waiting()
            self.changed("opening")
            self.work(token, self.session.open_runtime, lambda url, e: self.opened(token, url, e))

    def opened(self, token: int, url: str | None, error: str | None) -> None:
        if error:
            self.fail("runtimeUnavailable")
            return
        self.ui.open_runtime(url, token)
        self.ui.later(30000, lambda: self.load_timeout(token))

    def load_timeout(self, token: int) -> bool:
        if self.valid(token) and self.stage == "opening":
            self.loaded(token, False)
        return False

    def loaded(self, token: int, success: bool) -> None:
        if not self.valid(token) or self.stage not in ("opening", "connected"):
            return
        if self.hold:
            self.defer(lambda: self.loaded(token, success))
        elif not success:
            self.fail("runtimeUnavailable")
        else:
            self.runtime, self.access = "available", "allowed"
            self.changed("connected")

    def confirm_stop(self, finish: object) -> None:
        token = self.token
        self.confirmation += 1
        confirmation = self.confirmation
        self.hold = True
        def confirmed(yes: bool) -> None:
            if not self.valid(token) or confirmation != self.confirmation:
                finish(None, "stateChanged", "none")
                return
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
        self.token += 1  # Invalidate before stop dispatch, including late approval/load.
        token = self.token
        session, self.session = self.session, None
        if self.health_finish:
            self.health_finish(None, "stateChanged", "none")
            self.health_finish = None
        self.hold, self.deferred, self.reading = False, [], False
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
        if self.document == document:
            self.confirmation += 1
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
