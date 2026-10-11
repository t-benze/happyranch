"""Built Chromium UI observations, with no response routing or API mocks."""
from __future__ import annotations

import json
import hashlib
import time
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from playwright.sync_api import expect, sync_playwright

from oracles import require

if TYPE_CHECKING:
    from controller import Controller


class Browser:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        from importlib.metadata import version
        self.controller.durable.append(dict(playwright_version=version("playwright"), chromium_version=self.browser.version))
        self.context = self.browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.events: list[dict] = []
        # Deliberately never collect headers, bootstrap bodies or storage. This
        # sanitized event trace is inspectable; raw Playwright ZIP/HAR is absent.
        self.page.on("response", lambda response: self.events.append(dict(
            event="response", path=response.url.split(f"http://127.0.0.1:{controller.port}")[-1].split("?")[0],
            status=response.status)))
        self.page.on("pageerror", lambda error: self.events.append(dict(event="pageerror", message=str(error))))
        self.base = f"http://127.0.0.1:{controller.port}"

    def response(self, path: str) -> Any:
        return self.page.expect_response(lambda response: response.url.split("?")[0] == self.base + path and response.status == 200)

    def open_task(self, org: str, task: str, brief: str, *, switch: bool = False,
                  cached_recall: bool = False, alien: str | None = None) -> None:
        roots = f"/api/v1/orgs/{org}/tasks/roots"
        with self.response(roots):
            if switch:
                self.page.get_by_role("combobox", name="Active org", exact=True).click()
                self.page.get_by_role("option", name=org, exact=True).click()
            else:
                self.page.goto(f"{self.base}/orgs/{org}/tasks")
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0)
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0)
        link = main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').first
        expect(link).to_be_visible()
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
                responses.enter_context(self.response(detail_path))
                responses.enter_context(self.response(recall_path))
            link.click()
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()

    def revisit_after_cache_expiry(self, org: str, task: str) -> None:
        # AppProvider's shipping staleTime is 30s. Keep this same populated
        # context and navigate normally; never evict cache or change its clock.
        require(self.controller.remaining(32) >= 31, "cache revisit fits action budget")
        begin = time.monotonic()
        main = self.page.get_by_role("main")
        main.locator(f'a[href="/orgs/{org}/tasks"]').first.click()
        self.page.wait_for_timeout(31000)
        self.controller.remaining()
        self.events.append(dict(event="navigation", action="cache-expired-revisit", org=org, task=task))
        with self.response(f"/api/v1/orgs/{org}/tasks/{task}"), self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall"):
            main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').first.click()
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
            with self.response(f"/api/v1/orgs/{org}/tasks/{task}"), self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall"):
                self.page.reload()
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()
        expect(main.get_by_text(summary, exact=True).first).to_be_visible()
        expect(main.get_by_text("completed", exact=True).first).to_be_visible()
        # Recall heading and its containing section own their own assertions.
        recall_heading = main.get_by_role("heading", name="Recall tree", exact=True)
        expect(recall_heading).to_be_visible()
        recall = recall_heading.locator("..")
        expect(recall.get_by_text("Outcome", exact=True).first).to_be_visible()
        expect(recall.get_by_text(summary, exact=True)).to_be_visible()
        expect(recall.locator(f'a[href="/orgs/{org}/tasks/{task}"]')).to_be_visible()
        if child:
            expect(recall.locator(f'a[href="/orgs/{org}/tasks/{child}"]')).to_be_visible()
            expect(recall.get_by_text(child_summary, exact=True)).to_be_visible()
            expect(recall.get_by_text("completed", exact=True)).to_have_count(2)
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0)
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0)
        self.page.screenshot(path=str(self.controller.root / "raw" / f"{shot}.png"), full_page=True)
        require(not any(row["event"] == "pageerror" for row in self.events), "built-browser JavaScript error", self.events)

    def job_open(self, job: str, status: str) -> dict:
        self.page.set_default_timeout(self.controller.remaining(10) * 1000)
        path = f"/api/v1/orgs/alpha/jobs/{job}"
        with self.response(path) as response:
            self.page.goto(f"{self.base}/orgs/alpha/jobs/{job}")
        body = response.value.json()
        require(body["id"] == job and body["status"] == status, "fresh browser job state", body)
        expect(self.page.get_by_role("main").get_by_text(status, exact=True).first).to_be_visible()
        return body

    def job_action(self, job: str, cap: int, pending_check: Any, *, reject: bool = False) -> dict:
        self.job_open(job, "pending")
        action = "Reject" if reject else "Approve & run"
        self.page.get_by_role("main").get_by_role("button", name=action, exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text(action + " " + job, exact=True)).to_be_visible()
        if reject:
            dialog.get_by_label("Reason", exact=True).fill("E04_REJECTED_BY_REVIEW")
        else:
            expect(dialog.get_by_label("Timeout (seconds)", exact=True)).to_have_value(str(cap))
            expect(dialog.get_by_label("Working directory override", exact=True)).to_have_value("")
        pending_check()  # Still no launch with the real confirmation open.
        path = f"/api/v1/orgs/alpha/jobs/{job}/" + ("reject" if reject else "run")
        with self.page.expect_response(lambda r: r.url == self.base + path and r.request.method == "POST") as received:
            dialog.get_by_role("button", name=action, exact=True).click()
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
            expect(main.get_by_text("E04_REJECTED_BY_REVIEW", exact=True)).to_be_visible()
            expect(main.get_by_role("heading", name="Output", exact=True)).to_have_count(0)
        else:
            label = main.get_by_text("Exit code", exact=True)
            expect(label).to_be_visible()
            expect(label.locator("..").get_by_text(str(exit_code), exact=True)).to_be_visible()
            for stream, content in (("stdout", out), ("stderr", err)):
                heading = main.get_by_role("heading", name=stream, exact=True)
                expect(heading.locator("..").locator("pre")).to_have_text(content)
            if status == "failed":
                expect(main.get_by_text("timeout", exact=True)).to_be_visible()
        expect(main.get_by_role("button", name="Approve & run", exact=True)).to_have_count(0)
        expect(main.get_by_role("button", name="Reject", exact=True)).to_have_count(0)
        self.page.screenshot(path=str(self.controller.root / "raw" / f"e04-{job}.png"), full_page=True)
        return dict(body=body, text=main.inner_text(), screenshot=f"e04-{job}.png", at=time.monotonic())

    def jobs_waiting(self, task: str, ids: list[str]) -> dict:
        path = f"/api/v1/orgs/alpha/tasks/{task}"
        with self.response(path) as response, self.response(path + "/recall"):
            self.page.goto(f"{self.base}/orgs/alpha/tasks/{task}")
        main = self.page.get_by_role("main")
        expect(main.get_by_text("waiting on jobs", exact=False).first).to_be_visible()
        for job in ids:
            expect(main.get_by_text(job, exact=False).first).to_be_visible()
        return dict(body=response.value.json(), text=main.inner_text(), at=time.monotonic())

    def open_threads_context(self, port: int) -> None:
        self.thread_base = f"http://127.0.0.1:{port}"
        self.thread_events: list[dict] = []
        self.thread_context = self.browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        self.thread_page = self.thread_context.new_page()
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
                raw = res.body()
                self.thread_events.append(dict(event="compose-response", url=res.url, status=res.status,
                                               sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                                               body=res.json(), at=time.monotonic()))
        page.on("request", request)
        page.on("response", response)
        page.on("framenavigated", lambda frame: self.thread_events.append(dict(event="navigation", url=frame.url, at=time.monotonic()))
                if frame == page.main_frame else None)
        page.on("pageerror", lambda _error: self.thread_events.append(dict(event="pageerror", at=time.monotonic())))
        page.goto(self.thread_base + "/orgs/alpha/threads")

    def thread_compose_inputs(self) -> None:
        page = self.thread_page
        page.get_by_role("button", name="New thread", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Subject", exact=True).fill("E05 compose race")
        dialog.get_by_label("Recipients (comma-separated agent names)", exact=True).fill("case_manager, case_worker")
        dialog.get_by_label("Body (Markdown)", exact=True).fill("E05 opening\nliteral beta")
        expect(dialog.get_by_role("button", name="Send", exact=True)).to_be_enabled()

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
        }""")
        record = dict(event="event-batch", rows=result, at=time.monotonic(), guard_sensitivity="UNPROVEN")
        self.thread_events.append(record)
        first = result[0]
        require(first["nonempty"] and first["bodyConnected"] and first["sendConnected"]
                and not any(first[k] for k in ("bodyDisabled", "sendDisabled", "popup", "shift", "composing")),
                "initial genuine compose eligibility", first)
        return record

    def thread_created(self, thread: str) -> dict:
        page = self.thread_page
        expect(page.get_by_role("dialog")).to_have_count(0)
        created_url = page.url
        self.thread_events.append(dict(event="deliberate-revisit", url=self.thread_base+"/orgs/alpha/threads", at=time.monotonic()))
        with page.expect_response(lambda r: r.url.split("?")[0] == self.thread_base+"/api/v1/orgs/alpha/threads" and r.status == 200):
            page.goto(self.thread_base+"/orgs/alpha/threads")
        link = page.get_by_role("main").locator(f'a[href="/orgs/alpha/threads/{thread}"]')
        expect(link).to_have_count(1)
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
        with page.expect_response(lambda r: r.url.split("?")[0] == path and r.status == 200) as received:
            page.goto(url)
        main = page.get_by_role("main")
        expect(main.get_by_text(subject, exact=True).first).to_be_visible()
        for body in bodies:
            expect(main.get_by_text(body, exact=False).first).to_be_visible()
        if task:
            expect(main.locator(f'a[href="/orgs/alpha/tasks/{task}"]').first).to_be_visible()
            expect(main.get_by_text("completed", exact=False).first).to_be_visible()
            expect(main.get_by_text(f"dispatched {task}", exact=True)).to_be_visible()
            expect(main.get_by_text(f"task {task} completed · E05 task complete", exact=True)).to_be_visible()
        name = f"e05-{label}.png"
        page.screenshot(path=str(self.controller.root / "raw" / name), full_page=True)
        result = dict(thread=thread, body=received.value.json(), text=main.inner_text(), screenshot=name, at=time.monotonic())
        self.thread_events.append(dict(event="thread-view", **result))
        return result

    def close_threads_context(self) -> None:
        if hasattr(self, "thread_context"):
            try:
                self.thread_context.close()
                self.thread_events.append(dict(event="context-closed", at=time.monotonic()))
            finally:
                (self.controller.root / "raw/e05-browser-events.json").write_text(
                    self.controller.sanitize(json.dumps(self.thread_events, indent=2)))

    def close(self) -> None:
        try:
            (self.controller.root / "raw" / "browser-events.json").write_text(self.controller.sanitize(json.dumps(self.events, indent=2)))
            self.page.screenshot(path=str(self.controller.root / "raw" / "browser-final.png"), full_page=True)
            self.context.close()
            self.browser.close()
        finally:
            self.playwright.stop()
