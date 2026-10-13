"""Built Chromium UI observations, with no response routing or API mocks."""
from __future__ import annotations

import json
import hashlib
import re
import time
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

from oracles import require

if TYPE_CHECKING:
    from controller import Controller


class _ResponseObservation:
    """Keep native metadata separate from opt-in, bounded JSON capture."""
    def __init__(self) -> None:
        self.native: Any = None
        self._json: Any = None
        self.captured = False

    @property
    def value(self) -> Any:
        return self.native.value

    @property
    def captured_json(self) -> Any:
        require(self.captured, "response JSON was captured inside its original bound")
        return self._json


class Browser:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.controller.remaining()
        self.playwright = sync_playwright().start()
        self.controller.remaining()
        self.browser = self.playwright.chromium.launch(timeout=self.controller.remaining(30)*1000)
        self.controller.remaining()
        from importlib.metadata import version
        self.controller.durable.append(dict(playwright_version=version("playwright"), chromium_version=self.browser.version))
        self.context = self.browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        self.controller.remaining()
        self.page = self.context.new_page()
        self.controller.remaining()
        self.set_page_timeout(15)
        self.events: list[dict] = []
        # Deliberately never collect headers, bootstrap bodies or storage. This
        # sanitized event trace is inspectable; raw Playwright ZIP/HAR is absent.
        self.page.on("response", lambda response: self.events.append(dict(
            event="response", path=response.url.split(f"http://127.0.0.1:{controller.port}")[-1].split("?")[0],
            status=response.status)))
        self.page.on("pageerror", lambda error: self.events.append(dict(event="pageerror", message=str(error))))
        self.base = f"http://127.0.0.1:{controller.port}"

    def set_page_timeout(self, cap: float) -> None:
        # Preserve the actual page default, including an already clipped cap,
        # for later callers sharing this page (Pause and retained task views).
        self.page_timeout = self.controller.remaining(cap)
        self.page.set_default_timeout(self.page_timeout * 1000)
        self.controller.remaining()

    @contextmanager
    def response(self, path: str, *, mode: str = "action", document: str | None = None,
                 capture_json: bool = False, tree: bool = False) -> Any:
        c, page = self.controller, self.page
        end = min(c.effective_end(), time.monotonic() + self.page_timeout)
        base = urlsplit(self.base)  # Snapshot the current (possibly restarted) port.
        require(mode in {"action", "new-document"} and (document is not None) == (mode == "new-document"),
                "explicit response action/document scope")
        require(path.startswith("/api/v1/orgs/") and urlsplit(path).path == path
                and not urlsplit(path).query and not urlsplit(path).fragment, "literal API observation path")
        target = self.base + document if document is not None else None
        observation = _ResponseObservation()
        trace = dict(event="response-observation", path=path, document=document, mode=mode, tree=tree,
                     capture_json=capture_json, deadline=end, validation="partial/unvalidated", steps=[])
        self.events.append(trace)
        sequence, commit, navigation = 0, None, None
        invalid = False
        requests: dict[Any, int] = {}

        def note(event: str, **metadata: Any) -> None:
            trace["steps"].append(dict(event=event, sequence=sequence, at=time.monotonic(), **metadata))

        def endpoint(url: str) -> bool:
            parsed = urlsplit(url)
            return (parsed.scheme, parsed.netloc, parsed.path) == (base.scheme, base.netloc, path)

        def request_started(request: Any) -> None:
            nonlocal sequence, navigation, invalid
            sequence += 1
            main = request.frame == page.main_frame
            if main and request.is_navigation_request():
                valid = (mode == "new-document" and navigation is None and request.method == "GET"
                         and request.url == target and request.redirected_from is None)
                if valid:
                    navigation = request
                else:
                    invalid = True
                note("navigation-request", main_frame=True, expected=valid, method=request.method)
            if endpoint(request.url):
                # Retain the actual armed Request object, not its URL or an ID
                # guessed from the transport. A response for an older Request
                # can never enter this set retroactively.
                if main and request.method == "GET" and not invalid and (mode == "action" or commit is not None):
                    requests[request] = sequence
                note("request-start", main_frame=main, method=request.method,
                     eligible=request in requests, commit=commit)

        def navigated(frame: Any) -> None:
            nonlocal sequence, commit, invalid
            if frame != page.main_frame:
                return
            sequence += 1
            valid = navigation is not None and commit is None and frame.url == target and not invalid
            if valid:
                commit = sequence
            else:
                invalid = True
            note("commit", main_frame=True, expected=valid)

        def selected(response: Any) -> bool:
            if not endpoint(response.url):
                return False
            request = response.request
            eligible = (not invalid and request in requests and request.method == "GET"
                        and request.frame == page.main_frame and response.status == 200
                        and request.redirected_from is None and request.redirected_to is None
                        and (mode == "action" or commit is not None and requests[request] > commit)
                        and (not tree or parse_qs(urlsplit(request.url).query, keep_blank_values=True).get("tree") == ["true"]))
            note("selected" if eligible else "ineligible-response", method=request.method,
                 status=response.status, main_frame=request.frame == page.main_frame,
                 request_sequence=requests.get(request), commit=commit)
            return eligible

        note("arm")
        listeners = [("request", request_started)]
        if mode == "new-document":
            listeners.append(("framenavigated", navigated))
        registered = []
        failed = False
        try:
            # Native ownership includes its original timeout/close/crash path.
            # All nested scopes, action, body and parsing share the earlier end.
            with c.bounded(end):
                for event, listener in listeners:
                    page.on(event, listener)
                    registered.append((event, listener))
                c.remaining()
                with page.expect_response(selected, timeout=c.remaining(self.page_timeout) * 1000) as native:
                    observation.native = native
                    yield observation
                    c.remaining()
                c.remaining()
                require(not invalid, "response document scope remained current")
                response = observation.value
                if capture_json:
                    note("body-begin")
                    c.remaining()
                    raw = response.body()  # Exactly this selected native body, once.
                    c.remaining()
                    body = json.loads(raw.decode("utf-8"))
                    c.remaining()
                    require(not invalid, "body belongs to the selected current document scope")
                    # Retain only the existing synthetic target body through
                    # the existing sanitation context, before caller assertions.
                    trace["body"] = json.loads(c.sanitize(json.dumps(body)))
                    observation._json, observation.captured = body, True
                    note("body-end")
                c.remaining()
        except BaseException as exc:
            failed = True
            note("body-error" if capture_json and any(s["event"] == "body-begin" for s in trace["steps"]) else "observation-error",
                 error=type(exc).__name__)  # Never export arbitrary exception/query contents.
            raise
        finally:
            cleanup_error = None
            for event, listener in registered:
                try:
                    page.remove_listener(event, listener)
                except BaseException as exc:
                    cleanup_error = exc
                    note("listener-cleanup-error", error=type(exc).__name__)
            if cleanup_error is not None and not failed:
                raise cleanup_error
            # Cancelling native EventInfo on an action exception is not a
            # claim of immediate Waiter teardown: its finite timer/close owns it.

    def open_task(self, org: str, task: str, brief: str, *, switch: bool = False,
                  cached_recall: bool = False, alien: str | None = None) -> None:
        roots = f"/api/v1/orgs/{org}/tasks/roots"
        with self.response(roots, mode="action" if switch else "new-document",
                           document=None if switch else f"/orgs/{org}/tasks"):
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
                responses.enter_context(self.response(detail_path, mode="action"))
                responses.enter_context(self.response(recall_path, mode="action", tree=True))
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
            self.response(f"/api/v1/orgs/{org}/tasks/{task}", mode="action"),
            self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall", mode="action", tree=True),
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
            self.controller.remaining()
            try:
                self.thread_context.close()
                self.controller.remaining()
                self.thread_events.append(dict(event="context-closed", at=time.monotonic()))
            finally:
                (self.controller.root / "raw/e05-browser-events.json").write_text(
                    self.controller.sanitize(json.dumps(self.thread_events, indent=2)))

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


    def final_observation(self) -> None:
        self.controller.remaining()
        (self.controller.root / "raw/browser-events.json").write_text(
            self.controller.sanitize(json.dumps(self.events, indent=2)))
        self.page.screenshot(path=str(self.controller.root / "raw/browser-final.png"),
                             full_page=True, timeout=self.controller.remaining(4)*1000)
        self.controller.remaining()

    def close(self) -> None:
        self.final_observation()
        self.controller.remaining()
        try:
            self.context.close()
            self.controller.remaining()
            self.browser.close()
            self.controller.remaining()
        finally:
            self.playwright.stop()
            self.controller.remaining()
