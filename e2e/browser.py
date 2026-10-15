"""Built Chromium UI observations, with no response routing or API mocks."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import greenlet
from pyee import EventEmitter
from playwright._impl._errors import Error
from playwright._impl._sync_base import mapping
from playwright._impl._waiter import Waiter
from playwright.sync_api import expect, sync_playwright

from oracles import require

if TYPE_CHECKING:
    from controller import Controller


class _ResponseObservation:
    """Own one real Waiter, Request set and optional original response body."""
    def __init__(self, browser: Browser, path: str, mode: str, document: str,
                 capture_json: bool, tree: bool) -> None:
        self.browser, self.path, self.mode = browser, path, mode
        self.target, self.start_url = browser.base + document, browser._url
        self.lineage = browser._lineage if mode == "action" else None
        self.reached = self.start_url == self.target if mode == "action" else False
        self.navigation: Any = None
        self.commit: int | None = None
        self.requests: dict[Any, int] = {}
        self.response: Any = None
        self.waiter: Any = None
        self.tasks: list[Any] = []
        self.listeners: list[tuple[Any, str, Any]] = []
        self.aborted = False
        self.invalid = False
        self._json: Any = None
        self.captured = False
        self.trace = dict(event="response-observation", path=path, mode=mode, document=document,
                          tree=tree, capture_json=capture_json, deadline=browser._group_end,
                          validation="partial/unvalidated", steps=[])
        self.tree = tree
        browser.events.append(self.trace)

    def note(self, event: str, **metadata: Any) -> None:
        self.trace["steps"].append(dict(event=event, sequence=self.browser._sequence,
                                        at=time.monotonic(), **metadata))

    def current(self) -> bool:
        b = self.browser
        return (not self.aborted and not self.invalid and not b.poisoned and self.reached
                and self.lineage is not None and b._lineage is self.lineage
                and b._url == self.target and b.page.url == self.target
                and self.lineage.redirected_from is None and self.lineage.redirected_to is None)

    @property
    def value(self) -> Any:
        require(self.current() and self.response is not None, "current original native response")
        return mapping.from_impl(self.response)

    @property
    def captured_json(self) -> Any:
        require(self.captured and self.current() and time.monotonic() < self.trace["deadline"],
                "response JSON exposed in its original document/bound")
        return self._json

    def arm(self) -> None:
        page = self.browser.page._impl_obj
        # Pinned Playwright1.58.0 Page._expect_event, with an explicit owner.
        self.waiter = Waiter(page, "page.expect_event(response)")
        self.waiter.reject_on_timeout(self.browser._left() * 1000, "response absolute deadline")
        self.tasks.extend(self.waiter._pending_tasks)
        self.waiter.reject_on_event(page, "crash", Error("Page crashed"))
        self.waiter.reject_on_event(page, "close", lambda: page._close_error_with_reason())
        self.waiter.wait_for_event(page, "response", lambda response: response is self.response)
        self.listeners.extend(self.waiter._registered_listeners)
        self.note("arm")

    def shorten(self) -> None:
        self.trace["deadline"] = self.browser._group_end
        if self.waiter is None or self.waiter.result().done():
            return
        for task in self.waiter._pending_tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        self.waiter.reject_on_timeout(self.browser._left() * 1000, "response group absolute deadline")
        self.tasks = list(self.waiter._pending_tasks)
        self.trace["deadline"] = self.browser._group_end

    def detach(self, abort: bool = False) -> None:
        self.aborted |= abort
        if self.waiter is None:
            return
        def local(stage: str, action: Any) -> None:
            try:
                action()
            except BaseException as exc:
                self.browser.controller.record_error(stage, exc)
                self.browser.poisoned = True
        self.tasks = list(self.waiter._pending_tasks)
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                local("response-timer-cancel", task.cancel)
        # Pinned local emitter removal: no ChannelOwner unsubscribe/send on
        # poison. Each listener is attempted independently, once if present.
        self.listeners = list(self.waiter._registered_listeners)
        for emitter, event, listener in self.listeners:
            local("response-listener-detach", lambda emitter=emitter, event=event, listener=listener:
                  self.browser._detach(emitter, event, listener))
        result = self.waiter.result()
        if abort and not result.done():
            local("response-result-abort", lambda: result.set_exception(Error("response scope aborted")))
        if result.done() and not result.cancelled():
            local("response-result-consume", result.exception)
        self.note("local-cleanup", aborted=self.aborted,
                  tasks=[dict(done=t.done(), cancelled=t.cancelled()) for t in self.tasks])


class Browser:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.events: list[dict] = []
        self.poisoned = False
        self._scopes: list[_ResponseObservation] = []
        self._group_end = float("inf")
        self._bridge_active = False
        self._stop_attempted = False
        self._stop_success = False
        self._tracker_listeners: list[tuple[Any, str, Any]] = []
        self._sequence = 0
        self._lineage: Any = None
        self._navigation: Any = None
        self._url = "about:blank"
        self.base = f"http://127.0.0.1:{controller.port}"
        controller.remaining()
        self.manager = sync_playwright()
        self.playwright = self.manager.start()
        controller.remaining()
        self.browser = self.playwright.chromium.launch(timeout=controller.remaining(30)*1000)
        controller.remaining()
        from importlib.metadata import version
        require(version("playwright") == "1.58.0", "pinned native adapter version")
        controller.durable.append(dict(playwright_version=version("playwright"), chromium_version=self.browser.version))
        self.context = self.browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        controller.remaining()
        self.page = self.context.new_page()
        controller.remaining()
        self._loop = self.page._impl_obj._loop
        self._dispatcher = self.page._impl_obj._dispatcher_fiber
        self._connection = self.manager._connection
        # Raw native main-frame payload retains newDocument.request, unlike
        # public framenavigated(Frame). Install before the first navigation.
        for emitter, event, listener in (
            (self.page._impl_obj, "request", self._request),
            (self.page._impl_obj, "response", self._response),
            (self.page.main_frame._impl_obj._channel, "navigated", self._navigated),
            (self.page._impl_obj, "close", self._closed),
            (self.page._impl_obj, "crash", self._closed),
            (self.page._impl_obj, "pageerror", self._page_error),
        ):
            emitter.on(event, listener)
            self._tracker_listeners.append((emitter, event, listener))
        self.set_page_timeout(15)

    @staticmethod
    def _detach(emitter: Any, event: str, listener: Any) -> None:
        if listener in emitter._events.get(event, {}):
            EventEmitter._remove_listener(emitter, event, listener)
        require(listener not in emitter._events.get(event, {}), "owned listener detached locally")

    def _page_error(self, error: Any) -> None:
        self.events.append(dict(event="pageerror", message=str(error)))

    def _closed(self, *_args: Any) -> None:
        if self._scopes:
            self.controller.record_error("native-page-close/crash", Error("page closed/crashed during observation"))
            for scope in self._scopes:
                scope.invalid = True
            # The already-owned native Waiter reject path completes while this
            # healthy dispatcher is running. The caller then poisons locally.

    def poison(self, error: BaseException) -> None:
        self.controller.record_error("native-observation", error)
        if not self.poisoned:
            self.events.append(dict(event="native-unavailable", reason=type(error).__name__, at=time.monotonic()))
        self.poisoned = True
        for scope in reversed(self._scopes):
            scope.detach(abort=True)
        for emitter, event, listener in self._tracker_listeners:
            try:
                self._detach(emitter, event, listener)
            except BaseException as exc:
                self.controller.record_error("native-tracker-detach", exc)

    def _request(self, request: Any) -> None:
        try:
            self._sequence += 1
            main = request.frame is self.page.main_frame._impl_obj
            navigation = main and request.is_navigation_request()
            if navigation:
                self._navigation = request
            for scope in self._scopes:
                if navigation:
                    valid = (scope.mode == "new-document" and scope.navigation is None
                             and scope.commit is None and request.method == "GET"
                             and request.url == scope.target and request.redirected_from is None)
                    if valid:
                        scope.navigation = request
                    else:
                        scope.invalid = True
                    scope.note("navigation-request", expected=valid, main_frame=True, method=request.method)
                parsed, base = urlsplit(request.url), urlsplit(self.base)
                if (parsed.scheme, parsed.netloc, parsed.path) != (base.scheme, base.netloc, scope.path):
                    continue
                eligible = (main and request.method == "GET" and scope.current()
                            and request.redirected_from is None and request.redirected_to is None
                            and (not scope.tree or parse_qs(parsed.query, keep_blank_values=True).get("tree") == ["true"]))
                if eligible:
                    if scope.requests:
                        scope.invalid = True
                        scope.note("ambiguous-request")
                    scope.requests[request] = self._sequence
                scope.note("request-start", main_frame=main, method=request.method, eligible=eligible,
                           commit=scope.commit)
        except BaseException as exc:
            self.poison(exc)

    def _navigated(self, payload: Any) -> None:
        try:
            self._sequence += 1
            require(isinstance(payload, dict) and set(payload) <= {"url", "name", "newDocument", "error"}
                    and isinstance(payload.get("url"), str) and "error" not in payload,
                    "unambiguous pinned main-frame navigation payload")
            url = payload["url"]
            new = "newDocument" in payload
            if new:
                document = payload["newDocument"]
                require(isinstance(document, dict) and set(document) == {"request"}, "native navigation Request lineage")
                request = document["request"]._object
                require(request is self._navigation and request.frame is self.page.main_frame._impl_obj
                        and request.method == "GET" and request.is_navigation_request()
                        and request.url == url and request.redirected_from is None and request.redirected_to is None,
                        "unredirected main-frame document commit")
                self._lineage = request
                self._navigation = None
            self._url = url
            for scope in self._scopes:
                if new:
                    valid = (scope.mode == "new-document" and scope.commit is None
                             and scope.navigation is self._lineage and url == scope.target and not scope.invalid)
                    if valid:
                        scope.lineage, scope.commit, scope.reached = self._lineage, self._sequence, True
                    else:
                        scope.invalid = True
                else:
                    valid = self._lineage is not None and not scope.invalid
                    if scope.mode == "new-document":
                        valid &= scope.commit is not None and url == scope.target
                    elif scope.reached:
                        valid &= url == scope.target
                    else:
                        valid &= url in {scope.start_url, scope.target}
                        if valid and url == scope.target:
                            scope.reached = True
                    scope.invalid |= not valid
                scope.note("commit" if new else "same-document", expected=valid)
            self.events.append(dict(event="native-navigation", new_document=new,
                                    sequence=self._sequence, path=urlsplit(url).path))
        except BaseException as exc:
            self._lineage = None
            self.poison(exc)

    def _response(self, response: Any) -> None:
        try:
            self._sequence += 1
            parsed, base = urlsplit(response.url), urlsplit(self.base)
            # Preserve dashboard503 and other actual responses separately.
            self.events.append(dict(event="response", path=parsed.path, status=response.status))
            for scope in self._scopes:
                if (parsed.scheme, parsed.netloc, parsed.path) != (base.scheme, base.netloc, scope.path):
                    continue
                request = response.request
                eligible = (request in scope.requests and scope.current() and request.method == "GET"
                            and request.frame is self.page.main_frame._impl_obj and response.status == 200
                            and request.redirected_from is None and request.redirected_to is None)
                scope.note("selected" if eligible else "ineligible-response", method=request.method,
                           status=response.status, request_sequence=scope.requests.get(request), commit=scope.commit)
                if eligible:
                    if scope.response is not None:
                        scope.invalid = True
                        scope.note("ambiguous-response")
                    else:
                        scope.response = response
        except BaseException as exc:
            self.poison(exc)

    def _left(self) -> float:
        end = min(self._group_end, self.controller.effective_end())
        left = end - time.monotonic()
        require(left > 0, "native absolute deadline exhausted")
        return left

    def _healthy(self) -> None:
        require(not self.poisoned and not self._loop.is_closed() and not self._dispatcher.dead
                and self._connection._error is None, "native dispatcher available")

    def _drive(self, coroutine: Any, end: float) -> Any:
        """One scoped caller wakeup, on the original loop/dispatcher only."""
        task = None
        active = False
        caller = greenlet.getcurrent()
        def wake(_task: Any) -> None:
            if active and not caller.dead:
                caller.switch()
        try:
            self._healthy()
            require(not self._bridge_active, "one native bridge owner")
            task = self._loop.create_task(coroutine)
            active = self._bridge_active = True
            task.add_done_callback(wake)
            with self.controller.phase(end, native=True):
                while not task.done():
                    self._healthy()
                    self._dispatcher.switch()
                    if self._dispatcher.dead and not task.done():
                        raise Error("dispatcher ended before owned task")
                asyncio._set_running_loop(self._loop)
                result = task.result()
                self._healthy()
                return result
        except BaseException as exc:
            if task is None:
                coroutine.close()
            self.poison(exc)
            raise
        finally:
            active = self._bridge_active = False
            if task is not None:
                self.controller._attempt("bridge-wakeup-detach", lambda: task.remove_done_callback(wake), blocking=False)
                if not task.done() and not task.cancelling():
                    self.controller._attempt("bridge-task-cancel", task.cancel, blocking=False)
                # Never gather indefinitely or re-enter here.

    def _native(self, factory: Any, end: float) -> Any:
        async def owned() -> Any:
            operation = self._loop.create_task(factory())
            try:
                done, _ = await asyncio.wait({operation}, timeout=max(0, end-time.monotonic()))
                if not done:
                    raise TimeoutError("native operation absolute deadline")
                return operation.result()
            finally:
                if not operation.done():
                    operation.cancel()
                self.events.append(dict(event="native-task", done=operation.done(), cancelled=operation.cancelled(),
                                        callbacks_pending=len(self._connection._callbacks)))
        return self._drive(owned(), end)

    def _settle(self, scope: _ResponseObservation) -> None:
        pending = {task for task in scope.tasks if not task.done()}
        if pending:
            async def settle() -> None:
                _, unsettled = await asyncio.wait(pending, timeout=max(0, self._group_end-time.monotonic()))
                require(not unsettled, "owned Waiter timer cancellation settled")
            self._drive(settle(), self._group_end)
        for task in scope.tasks:
            if task.done() and not task.cancelled():
                require(task.exception() is None, "native Waiter timer failure")

    def set_page_timeout(self, cap: float) -> None:
        # Preserve the actual page default, including an already clipped cap,
        # for later callers sharing this page (Pause and retained task views).
        self.page_timeout = self.controller.remaining(cap)
        self.page.set_default_timeout(self.page_timeout * 1000)
        self.controller.remaining()

    @contextmanager
    def response(self, path: str, *, mode: str = "action", document: str | None = None,
                 capture_json: bool = False, tree: bool = False) -> Any:
        c = self.controller
        end = min(c.effective_end(), time.monotonic()+self.page_timeout, self._group_end)
        outer = not self._scopes
        self._group_end = end
        c.observation_end = end
        observation = None
        try:
            with c.phase(end):
                self._healthy()
                require(mode in {"action", "new-document"} and isinstance(document, str)
                        and document.startswith("/orgs/") and urlsplit(document).path == document
                        and not urlsplit(document).query and not urlsplit(document).fragment,
                        "literal caller-declared document destination for both modes")
                require(path.startswith("/api/v1/orgs/") and urlsplit(path).path == path
                        and not urlsplit(path).query and not urlsplit(path).fragment, "literal API observation path")
                observation = _ResponseObservation(self, path, mode, document, capture_json, tree)
                if self._scopes:
                    parent = self._scopes[-1]
                    require((parent.target, parent.mode, parent.start_url, parent.lineage) ==
                            (observation.target, mode, observation.start_url, observation.lineage),
                            "nested observations share one document transition")
                if mode == "action":
                    source, base = urlsplit(self._url), urlsplit(self.base)
                    require(self._lineage is not None and self._url == self.page.url
                            and (source.scheme, source.netloc) == (base.scheme, base.netloc)
                            and source.path.startswith("/orgs/") and not source.query and not source.fragment,
                            "action starts from authenticated current-origin native document lineage")
                for scope in self._scopes:
                    scope.shorten()
                self._scopes.append(observation)
                observation.arm()
            with c.phase(end, native=True):
                yield observation  # Only the audited finite native action sites.
            async def selected() -> Any:
                result = observation.waiter.result()
                done, _ = await asyncio.wait({result}, timeout=max(0, self._group_end-time.monotonic()))
                if not done:
                    raise TimeoutError("response absolute deadline")
                return result.result()
            native = self._drive(selected(), self._group_end)
            with c.phase(self._group_end):
                require(native is observation.response and observation.current(), "selected current Request/Response lineage")
            if capture_json:
                observation.note("body-begin")
                # Original selected Response channel, one RPC, no inline decode
                # in dispatcher and no alternative body or response selection.
                binary = self._native(lambda: native._channel.send("body", None), self._group_end)
                with c.phase(self._group_end):
                    require(observation.current(), "body completed in selected current document")
                    raw = base64.b64decode(binary, validate=True)
                    body = json.loads(raw.decode("utf-8"))
                    observation.trace["body"] = json.loads(c.sanitize(json.dumps(body)))
                    require(observation.current(), "parsed body retains selected document/destination")
                    observation._json, observation.captured = body, True
                    observation.note("body-end")
            with c.phase(self._group_end):
                require(observation.current(), "response final document/destination")
                observation.detach()
            require(not self.poisoned, "local native cleanup completed")
            self._settle(observation)
            with c.phase(self._group_end):
                require(observation.current(), "response current after timer settlement")
                observation.trace["validation"] = "validated"
        except BaseException as exc:
            if observation is not None:
                observation.note("observation-error", error=type(exc).__name__)
            self.poison(exc)
            raise
        finally:
            if observation is not None and observation in self._scopes:
                observation.detach(abort=self.poisoned)
                self._scopes.remove(observation)
            if outer:
                self._group_end = float("inf")
                if hasattr(c, "observation_end"):
                    del c.observation_end
                c._timer_sync()

    def open_task(self, org: str, task: str, brief: str, *, switch: bool = False,
                  cached_recall: bool = False, alien: str | None = None) -> None:
        roots = f"/api/v1/orgs/{org}/tasks/roots"
        with self.response(roots, mode="action" if switch else "new-document",
                           document=f"/orgs/{org}/tasks"):
            if switch:
                self.page.get_by_role("combobox", name="Active org", exact=True).click(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
                self.page.get_by_role("option", name=org, exact=True).click(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
            else:
                self.page.goto(f"{self.base}/orgs/{org}/tasks", timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        link = main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').first
        expect(link).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        detail_path = f"/api/v1/orgs/{org}/tasks/{task}"
        recall_path = detail_path + "/recall"
        with ExitStack() as responses:
            if cached_recall:
                # The explicit round-trip return may reuse the real response
                # previously observed in this context. Rendering is asserted by
                # completed(), before a separate fresh-HTTP revisit below.
                for path in (detail_path, recall_path):
                    require(any(row.get("event") == "response" and row.get("path") == path
                                and row.get("status") == 200 for row in self.events),
                            "cached detail/Recall has target-org real HTTP provenance", path)
                self.events.append(dict(event="navigation", action="cached-return", org=org, task=task))
            else:
                responses.enter_context(self.response(detail_path, mode="action", document=f"/orgs/{org}/tasks/{task}"))
                responses.enter_context(self.response(recall_path, mode="action", document=f"/orgs/{org}/tasks/{task}", tree=True))
            link.click(timeout=self.controller.remaining(self.page_timeout)*1000)
            self.controller.remaining()
        expect(main.get_by_text(brief, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()

    def revisit_after_cache_expiry(self, org: str, task: str) -> None:
        # AppProvider's shipping staleTime is 30s. Keep this same populated
        # context and navigate normally; never evict cache or change its clock.
        require(self.controller.remaining(32) >= 31, "cache revisit fits action budget")
        begin = time.monotonic()
        main = self.page.get_by_role("main")
        main.locator(f'a[href="/orgs/{org}/tasks"]').first.click(timeout=self.controller.remaining(self.page_timeout)*1000)
        self.controller.remaining()
        self.page.wait_for_timeout(31000)
        self.controller.remaining()
        self.events.append(dict(event="navigation", action="cache-expired-revisit", org=org, task=task))
        with (
            self.response(f"/api/v1/orgs/{org}/tasks/{task}", mode="action", document=f"/orgs/{org}/tasks/{task}"),
            self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall", mode="action", document=f"/orgs/{org}/tasks/{task}", tree=True),
        ):
            main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').first.click(timeout=self.controller.remaining(self.page_timeout)*1000)
            self.controller.remaining()
        self.controller.phases.append(dict(name="browser-cache-revisit", seconds=time.monotonic() - begin))

    def completed(self, org: str, task: str, brief: str, summary: str, *, child: str | None = None,
                  child_summary: str | None = None, alien: str | None = None, shot: str,
                  refresh: bool = False) -> None:
        if refresh:
            # Task/Recall queries do not poll while mounted. A normal user
            # refresh observes final state through the real browser transport;
            # this makes no claim about automatic live-view invalidation.
            require(self.page.url == f"{self.base}/orgs/{org}/tasks/{task}", "refresh owns expected task")
            self.events.append(dict(event="navigation", action="reload", org=org, task=task))
            with (
                self.response(f"/api/v1/orgs/{org}/tasks/{task}", mode="new-document", document=f"/orgs/{org}/tasks/{task}"),
                self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall", mode="new-document", document=f"/orgs/{org}/tasks/{task}", tree=True),
            ):
                self.page.reload(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(main.get_by_text(summary, exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(main.get_by_text("completed", exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        # Recall heading and its containing section own their own assertions.
        recall_heading = main.get_by_role("heading", name="Recall tree", exact=True)
        expect(recall_heading).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        recall = recall_heading.locator("..")
        expect(recall.get_by_text("Outcome", exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(recall.get_by_text(summary, exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(recall.locator(f'a[href="/orgs/{org}/tasks/{task}"]')).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        if child:
            expect(recall.locator(f'a[href="/orgs/{org}/tasks/{child}"]')).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(recall.get_by_text(child_summary, exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(recall.get_by_text("completed", exact=True)).to_have_count(2, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        self.page.screenshot(path=str(self.controller.root / "raw" / f"{shot}.png"), full_page=True, timeout=self.controller.remaining(self.page_timeout)*1000)
        self.controller.remaining()
        require(not any(row["event"] == "pageerror" for row in self.events), "built-browser JavaScript error", self.events)

    def job_open(self, job: str, status: str) -> dict:
        self.set_page_timeout(10)
        path = f"/api/v1/orgs/alpha/jobs/{job}"
        with self.response(path, mode="new-document", document=f"/orgs/alpha/jobs/{job}", capture_json=True) as response:
            self.page.goto(f"{self.base}/orgs/alpha/jobs/{job}", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        body = response.captured_json
        require(body["id"] == job and body["status"] == status, "fresh browser job state", body)
        expect(self.page.get_by_role("main").get_by_text(status, exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        return body

    def job_action(self, job: str, cap: int, pending_check: Any, *, reject: bool = False) -> dict:
        self.job_open(job, "pending")
        action = "Reject" if reject else "Approve & run"
        self.page.get_by_role("main").get_by_role("button", name=action, exact=True).click(timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        dialog = self.page.get_by_role("dialog")
        expect(dialog).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(dialog.get_by_text(action + " " + job, exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        if reject:
            dialog.get_by_label("Reason", exact=True).fill("E04_REJECTED_BY_REVIEW", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        else:
            expect(dialog.get_by_label("Timeout (seconds)", exact=True)).to_have_value(str(cap), timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(dialog.get_by_label("Working directory override", exact=True)).to_have_value("", timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        pending_check()  # Still no launch with the real confirmation open.
        path = f"/api/v1/orgs/alpha/jobs/{job}/" + ("reject" if reject else "run")
        with self.page.expect_response(lambda r: r.url == self.base + path and r.request.method == "POST", timeout=self.controller.remaining(10)*1000) as received:
            dialog.get_by_role("button", name=action, exact=True).click(timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        self.controller.remaining()
        response = received.value
        request = response.request.post_data_json
        expected = {"reason": "E04_REJECTED_BY_REVIEW"} if reject else {}
        record = dict(path=path, request=request, status=response.status, response=response.json(), at=time.monotonic())
        self.events.append(dict(event="job-action", **record))
        require(request == expected and response.status == (200 if reject else 202), "actual browser job action", record)
        return record

    def job_terminal(self, job: str, status: str, exit_code: int | None, out: str, err: str) -> dict:
        body = self.job_open(job, status)
        main = self.page.get_by_role("main")
        if status == "rejected":
            expect(main.get_by_text("E04_REJECTED_BY_REVIEW", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(main.get_by_role("heading", name="Output", exact=True)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        else:
            label = main.get_by_text("Exit code", exact=True)
            expect(label).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(label.locator("..").get_by_text(str(exit_code), exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            for stream, content in (("stdout", out), ("stderr", err)):
                heading = main.get_by_role("heading", name=stream, exact=True)
                expect(heading.locator("..").locator("pre")).to_have_text(content, timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
            if status == "failed":
                expect(main.get_by_text("timeout", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
        expect(main.get_by_role("button", name="Approve & run", exact=True)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        expect(main.get_by_role("button", name="Reject", exact=True)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        self.page.screenshot(path=str(self.controller.root / "raw" / f"e04-{job}.png"), full_page=True, timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        result = dict(body=body, text=main.inner_text(timeout=self.controller.remaining(10)*1000), screenshot=f"e04-{job}.png", at=time.monotonic())
        self.controller.remaining()
        return result

    def jobs_waiting(self, task: str, ids: list[str]) -> dict:
        path = f"/api/v1/orgs/alpha/tasks/{task}"
        with (
            self.response(path, mode="new-document", document=f"/orgs/alpha/tasks/{task}", capture_json=True) as response,
            self.response(path + "/recall", mode="new-document", document=f"/orgs/alpha/tasks/{task}", tree=True),
        ):
            self.page.goto(f"{self.base}/orgs/alpha/tasks/{task}", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        main = self.page.get_by_role("main")
        expect(main.get_by_text("waiting on jobs", exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        for job in ids:
            expect(main.get_by_text(job, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        result = dict(body=response.captured_json, text=main.inner_text(timeout=self.controller.remaining(10)*1000), at=time.monotonic())
        self.controller.remaining()
        return result

    def open_threads_context(self, port: int) -> None:
        self.thread_base = f"http://127.0.0.1:{port}"
        self.thread_events: list[dict] = []
        self.thread_context = self.browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        self.controller.remaining()
        self.thread_page = self.thread_context.new_page()
        self.controller.remaining()
        page = self.thread_page
        page.set_default_timeout(self.controller.remaining(10) * 1000)
        def request(req):
            if req.url.startswith(self.thread_base + "/"):
                path = req.url[len(self.thread_base):]
                if path.split("?")[0] == "/api/v1/orgs/alpha/threads" and req.method == "POST":
                    raw = req.post_data_buffer or b""
                    self.thread_events.append(dict(event="compose-request", url=req.url, method=req.method,
                                                   sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                                                   payload=req.post_data_json, at=time.monotonic()))
            else:
                self.thread_events.append(dict(event="origin-bypass", at=time.monotonic()))
        def response(res):
            if res.request.method == "POST" and res.url == self.thread_base + "/api/v1/orgs/alpha/threads":
                record = dict(event="compose-response", url=res.url, status=res.status, at=time.monotonic())
                self.thread_events.append(record)
                self.controller.remaining()
                raw = res.body()
                self.controller.remaining()
                record.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), body=res.json())
            elif res.request.method == "GET" and re.fullmatch(re.escape(self.thread_base) + r"/api/v1/orgs/alpha/threads/THR-\d+(?:/messages)?", res.url.split("?")[0]):
                record = dict(event="thread-response", url=res.url, method=res.request.method, status=res.status, at=time.monotonic())
                self.thread_events.append(record)
                self.controller.remaining()
                record["body"] = res.json()
                self.controller.remaining()
        page.on("request", request)
        page.on("response", response)
        page.on("framenavigated", lambda frame: self.thread_events.append(dict(event="navigation", url=frame.url, at=time.monotonic()))
                if frame == page.main_frame else None)
        page.on("pageerror", lambda _error: self.thread_events.append(dict(event="pageerror", at=time.monotonic())))
        page.goto(self.thread_base + "/orgs/alpha/threads", timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()

    def thread_compose_inputs(self) -> None:
        page = self.thread_page
        page.get_by_role("button", name="New thread", exact=True).click(timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Subject", exact=True).fill("E05 compose race", timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        dialog.get_by_label("Recipients (comma-separated agent names)", exact=True).fill("case_manager, case_worker", timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        dialog.get_by_label("Body (Markdown)", exact=True).fill("E05 opening\nliteral beta", timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        expect(dialog.get_by_role("button", name="Send", exact=True)).to_be_enabled(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()

    def thread_event_batch(self) -> dict:
        # Passive DOM observers only: no React internals, state changes, forced
        # enabled properties, fetch replacement or request interception.
        result = self.thread_page.get_by_role("dialog").evaluate("""dialog => {
          const body = dialog.querySelector('textarea');
          const send = [...dialog.querySelectorAll('button')].find(b => b.textContent.trim() === 'Send');
          const rows = []; let id = 0;
          const snap = (phase, event) => rows.push({id, phase, at: performance.now(), type:event.type,
            defaultPrevented:event.defaultPrevented, bodyDisabled:body.disabled, sendDisabled:send.disabled,
            bodyConnected:body.isConnected, sendConnected:send.isConnected, nonempty:!!body.value.trim(),
            shift:!!event.shiftKey, composing:!!event.isComposing,
            popup:[...document.querySelectorAll('[role=listbox]')].some(el => el.getClientRects().length > 0),
            target:event.target === body ? 'body' : event.target === send ? 'send' : null, keyCode:event.keyCode || 0});
          const capture = e => { if (e.target === body || e.target === send) snap('capture',e); };
          const bubble = e => { if (e.target === body || e.target === send) snap('bubble',e); };
          document.addEventListener('keydown',capture,true); document.addEventListener('click',capture,true);
          document.addEventListener('keydown',bubble); document.addEventListener('click',bubble);
          try {
            for (const [target,event] of [[body,new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true,cancelable:true,shiftKey:false,isComposing:false})],
              [send,new MouseEvent('click',{bubbles:true,cancelable:true})],[send,new MouseEvent('click',{bubbles:true,cancelable:true})]]) {
              ++id; snap('before',event); const dispatched = target.dispatchEvent(event); snap('after',event);
              rows[rows.length-1].dispatched = dispatched;
            }
          } finally {
            document.removeEventListener('keydown',capture,true); document.removeEventListener('click',capture,true);
            document.removeEventListener('keydown',bubble); document.removeEventListener('click',bubble);
          }
          return rows;
        }""", timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        record = dict(event="event-batch", rows=result, at=time.monotonic(), guard_sensitivity="UNPROVEN")
        self.thread_events.append(record)
        first = result[0]
        require(first["nonempty"] and first["bodyConnected"] and first["sendConnected"]
                and not any(first[k] for k in ("bodyDisabled", "sendDisabled", "popup", "shift", "composing")),
                "initial genuine compose eligibility", first)
        return record

    def thread_snapshot(self, name: str) -> list[dict]:
        self.thread_events.append(dict(event="snapshot", name=name, at=time.monotonic()))
        return list(self.thread_events)

    def thread_created(self, thread: str) -> dict:
        page = self.thread_page
        expect(page.get_by_role("dialog")).to_have_count(0, timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        created_url = page.url
        self.thread_events.append(dict(event="deliberate-revisit", url=self.thread_base+"/orgs/alpha/threads", at=time.monotonic()))
        with page.expect_response(lambda r: r.url.split("?")[0] == self.thread_base+"/api/v1/orgs/alpha/threads" and r.status == 200, timeout=self.controller.remaining(10)*1000):
            page.goto(self.thread_base+"/orgs/alpha/threads", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        self.controller.remaining()
        link = page.get_by_role("main").locator(f'a[href="/orgs/alpha/threads/{thread}"]')
        expect(link).to_have_count(1, timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        record = dict(event="compose-created", thread=thread, dialog_count=page.get_by_role("dialog").count(),
                      list_rows=link.count(), url=created_url, list_url=page.url, at=time.monotonic())
        self.thread_events.append(record)
        return record

    def thread_view(self, thread: str, subject: str, bodies: list[str], label: str, task: str | None = None) -> dict:
        page = self.thread_page
        page.set_default_timeout(self.controller.remaining(10) * 1000)
        url = self.thread_base + f"/orgs/alpha/threads/{thread}"
        path = self.thread_base + f"/api/v1/orgs/alpha/threads/{thread}"
        self.thread_events.append(dict(event="deliberate-revisit", url=url, at=time.monotonic()))
        with page.expect_response(lambda r: r.url.split("?")[0] == path and r.status == 200, timeout=self.controller.remaining(10)*1000) as received, \
                page.expect_response(lambda r: r.url.split("?")[0] == path + "/messages" and r.status == 200, timeout=self.controller.remaining(10)*1000) as messages:
            page.goto(url, timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        self.controller.remaining()
        main = page.get_by_role("main")
        expect(main.get_by_text(subject, exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        for body in bodies:
            expect(main.get_by_text(body, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        if task:
            expect(main.locator(f'a[href="/orgs/alpha/tasks/{task}"]').first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(main.get_by_text("completed", exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(main.get_by_text(f"dispatched {task}", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(main.get_by_text(f"task {task} completed · E05 task complete", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        name = f"e05-{label}.png"
        page.screenshot(path=str(self.controller.root / "raw" / name), full_page=True, timeout=self.controller.remaining(10)*1000)
        self.controller.remaining()
        result = dict(thread=thread, body=received.value.json(), text=main.inner_text(timeout=self.controller.remaining(10)*1000), screenshot=name,
                      url=page.url, response_url=received.value.url, method=received.value.request.method,
                      status=received.value.status, messages_body=messages.value.json(), messages_url=messages.value.url,
                      messages_status=messages.value.status, messages_method=messages.value.request.method,
                      rendered_messages=main.locator("article").evaluate_all("""articles => articles.map(article => ({
                        header: article.querySelector('header')?.innerText,
                        body: article.querySelector('.gl-prose')?.innerText,
                        visible: article.getClientRects().length > 0
                      }))"""), at=time.monotonic())
        self.controller.remaining()
        self.thread_events.append(dict(event="thread-view", **result))
        return result

    def close_threads_context(self) -> None:
        if hasattr(self, "thread_context"):
            try:
                self._healthy()
                self._native(self.thread_context._impl_obj.close, self.controller.effective_end())
                self.thread_events.append(dict(event="context-closed", at=time.monotonic()))
                del self.thread_context
            finally:
                self.controller._atomic_json(self.controller.root / "raw/e05-browser-events.json", list(self.thread_events))


    def e07_detail(self, task: str, status: str, shot: str | None = None) -> dict:
        c = self.controller
        self.set_page_timeout(4)
        self.page.set_default_navigation_timeout(c.remaining(4) * 1000)
        path = f"/api/v1/orgs/alpha/tasks/{task}"
        url = self.base + f"/orgs/alpha/tasks/{task}"
        self.page.goto(url, timeout=self.controller.remaining(4)*1000)
        self.controller.remaining()
        # Real reload and real responses even if a previous React cache exists.
        with (
            self.response(path, mode="new-document", document=f"/orgs/alpha/tasks/{task}", capture_json=True) as detail,
            self.response(path + "/recall", mode="new-document", document=f"/orgs/alpha/tasks/{task}", capture_json=True, tree=True) as recall,
        ):
            self.page.reload(timeout=c.remaining(4)*1000)
            self.controller.remaining()
        main = self.page.get_by_role("main")
        expect(main.locator("h1").get_by_text(status, exact=False).first).to_be_visible(timeout=c.remaining(4)*1000)
        self.controller.remaining()
        cancel = main.get_by_role("button", name="Cancel", exact=True)
        if status in {"cancelled", "completed"}:
            expect(cancel).to_be_disabled(timeout=c.remaining(4)*1000)
            self.controller.remaining()
            expect(cancel).to_have_attribute("title", f"Cannot cancel a {status} task", timeout=c.remaining(4)*1000)
            self.controller.remaining()
        else:
            expect(cancel).to_be_enabled(timeout=c.remaining(4)*1000)
            self.controller.remaining()
        record = dict(task=task, url=self.page.url, detail_url=detail.value.url, detail=detail.captured_json,
                      detail_method=detail.value.request.method, detail_status=detail.value.status,
                      recall_url=recall.value.url, recall=recall.captured_json, recall_status=recall.value.status,
                      recall_method=recall.value.request.method, text=main.inner_text(timeout=self.controller.remaining(4)*1000),
                      cancel_disabled=cancel.is_disabled(timeout=self.controller.remaining(4)*1000), cancel_title=cancel.get_attribute("title", timeout=self.controller.remaining(4)*1000),
                      badges=main.get_by_text(status,exact=True).all_text_contents(), at=time.monotonic())
        self.controller.remaining()
        if shot:
            self.page.screenshot(path=str(c.root / "raw" / shot),full_page=True,timeout=c.remaining(4)*1000)
            self.controller.remaining()
            record["screenshot"] = shot
        c.e07.setdefault("browser_events",[]).append(dict(event="detail-reload",**record))
        require(record["detail"]["task"]["task_id"] == task and record["detail"]["task"]["status"] == status,
                "E07 current actual browser response",record)
        return record

    def e07_cancel(self, parent: str, child: str) -> dict:
        c = self.controller
        child_view = self.e07_detail(child,"in_progress")
        parent_view = self.e07_detail(parent,"in_progress")
        require("waiting on subtasks" in parent_view["text"] and child_view["detail"]["task"]["block_kind"] is None,
                "E07 waiting parent and running child rendered")
        self.page.get_by_role("main").get_by_role("button",name="Cancel",exact=True).click(timeout=c.remaining(4)*1000)
        self.controller.remaining()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_role("heading",name="Cancel task",exact=True)).to_be_visible(timeout=c.remaining(4)*1000)
        self.controller.remaining()
        dialog.get_by_placeholder("Reason for cancellation (optional)",exact=True).fill("E07_CANCEL_TREE",timeout=c.remaining(4)*1000)
        self.controller.remaining()
        path = f"/api/v1/orgs/alpha/tasks/{parent}/cancel"
        requests = []
        def capture(request):
            if request.method == "POST" and request.url == self.base + path:
                requests.append(dict(method=request.method,url=request.url,body=request.post_data_json,at=time.monotonic()))
        self.page.on("request",capture)
        rationale = dialog.get_by_role("textbox").input_value(timeout=self.controller.remaining(4)*1000)
        self.controller.remaining()
        entered = time.monotonic()
        try:
            with self.page.expect_response(lambda r:r.url == self.base+path and r.request.method == "POST",timeout=c.remaining()*1000) as received:
                dialog.get_by_role("button",name="Cancel task",exact=True).click(timeout=c.remaining()*1000)
                self.controller.remaining()
            self.controller.remaining()
            response = received.value
            record = dict(entered=entered,finished=time.monotonic(),path=path,method=response.request.method,
                          request=response.request.post_data_json,status=response.status,response=response.json(),
                          requests=requests,parent_view=parent_view,child_view=child_view,rationale=rationale)
            c.e07.setdefault("browser_events",[]).append(dict(event="cancel",**record))
            require(len(requests) == 1 and record["request"] == {"rationale":"E07_CANCEL_TREE"}
                    and record["status"] == 200 and record["response"] == {"ok":True,"task_id":parent,"cancelled":[parent,child],"killed":[{"task_id":child,"agent":"case_worker"}]},
                    "E07 literal shipping browser cancellation",record)
            return record
        finally:
            self.page.remove_listener("request",capture)

    def e07_terminal(self, parent: str, child: str) -> list[dict]:
        c = self.controller
        views = [self.e07_detail(parent,"cancelled","e07-parent.png"),self.e07_detail(child,"cancelled","e07-child.png")]
        path = "/api/v1/orgs/alpha/tasks/roots"
        self.page.goto(self.base+"/orgs/alpha/tasks",timeout=c.remaining(4)*1000)
        self.controller.remaining()
        with self.response(path, mode="new-document", document="/orgs/alpha/tasks", capture_json=True) as roots:
            self.page.reload(timeout=c.remaining(4)*1000)
            self.controller.remaining()
        main = self.page.get_by_role("main")
        expect(main.get_by_text("Cancelled",exact=True).first).to_be_visible(timeout=c.remaining(4)*1000)
        self.controller.remaining()
        link = main.locator(f'a[href="/orgs/alpha/tasks/{parent}"]').first
        expect(link).to_be_visible(timeout=c.remaining(4)*1000)
        self.controller.remaining()
        self.page.screenshot(path=str(c.root/"raw/e07-list.png"),full_page=True,timeout=c.remaining(4)*1000)
        self.controller.remaining()
        record = dict(url=self.page.url,roots_url=roots.value.url,roots=roots.captured_json,method=roots.value.request.method,
                      status=roots.value.status,text=main.inner_text(timeout=self.controller.remaining(4)*1000),parent_href=link.get_attribute("href", timeout=self.controller.remaining(4)*1000),
                      screenshot="e07-list.png",at=time.monotonic())
        self.controller.remaining()
        c.e07.setdefault("browser_events",[]).append(dict(event="list-reload",**record))
        views.append(record)
        return views


    def _terminal_stop(self) -> None:
        """ONE exit; sticky expiry leaves ONE successor across loop phases.

        Standard CPython's fixed ready batch preserves the successor across
        run_until_complete boundaries. Nonadvancing synchronous work and a
        broken scheduler remain outside this cooperative bound; Owned is the
        separate process boundary.
        """
        if self._stop_success:
            return
        c, loop, connection = self.controller, self._loop, self._connection
        end = c.effective_end()
        owner: dict = dict(event="native-terminal-stop", deadline=end, attempted=False, active=False,
                           expired=False, enforcement_error=None, exit_error=None, cancel_error=None)
        self.events.append(owner)
        handle = None
        def expire() -> None:
            nonlocal handle
            handle = None  # This callback's handle has been consumed.
            if not owner["active"]:
                return
            owner["expired"] = True
            self.poisoned = True
            try:
                require(not loop.is_closed(), "terminal enforcement loop unavailable")
                loop.stop()
                # stop only sets _stopping. A fixed ready batch cannot consume
                # this successor; it survives into transport/asyncgen phases.
                handle = loop.call_soon(expire)
            except BaseException as exc:
                owner["enforcement_error"] = type(exc).__name__
                c.record_error("terminal-stop-enforcement", exc)
                # No scheduling retry or proven phase-spanning bound after
                # registration failure. Already staged evidence/Owned remain.
        failed = False
        try:
            self._healthy()
            from playwright._impl._transport import PipeTransport
            from playwright.sync_api._context_manager import PlaywrightContextManager
            require(threading.current_thread() is threading.main_thread()
                    and type(self.manager) is PlaywrightContextManager and self.manager._own_loop
                    and self.manager._loop is loop and self.manager._connection is connection
                    and connection._dispatcher_fiber is self._dispatcher
                    and connection._loop is loop and type(connection._transport) is PipeTransport
                    and type(loop) is asyncio.SelectorEventLoop and loop.get_task_factory() is None
                    and not self.manager._exit_was_called and not self._stop_attempted
                    and not self._bridge_active and not self._scopes
                    and asyncio._get_running_loop() in (None, loop), "supported terminal native owner")
            require(end > time.monotonic(), "terminal stop has existing positive work")
            with c.phase(end, native=True):
                owner["active"] = True
                try:
                    handle = loop.call_at(end, expire)
                    self._healthy()
                    require(end > time.monotonic(), "terminal stop expired before entry")
                    self._stop_attempted = owner["attempted"] = True
                    try:
                        self.manager.__exit__()
                        owner["returned"] = time.monotonic()
                    except BaseException as exc:
                        owner["exit_error"] = type(exc).__name__
                        c.record_error("terminal-stop-exit", exc)
                        raise
                finally:
                    # Deactivate before cancellation, including already closed
                    # loops or callbacks copied to a ready batch. No loop drive.
                    owner["active"] = False
                    pending, handle = handle, None
                    if pending is not None:
                        try:
                            pending.cancel()
                        except BaseException as exc:
                            owner["cancel_error"] = type(exc).__name__
                            c.record_error("terminal-stop-cancel", exc)
                if time.monotonic() >= end:
                    owner["expired"] = True
                transport = connection._transport
                stopped = transport._stopped_future
                owner.update(loop_closed=loop.is_closed(), dispatcher_dead=self._dispatcher.dead,
                             transport_done=stopped.done(), transport_cancelled=stopped.cancelled(),
                             driver_returncode=transport._proc.returncode)
                require(not owner["expired"] and not owner["enforcement_error"] and not owner["cancel_error"],
                        "terminal stop expiry/enforcement failure")
                require(loop.is_closed() and self._dispatcher.dead and stopped.done() and not stopped.cancelled()
                        and stopped.exception() is None and transport._proc.returncode is not None,
                        "terminal stop actual native return/transport/loop receipt")
                self._stop_success = True
                owner["state"] = "returned-before-deadline"
        except BaseException as exc:
            failed = True
            owner["failure"] = type(exc).__name__
            owner["state"] = "failed" if owner["attempted"] else "unavailable"
            c.record_error("terminal-stop", exc)
            raise
        finally:
            owner["active"] = False
            if failed:
                self.poisoned = True
            for emitter, event, listener in self._tracker_listeners:
                try:
                    self._detach(emitter, event, listener)
                except BaseException as exc:
                    c.record_error("terminal-tracker-detach", exc)

    def final_observation(self) -> None:
        c = self.controller
        c.stage_browser_trace()  # Complete safe prefix before optional native work.
        self._healthy()
        end = min(c.effective_end(), time.monotonic()+4)
        with c.phase(end, native=True):
            try:
                left = end-time.monotonic()
                require(left > 0, "final screenshot has positive existing time")
                self.page.screenshot(path=str(c.root / "raw/browser-final.png"),
                                     full_page=True, timeout=left*1000)
            except Exception as exc:
                from playwright._impl._errors import TimeoutError as NativeTimeoutError
                if isinstance(exc, (NativeTimeoutError, TimeoutError)):
                    self.poison(exc)
                else:
                    self._healthy()  # Ordinary returned screenshot errors may still close.
                raise
            except BaseException as exc:
                self.poison(exc)
                raise

    def close(self) -> None:
        c = self.controller
        stages = [
            ("browser-final-observation", self.final_observation),
            ("thread-context-close", self.close_threads_context),
            ("browser-context-close", lambda: self._native(self.context._impl_obj.close, c.effective_end())),
            ("browser-close", lambda: self._native(self.browser._impl_obj.close, c.effective_end())),
            ("browser-terminal-stop", self._terminal_stop),
        ]
        for name, action in stages:
            if name == "thread-context-close" and not hasattr(self, "thread_context"):
                continue
            record = dict(event="native-teardown", stage=name, at=time.monotonic())
            self.events.append(record)
            try:
                if c.effective_end() <= time.monotonic() or self.poisoned:
                    record.update(state="unavailable", reason="poisoned" if self.poisoned else "work-exhausted")
                    raise Error("native teardown unavailable")
                action()
                record["state"] = "returned"
            except BaseException as exc:
                record.setdefault("state", "failed")
                record["error"] = type(exc).__name__
                c.record_error(name, exc)
        for emitter, event, listener in self._tracker_listeners:
            try:
                self._detach(emitter, event, listener)
            except BaseException as exc:
                c.record_error("browser-tracker-detach", exc)
