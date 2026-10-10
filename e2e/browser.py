"""Built Chromium UI observations, with no response routing or API mocks."""
from __future__ import annotations

import json
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

    def open_task(self, org: str, task: str, brief: str, *, switch: bool = False) -> None:
        roots = f"/api/v1/orgs/{org}/tasks/roots"
        with self.response(roots):
            if switch:
                self.page.get_by_role("combobox", name="Active org", exact=True).click()
                self.page.get_by_role("option", name=org, exact=True).click()
            else:
                self.page.goto(f"{self.base}/orgs/{org}/tasks")
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()
        link = main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').first
        expect(link).to_be_visible()
        with self.response(f"/api/v1/orgs/{org}/tasks/{task}"), self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall"):
            link.click()
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()

    def completed(self, org: str, task: str, brief: str, summary: str, *, child: str | None = None,
                  child_summary: str | None = None, alien: str | None = None, shot: str) -> None:
        main = self.page.get_by_role("main")
        expect(main.get_by_text(brief, exact=False).first).to_be_visible()
        expect(main.get_by_text(summary, exact=True).first).to_be_visible()
        expect(main.get_by_text("Completed", exact=True).first).to_be_visible()
        # Recall heading and its containing section own their own assertions.
        recall_heading = main.get_by_role("heading", name="Recall", exact=True)
        expect(recall_heading).to_be_visible()
        recall = recall_heading.locator("..")
        expect(recall.get_by_text("Outcome", exact=True).first).to_be_visible()
        expect(recall.get_by_text(summary, exact=True)).to_be_visible()
        expect(recall.locator(f'a[href="/orgs/{org}/tasks/{task}"]')).to_be_visible()
        if child:
            expect(recall.locator(f'a[href="/orgs/{org}/tasks/{child}"]')).to_be_visible()
            expect(recall.get_by_text(child_summary, exact=True)).to_be_visible()
            expect(recall.get_by_text("Completed", exact=True)).to_have_count(2)
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0)
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0)
        self.page.screenshot(path=str(self.controller.root / "raw" / f"{shot}.png"), full_page=True)
        require(not any(row["event"] == "pageerror" for row in self.events), "built-browser JavaScript error", self.events)

    def close(self) -> None:
        try:
            (self.controller.root / "raw" / "browser-events.json").write_text(json.dumps(self.events, indent=2))
            self.page.screenshot(path=str(self.controller.root / "raw" / "browser-final.png"), full_page=True)
            self.context.close()
            self.browser.close()
        finally:
            self.playwright.stop()
