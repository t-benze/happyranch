"""Live bilingual Pause readback using the baseline Chromium context."""
from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import expect

from oracles import require

if TYPE_CHECKING:
    from playwright.sync_api import Request

    from controller import Controller


class PauseBrowser:
    def __init__(self, controller: Controller) -> None:
        self.c, self.b = controller, controller.browser

    def locale(self, locale: str) -> None:
        page = self.b.page
        current = page.locator("html").get_attribute("lang", timeout=self.c.remaining(self.b.page_timeout)*1000)
        self.c.remaining()
        require(current in {"en", "zh-CN"}, "shipping browser locale", current)
        if current != locale:
            page.get_by_role("combobox", name="Language" if current == "en" else "语言", exact=True).click(timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
            page.get_by_role("option", name="English" if locale == "en" else "简体中文", exact=True).click(timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
        expect(page.locator("html")).to_have_attribute("lang", locale, timeout=self.c.remaining(5)*1000)
        self.c.remaining()

    def held(self, root: str, child: str, state: str, locale: str) -> dict:
        """Real list/detail/child + reload; no projected-state mocks.

        The Tasks page no longer mounts the standalone pause overview panel
        (founder THR-292 seq41); the CLI/API overview stays in ``pause.overview``.
        """
        page = self.b.page
        self.locale(locale)
        offset = len(self.b.events)
        label = ({"pausing": "Pausing", "paused": "Paused"} if locale == "en"
                 else {"pausing": "暂停中", "paused": "已暂停"})[state]
        resume, pause = ("Resume", "Pause") if locale == "en" else ("恢复", "暂停")
        overview_title = "Task pause overview" if locale == "en" else "任务暂停概览"
        roots_path = "/api/v1/orgs/alpha/tasks/roots"
        overview_path = "/api/v1/orgs/alpha/tasks/pause-overview"
        overview_reads: list[str] = []

        def record_overview(request: Request) -> None:
            if request.url.split("?")[0] == self.b.base + overview_path:
                overview_reads.append(request.url)

        page.on("request", record_overview)
        try:
            with self.b.response(roots_path):
                page.goto(f"{self.b.base}/orgs/alpha/tasks", timeout=self.c.remaining(self.b.page_timeout)*1000)
                self.c.remaining()
            main = page.get_by_role("main")
            root_control = main.locator(f'[data-task-pause="{root}"]')
            expect(root_control.get_by_text(label, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
            self.c.remaining()
            expect(root_control.get_by_role("button", name=resume, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
            self.c.remaining()
            expect(main.get_by_role("region", name=overview_title, exact=True)).to_have_count(0, timeout=self.c.remaining(5)*1000)
            self.c.remaining()
        finally:
            page.remove_listener("request", record_overview)
        require(overview_reads == [], "Tasks page mounts no pause overview read", overview_reads)
        # Detail is reached through the actual list link, then genuinely reloaded.
        path = f"/api/v1/orgs/alpha/tasks/{root}"
        with self.b.response(path), self.b.response(path + "/recall"):
            main.locator(f'a[href="/orgs/alpha/tasks/{root}"]').first.click(timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
        root_control = page.get_by_role("main").locator(f'[data-task-pause="{root}"]')
        expect(root_control.get_by_text(label, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        with self.b.response(path) as reloaded, self.b.response(path + "/recall"):
            page.reload(timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
        p = reloaded.value.json()["pause"]
        require(p["org_slug"] == "alpha" and p["root_task_id"] == root and p["generation"] == 1
                and p["effective_hold"] and p["control_state"] == state, "real reload preserves committed hold", p)
        expect(root_control.get_by_text(label, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(root_control.get_by_role("button", name=resume, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        if state == "pausing":
            expect(root_control.locator("summary")).to_be_visible(timeout=self.c.remaining(5)*1000)
            self.c.remaining()
            root_control.locator("summary").click(timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
            expect(root_control.get_by_text("Task session" if locale == "en" else "任务会话", exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
            self.c.remaining()
            expect(root_control.get_by_text(child, exact=False).first).to_be_visible(timeout=self.c.remaining(5)*1000)
            self.c.remaining()
        else:
            expect(root_control.locator("summary")).to_have_count(0, timeout=self.c.remaining(5)*1000)
            self.c.remaining()
        shot = f"pause-{state}-{locale}"
        page.screenshot(path=str(self.c.root / "raw" / f"{shot}-root.png"), full_page=True, timeout=self.c.remaining(self.b.page_timeout)*1000)
        self.c.remaining()
        child_path = f"/api/v1/orgs/alpha/tasks/{child}"
        with self.b.response(child_path) as child_response, self.b.response(child_path + "/recall"):
            page.goto(f"{self.b.base}/orgs/alpha/tasks/{child}", timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
        data = child_response.value.json()
        require(data["task"]["parent_task_id"] == root and data["pause"]["effective_hold"], "live inherited child transport", data)
        child_control = page.get_by_role("main").locator(f'[data-task-pause="{child}"]')
        expect(child_control.get_by_text(label, exact=True)).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(child_control.get_by_text("Hold inherited from root" if locale == "en" else "继承根任务", exact=False)).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(child_control.locator(f'a[href="/orgs/alpha/tasks/{root}"]')).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(child_control.get_by_role("button", name=pause, exact=True)).to_have_count(0, timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(child_control.get_by_role("button", name=resume, exact=True)).to_have_count(0, timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        page.screenshot(path=str(self.c.root / "raw" / f"{shot}-child.png"), full_page=True, timeout=self.c.remaining(self.b.page_timeout)*1000)
        self.c.remaining()
        require(not any(r["event"] == "pageerror" for r in self.b.events), "Pause browser JavaScript errors", self.b.events)
        return dict(root=root, child=child, locale=locale, state=state, generation=1,
                    viewport=dict(width=1440, height=900), events=self.b.events[offset:], overview_reads=overview_reads,
                    reload=p, child_projection=data["pause"], screenshots=[shot + "-root.png", shot + "-child.png"])

    def terminal(self, root: str) -> None:
        page = self.b.page
        self.locale("en")
        path = f"/api/v1/orgs/alpha/tasks/{root}"
        with self.b.response(path), self.b.response(path + "/recall"):
            page.goto(f"{self.b.base}/orgs/alpha/tasks/{root}", timeout=self.c.remaining(self.b.page_timeout)*1000)
            self.c.remaining()
        main = page.get_by_role("main")
        expect(main.get_by_text("completed", exact=True).first).to_be_visible(timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        controls = main.locator(f'[data-task-pause="{root}"]')
        expect(controls.get_by_role("button", name="Pause", exact=True)).to_have_count(0, timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        expect(controls.get_by_role("button", name="Resume", exact=True)).to_have_count(0, timeout=self.c.remaining(5)*1000)
        self.c.remaining()
        page.screenshot(path=str(self.c.root / "raw/pause-terminal.png"), full_page=True, timeout=self.c.remaining(self.b.page_timeout)*1000)
        self.c.remaining()
