"""Built Chromium UI observations, with no response routing or API mocks."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import SplitResult, parse_qs, urlsplit

import greenlet
from pyee import EventEmitter
from playwright._impl._errors import Error
from playwright._impl._sync_base import mapping
from playwright.sync_api import expect, sync_playwright

from oracles import require

if TYPE_CHECKING:
    from controller import Controller


# Shared by the native bridge and finite, detached-DOM controls. Ownership is
# resolved here, not by a fixture or by searching descendant text for a match.
_RENDERED_DOM = r"""(main, args) => {
    const {org, task, surface, locale} = args;
    const fail = name => { throw new Error('rendered-owner:' + name); };
    const one = (items, name) => { if (items.length !== 1) fail(name); return items[0]; };
    const direct = el => [...el.children];
    const path = el => {
        if (el === main) return ':scope';
        if (!el || !main.contains(el)) fail('outside-main');
        return path(el.parentElement) + ' > :nth-child(' +
            (direct(el.parentElement).indexOf(el) + 1) + ')';
    };
    const text = el => el.textContent;
    const tokens = el => [...el.childNodes].map(n => n.nodeType === 3 ? n.nodeValue :
        n.nodeType === 1 ? {tag: n.tagName.toLowerCase(), children: tokens(n)} :
        {unsupported: n.nodeType});
    const body = el => {
        if (!el || el.tagName !== 'DIV') fail('body');
        const prose = one(direct(el).filter(c => c.classList.contains('gl-prose')), 'prose');
        const buttons = direct(el).filter(c => c.tagName === 'BUTTON');
        if (direct(el).length !== 1 + buttons.length || el.querySelectorAll('button').length !== buttons.length)
            fail('body-children');
        if ([...el.childNodes].some(n => n.nodeType !== 1 && (n.nodeType !== 3 || n.nodeValue.trim())))
            fail('body-text');
        return {path: path(el), prose: path(prose), blocks: direct(prose).map(path),
            content: tokens(prose), buttons: buttons.map(text), toggles: buttons.map(path)};
    };
    const doc = main.ownerDocument;
    if (one([...doc.querySelectorAll('main:not(main main)')], 'shell') !== main) fail('shell-scope');
    if (!['en', 'zh-CN'].includes(locale) || doc.documentElement.lang !== locale) fail('locale');
    const href = '/orgs/' + org + '/tasks/' + task;
    if (doc.location.pathname !== (surface === 'list' ? '/orgs/' + org + '/tasks' : href)
        || doc.location.search || doc.location.hash) fail('url');
    // TasksPage owns an inner main; detail owns ContentWrap's two DIVs.
    // Keep all returned paths relative to the outer shell on both routes.
    const owner = surface === 'list' ? one(direct(main).filter(c => c.tagName === 'DIV')
        .flatMap(c => direct(c)).filter(c => c.tagName === 'MAIN'), 'list') :
        one(direct(main).filter(c => c.tagName === 'DIV')
            .flatMap(c => direct(c)).filter(c => c.tagName === 'DIV'), 'detail');
    if (surface === 'list') {
        const link = one([...owner.querySelectorAll('a')].filter(a => a.getAttribute('href') === href), 'list-link');
        const title = one(direct(link).flatMap(c => direct(c)).filter(c => c.tagName === 'SPAN'
            && c.classList.contains('text-task-title')), 'list-title');
        return {surface, href: link.getAttribute('href'), path: path(link), titlePath: path(title),
            headline: text(title), title: title.getAttribute('title')};
    }
    const headingText = surface === 'detail' ? (locale === 'en' ? 'Brief' : '简报') :
        (locale === 'en' ? 'Recall tree' : '回溯树');
    const heading = one([...owner.querySelectorAll('h3')].filter(h => text(h) === headingText), 'heading');
    const section = heading.parentElement;
    if (section.tagName !== 'SECTION' || section.firstElementChild !== heading) fail('section');
    const owned = direct(section);
    if (owned.length !== 2 || owned[1].tagName !== 'DIV') fail('section-body');
    if (surface === 'detail') return {surface, heading: path(heading), body: body(owned[1])};
    if (surface !== 'recall') fail('surface');
    const node = el => {
        if (el.tagName !== 'DIV') fail('node');
        const parts = direct(el), header = parts.shift();
        if (!header || header.tagName !== 'DIV') fail('header');
        const fields = direct(header), link = fields.shift(), status = fields.pop();
        if (!link || link.tagName !== 'A' || !status || status.tagName !== 'SPAN'
            || fields.length > 1 || fields.some(f => f.tagName !== 'SPAN')
            || header.querySelectorAll('a').length !== 1) fail('header-fields');
        let brief = null, outcome = null, outcomeLabel = null, outcomeLabelPath = null;
        if (parts[0]?.classList.contains('mt-1')) {
            const wrapper = parts.shift();
            brief = body(one(direct(wrapper), 'brief-body'));
        }
        if (parts[0]?.classList.contains('mt-2')) {
            const wrapper = parts.shift(), pair = direct(wrapper);
            if (pair.length !== 2 || pair[0].tagName !== 'DIV') fail('outcome-body');
            outcomeLabel = text(pair[0]);
            outcomeLabelPath = path(pair[0]);
            outcome = body(pair[1]);
        }
        return {href: link.getAttribute('href'), id: text(link), header: path(header), identityPath: path(link),
            status: text(status), statusPath: path(status), agent: fields.length ? text(fields[0]) : null,
            agentPath: fields.length ? path(fields[0]) : null,
            brief, outcome, outcomeLabel, outcomeLabelPath, children: parts.map(node)};
    };
    return {surface, heading: path(heading), node: node(owned[1]),
        links: [...section.querySelectorAll('a')].map(a => a.getAttribute('href'))};
}"""


def _rendered_shape(raw: str) -> list:
    """Finite audited inputs only; never parse/strip arbitrary Markdown."""
    require(type(raw) is str, 'unsupported-rendered-shape', raw)
    plain = {
        'ALPHA_ROOT', 'LOSS_ROOT', 'ALPHA_COLLISION', 'BETA_COLLISION',
        'ALPHA_ROOT_SENTINEL', 'LOSS_ROOT_SENTINEL', 'ALPHA_CHILD_OUTCOME', 'LOSS_CHILD_OUTCOME',
        'ALPHA_COLLISION_OUTCOME', 'BETA_COLLISION_OUTCOME',
        'PAUSE_RESTART_ROOT', 'PAUSE_RESTART_WORKER', 'PAUSE_RESTART_DELEGATE',
        'PAUSE_RESTART_WORKER_OUTCOME', 'PAUSE_RESTART_DONE', 'PAUSE_RESTART_REVIEW_APPROVED',
        'E04.approve-exit0', 'E04.reject', 'E04.completed-exit7', 'E04.failed-timeout',
        'E04.two-jobs-A-first', 'E04.two-jobs-B-first', 'E04_OK completed exit0',
        'E04_REJECT rejected E04_REJECTED_BY_REVIEW', 'E04_EXIT7 completed exit7; operation nonzero',
        'E04_TIMEOUT failed timeout exit-9', 'E04_PAIR A completed exit0; B completed exit7',
    }
    if raw in plain or raw in {'A' * n for n in (240, 241, 600, 601)} or raw in {
        'VISIBLE_PREFIX ' + 'word ' * n + 'HIDDEN_SUFFIX' for n in (60, 140)
    }:
        return [dict(tag='p', children=[raw])]
    if re.fullmatch(r'Delegated to case_worker \(child=TASK-[0-9]+\)', raw):
        return [dict(tag='p', children=[raw])]
    match = re.fullmatch(
        r'PAUSE_RESTART_REVIEW\n---\n## Prior leg context \(auto-generated by orchestrator\)\n\n'
        r'Prior leg:    (TASK-[0-9]+)  \(agent: case_worker\)\nStatus:       completed\n'
        r'Verdict:      -\nConfidence:   90\nSummary:\n  PAUSE_RESTART_WORKER_OUTCOME\n---', raw)
    require(match is not None, 'unsupported-rendered-shape', raw)
    lines = [f'Prior leg:    {match[1]}  (agent: case_worker)', '\nStatus:       completed',
             '\nVerdict:      -', '\nConfidence:   90', '\nSummary:', '\nPAUSE_RESTART_WORKER_OUTCOME']
    content: list = [lines[0]]
    for line in lines[1:]:
        content.extend([dict(tag='br', children=[]), line])
    return [dict(tag='h2', children=['PAUSE_RESTART_REVIEW']),
            dict(tag='h2', children=['Prior leg context (auto-generated by orchestrator)']),
            dict(tag='h2', children=content)]


def _rendered_body(observed: dict | None, raw: str | None, *, surface: str, kind: str,
                   locale: str, expanded: bool = False) -> None:
    require(locale in {'en', 'zh-CN'} and surface in {'detail', 'recall'}
            and kind in {'brief', 'summary'}, 'rendered-body-contract')
    require(raw is None or type(raw) is str, 'unsupported-rendered-shape', raw)
    if not raw:
        require(observed is None, 'rendered-body-absent', observed)
        return
    shape = _rendered_shape(raw)  # Admit the full input before considering previews.
    require(type(observed) is dict, 'rendered-body-present', observed)
    limit = 600 if surface == 'detail' else 240
    long = len(raw) > limit
    if long and not expanded:
        # Only these independently reviewed ASCII long fixtures are admitted.
        if raw == 'A' * len(raw):
            preview = 'A' * limit + '…'
        else:
            require(raw == 'VISIBLE_PREFIX ' + 'word ' * (140 if surface == 'detail' else 60)
                    + 'HIDDEN_SUFFIX', 'unsupported-rendered-preview')
            preview = 'VISIBLE_PREFIX' + ' word' * (117 if surface == 'detail' else 45) + '…'
        shape = [dict(tag='p', children=[preview])]
    # The renderer inserts only single LF text nodes between top-level blocks.
    content = observed['content']
    expected: list = []
    for block in shape:
        if expected:
            expected.append('\n')
        expected.append(block)
    require(content == expected, 'rendered-body-content', dict(actual=content, expected=expected))
    if not long:
        buttons = []
    elif locale == 'en':
        buttons = [('Show less' if surface == 'detail' else f'Hide {kind}') if expanded
                   else f'Show full {kind} ({len(raw)} chars)']
    else:
        noun = '简报' if kind == 'brief' else '摘要'
        buttons = [('收起' if surface == 'detail' else '收起' + noun) if expanded
                   else f'显示完整{noun}（{len(raw)} 个字符）']
    require(observed['buttons'] == buttons, 'rendered-body-controls',
            dict(actual=observed['buttons'], expected=buttons))


def _rendered_validate(observed: dict, expected: dict) -> None:
    """Pure acceptance seam used unchanged by native and isolated controls."""
    surface, org, task = (expected[k] for k in ('surface', 'org', 'task'))
    locale = expected.get('locale', 'en')
    require(observed['surface'] == surface, 'rendered-surface')
    if surface == 'list':
        require(observed['href'] == f'/orgs/{org}/tasks/{task}'
                and observed['title'] == expected['raw'] and observed['headline'] == expected['headline'],
                'rendered-list-title', observed)
    elif surface == 'detail':
        _rendered_body(observed['body'], expected['raw'], surface=surface, kind='brief', locale=locale,
                       expanded=expected.get('expanded', False))
    else:
        require(surface == 'recall', 'rendered-surface')
        hrefs: list[str] = []
        def visit(node: dict, want: dict) -> None:
            href = f'/orgs/{org}/tasks/{want["task_id"]}'
            hrefs.append(href)
            require(node['href'] == href and node['id'] == want['task_id'], 'rendered-node-identity', node)
            require(node['status'] == want['status'], 'rendered-node-status', node)
            agent = want['assigned_agent']
            # IdentityName retains the canonical ID, optionally following the
            # current name and literal separator. Never guess a display name.
            require(node['agent'] == agent or (type(node['agent']) is str and agent is not None
                    and node['agent'].endswith(' · ' + agent)), 'rendered-node-agent', node)
            for kind, slot, key in (('brief', 'brief', 'brief'), ('summary', 'outcome', 'output_summary')):
                if key in want:
                    _rendered_body(node[slot], want[key], surface='recall', kind=kind, locale=locale,
                                   expanded=want.get(slot + '_expanded', False))
            require(node['outcomeLabel'] == (('Outcome' if locale == 'en' else '结果')
                    if want['output_summary'] else None), 'rendered-outcome-label', node)
            require(len(node['children']) == len(want['children']), 'rendered-node-children', node)
            for child, wanted in zip(node['children'], want['children'], strict=True):
                visit(child, wanted)
        visit(observed['node'], expected['node'])
        require(observed['links'] == hrefs, 'rendered-tree-links', observed['links'])


def _json_image(value: Any) -> str:
    """Type-sensitive JSON equality; arrays and all unknown fields survive."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _strict_json(raw: bytes) -> Any:
    def pairs(items: list) -> dict:
        result = {}
        for key, value in items:
            require(key not in result, "duplicate-json-key")
            result[key] = value
        return result
    def constant(_value: str) -> Any:
        raise Error("nonfinite-json")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError):
        raise Error("invalid-original-json") from None


def _fields(value: Any, strings: str = "", nullable: str = "", integers: str = "",
            booleans: str = "") -> None:
    require(type(value) is dict, "object-shape")
    for key in strings.split():
        require(key in value and type(value[key]) is str, "string-field-shape")
    for key in nullable.split():
        require(key in value and (value[key] is None or type(value[key]) is str), "nullable-field-shape")
    for key in integers.split():
        require(key in value and type(value[key]) is int, "integer-field-shape")
    for key in booleans.split():
        require(key in value and type(value[key]) is bool, "boolean-field-shape")


def _utc_stamp(value: Any) -> None:
    require(type(value) is str and "T" in value, "observation-stamp-shape")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise Error("observation-stamp-invalid") from None
    require(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0), "observation-stamp-utc")


def _pause_copy(value: Any) -> dict:
    _fields(value, "org_slug root_task_id lifecycle_status control_state observed_at",
            "paused_at resumed_at", "generation", "held effective_hold evidence_complete")
    _utc_stamp(value["observed_at"])
    require(type(value.get("blockers")) is list, "pause-blockers-shape")
    copy = deepcopy(value)
    for blocker in copy["blockers"]:
        require(type(blocker) is dict and blocker.get("observed_at") == copy["observed_at"],
                "pause-blocker-stamp")
        del blocker["observed_at"]
    del copy["observed_at"]
    return copy


def _task_copy(value: Any, expected: dict | None = None) -> dict:
    _fields(value, "task_id status team task_type brief created_at updated_at",
            "assigned_agent parent_task_id revisit_of_task_id dispatched_from_thread_id block_kind "
            "blocked_on_job_ids active_chain active_fanout note final_output_dir completed_at cancelled_at "
            "last_heartbeat current_session_id zombie_flagged_at", "orchestration_step_count revision_count")
    require("session_timeout_seconds" in value and (value["session_timeout_seconds"] is None
            or type(value["session_timeout_seconds"]) is int), "task-timeout-shape")
    if expected is not None:
        require(all(key in value and _json_image(value[key]) == _json_image(want)
                    for key, want in expected.items() if key != "child_wait_state"), "scenario-task-core")
    require("child_wait" in value, "child-wait-slot")
    wait = value["child_wait"]
    copy = deepcopy(value)
    if value["status"] == "in_progress" and value["block_kind"] == "delegated":
        require(expected is not None and all(key in expected for key in (
            "task_id", "brief", "status", "parent_task_id", "assigned_agent", "block_kind")),
            "declared-delegated-task-identity")
        _fields(wait, "state observed_at")
        require(wait["state"] in {"waiting_children", "pending_children", "child_results_received",
                                  "continuation_pending", "unavailable"}, "child-wait-state")
        _utc_stamp(wait["observed_at"])
        if expected and "child_wait_state" in expected:
            require(wait["state"] == expected["child_wait_state"], "scenario-child-wait-state")
        del copy["child_wait"]["observed_at"]
    else:
        require(wait is None, "child-wait-nullability")
    return copy


def _recall_shape(value: Any, seen: set | None = None) -> None:
    _fields(value, "task_id brief status created_at",
            "parent_task_id revisit_of_task_id assigned_agent completed_at output_summary output_dir verdict")
    seen = set() if seen is None else seen
    require(value["task_id"] not in seen and type(value.get("children")) is list, "recursive-recall-identity")
    seen.add(value["task_id"])
    for child in value["children"]:
        _recall_shape(child, seen)


def _body_image(body: Any, family: str, expected: dict) -> str:
    """Literal harness predicates, independent scenario E, then narrow comparison copy."""
    require(type(expected) is dict and type(expected.get("task" if family != "J" else "job")) is dict,
            "predeclared-scenario-expectation")
    require(type(body) is dict, "response-object")
    copy = deepcopy(body)
    if family == "J":
        _fields(body, "id task_id agent_name title rationale script_text interpreter status created_at",
                "cwd_hint stdout_head stderr_head stdout_path stderr_path started_at finished_at reviewed_at "
                "reviewed_by reject_reason cwd_resolved reason", booleans="review_required persistent")
        for key in ("exit_code", "duration_ms", "max_runtime_seconds", "max_output_bytes"):
            require(key in body and (body[key] is None or type(body[key]) is int), "job-integer-shape")
        require(all(key in body and _json_image(body[key]) == _json_image(want)
                    for key, want in expected["job"].items()), "scenario-job-core")
    elif family == "C":
        _recall_shape(body)
        def check(node: dict, want: dict) -> None:
            require(all(key in node and _json_image(node[key]) == _json_image(value)
                        for key, value in want.items() if key != "children"), "scenario-recall-core")
            require(len(node["children"]) == len(want["children"]), "scenario-recall-children")
            for child, target in zip(node["children"], want["children"]):
                check(child, target)
        check(body, expected["recall"])
    elif family == "D":
        copy["task"] = _task_copy(body.get("task"), expected["task"])
        for key in ("results", "audit_log", "revisit_chain", "direct_revisits"):
            require(type(body.get(key)) is list, "detail-array-shape")
        require(all(type(x) is str for x in body["revisit_chain"]), "revisit-chain-shape")
        for key in ("predecessor_prior_status", "superseded_by_task_id"):
            require(key in body and (body[key] is None or type(body[key]) is str), "detail-nullable-shape")
        for key in ("blocked_on_jobs", "active_chain", "escalation_reason", "work_status"):
            require(key in body and (body[key] is None or type(body[key]) is
                    (list if key == "blocked_on_jobs" else dict)), "detail-projection-shape")
        if body["blocked_on_jobs"] is not None:
            for job in body["blocked_on_jobs"]:
                _fields(job, "job_id status")
        for key, want in expected.get("detail", {}).items():
            require(key in body and _json_image(body[key]) == _json_image(want), "scenario-detail-core")
        copy["pause"] = _pause_copy(body.get("pause"))
        if "result_ids" in expected:
            require([r["id"] for r in body["results"]] == expected["result_ids"], "scenario-result-identities")
    else:
        require(family == "R" and type(body.get("tasks")) is list and "next_cursor" in body
                and body["next_cursor"] is None, "small-synthetic-roots-page")
        seen = set()
        for index, row in enumerate(body["tasks"]):
            require(type(row) is dict and row.get("task_id") not in seen and row.get("parent_task_id") is None,
                    "unique-root-identity")
            seen.add(row["task_id"])
            target = expected["task"] if row["task_id"] == expected["task"]["task_id"] else None
            copy["tasks"][index] = _task_copy(row, target)
            _fields(row, "severity_rollup")
            if target is not None:
                require(row["severity_rollup"] == expected["task"]["status"], "scenario-root-severity")
            require(type(row.get("direct_revisits")) is list, "root-revisits-shape")
            copy["tasks"][index]["pause"] = _pause_copy(row.get("pause"))
        require(expected["task"]["task_id"] in seen, "scenario-root-present")
    if family in {"D", "R"} and "pause" in expected:
        projection = body["pause"] if family == "D" else next(
            row["pause"] for row in body["tasks"] if row["task_id"] == expected["task"]["task_id"])
        require(all(key in projection and _json_image(projection[key]) == _json_image(want)
                    for key, want in expected["pause"].items()), "scenario-hold-core")
    if family in {"D", "R"} and "blocker_task" in expected:
        require(any(blocker.get("task_id") == expected["blocker_task"]
                    and blocker.get("kind") == "task_session" for blocker in projection["blockers"]),
                "scenario-running-worker-blocker")
    if family == "D" and "detail_snapshot" in expected:
        independent = {key: value for key, value in expected.items() if key != "detail_snapshot"}
        require(_json_image(copy) == _body_image(expected["detail_snapshot"], "D", independent),
                "complete-terminal-detail-snapshot")
    for forbidden in expected.get("forbidden", ()):
        require(forbidden not in _json_image(body), "scenario-alien-absence")
    return _json_image(copy)


class _ObservationGroup:
    """One finite caller checkpoint, one final acceptance owner, one absolute D."""
    def __init__(self, browser: Browser, expected: dict, mode: str, document: str) -> None:
        self.browser = browser
        self.expected = deepcopy(expected)  # Before arm, never supplied by a browser body.
        self.mode, self.document = mode, document
        self.base = browser.base
        self.boot = getattr(browser.controller, "daemon", None)
        self.end = min(browser.controller.effective_end(), time.monotonic()+browser.page_timeout)
        self.scopes: list[_ResponseObservation] = []
        self.state = "OPEN"
        self.failure: BaseException | None = None
        self.changed = asyncio.Event()
        self.tasks: list[Any] = []
        self.final_checks: list[Any] = []
        self.disposals: list[Any] = []

    def fail(self, reason: str) -> None:
        if self.failure is None:
            self.failure = Error(reason)
        self.changed.set()

    def guard(self) -> None:
        # A task can be done before its queued completion callback is delivered.
        for scope in self.scopes:
            for member in scope.requests.values():
                scope.observe_body(member)
        if self.failure is not None:
            raise self.failure
        require(self.browser.base == self.base and getattr(self.browser.controller, "daemon", None) is self.boot
                and time.monotonic() < self.end
                and not self.browser.poisoned, "group-current-bound")
        require(all(scope.current() for scope in self.scopes), "group-document-lineage")

    def checkpoint(self, site: str) -> None:
        self.guard()
        require(self.state == "OPEN" and self.scopes and all(s.requests for s in self.scopes),
                "nonempty-open-cohorts")
        self.state = "FROZEN"
        for scope in self.scopes:
            scope.note("checkpoint", site=site)

    def wait(self, ready: Any) -> None:
        async def wait() -> None:
            while not ready():
                self.guard()
                self.changed.clear()
                await asyncio.wait_for(self.changed.wait(), timeout=max(0, self.end-time.monotonic()))
            self.guard()
        self.browser._drive(wait(), self.end)
        self.guard()

    def finalize(self) -> None:
        require(self.state == "FROZEN", "explicit-caller-checkpoint")
        self.state = "SETTLING"
        for scope in self.scopes:
            for member in scope.requests.values():
                scope.consume(member)
                self.wait(lambda member=member: member["finished"])
                scope.consume(member)
        self.guard()
        self.state = "FINALIZING"
        # All body RPCs and bridge wakeups have settled. This final check,
        # local detach and seal do not yield to the native dispatcher.
        require(all(t.done() and not t.cancelled() and t.exception() is None for t in self.tasks),
                "owned-body-tasks-settled")
        for scope in self.scopes:
            for member in scope.requests.values():
                member["wake_active"] = False
                member["body_task"].remove_done_callback(member["wake"])
        for scope in self.scopes:
            if scope.capture_json:
                scope.trace["body"] = json.loads(self.browser.controller.sanitize(
                    json.dumps(next(iter(scope.requests.values()))["json"])))
        for check in self.final_checks:
            check()
        for dispose in self.disposals:
            dispose()
        self.guard()
        for scope in self.scopes:
            scope.note("local-cleanup", aborted=False, tasks=[])
            scope.trace["validation"] = "validated"
        self.browser._scopes.clear()
        self.state = "FINALIZED"


class _ResponseObservation:
    """Actual Request-indexed originals; scope exit never accepts or detaches."""
    def __init__(self, group: _ObservationGroup, path: str, capture_json: bool,
                 tree: bool, ordinary_roots: bool) -> None:
        browser = group.browser
        self.group, self.browser, self.path = group, browser, path
        self.mode, self.document = group.mode, group.document
        self.target, self.start_url = browser.base + self.document, browser._url
        self.lineage = browser._lineage if self.mode == "action" else None
        self.reached = self.start_url == self.target if self.mode == "action" else False
        self.navigation: Any = None
        self.commit: int | None = None
        self.requests: dict[Any, dict] = {}
        self.invalid = False
        self.aborted = False
        self.capture_json = capture_json
        self.family = "R" if ordinary_roots else "C" if tree else "J" if "/jobs/" in path else "D"
        self.image: str | None = None
        self.trace = dict(event="response-observation", path=path, mode=self.mode, document=self.document,
                          tree=tree, capture_json=capture_json, deadline=group.end,
                          validation="partial/unvalidated", steps=[])
        if ordinary_roots:
            self.trace["query_contract"] = "ordinary-roots-limit50"
        browser.events.append(self.trace)

    def note(self, event: str, **metadata: Any) -> None:
        self.trace["steps"].append(dict(event=event, sequence=self.browser._sequence,
                                       at=time.monotonic(), **metadata))

    def query_matches(self, parsed: SplitResult, request_sequence: int | None) -> bool:
        contract = {"R": {"limit": ["50"]}, "C": {"tree": ["true"]}, "D": {}, "J": {}}[self.family]
        try:
            matches = not parsed.fragment and parse_qs(parsed.query, keep_blank_values=True,
                                                       errors="strict", strict_parsing=True) == contract
        except (ValueError, UnicodeError):
            self.note("query-match", request_sequence=request_sequence, matches=False, reason="invalid-query")
            raise Error("invalid ordinary roots query" if self.family == "R" else "invalid observation query") from None
        self.note("query-match", request_sequence=request_sequence,
                  contract="ordinary-roots-limit50" if self.family == "R" else self.family,
                  matches=matches, reason="invalid-query" if parsed.fragment else
                  "ordinary-root-page" if matches and self.family == "R" else "exact-query" if matches else "other-query")
        return matches

    def current(self) -> bool:
        b = self.browser
        return (not self.aborted and not self.invalid and not b.poisoned and self.reached
                and self.lineage is not None and b._lineage is self.lineage
                and b._url == self.target and b.page.url == self.target
                and self.lineage.redirected_from is None and self.lineage.redirected_to is None)

    def consume(self, member: dict) -> Any:
        self.group.wait(lambda: member["body_task"] is not None and member["body_task"].done())
        if "json" not in member:
            with self.browser.controller.phase(self.group.end):
                task = member["body_task"]
                require(not task.cancelled(), "original-body-cancelled")
                try:
                    raw = base64.b64decode(task.result(), validate=True)
                except (ValueError, UnicodeError):
                    raise Error("invalid-original-body") from None
                body = _strict_json(raw)
                image = _body_image(body, self.family, self.group.expected)
                require(self.image is None or self.image == image, "conflicting-original-bodies")
                self.image = image
                member["json"] = body
                diagnostic = dict(request_sequence=member["sequence"], sha256=hashlib.sha256(raw).hexdigest())
                if self.capture_json:
                    diagnostic["body"] = json.loads(self.browser.controller.sanitize(json.dumps(body)))
                self.trace.setdefault("bodies", []).append(diagnostic)
                self.note("body-end", request_sequence=member["sequence"])
        self.group.guard()
        response = member["response"]
        request = response.request
        parsed, base = urlsplit(response.url), urlsplit(self.browser.base)
        require(request in self.requests and self.requests[request] is member and response.status == 200
                and request.method == "GET" and request.frame is self.browser.page.main_frame._impl_obj
                and request.redirected_from is None and request.redirected_to is None
                and (parsed.scheme, parsed.netloc, parsed.path) == (base.scheme, base.netloc, self.path)
                and self.query_matches(parsed, member["sequence"]), "original-member-recheck")
        return member["json"]

    def provisional_json(self) -> Any:
        require(self.group.state == "OPEN", "open-provisional-loan")
        self.group.wait(lambda: bool(self.requests))
        return self.consume(next(iter(self.requests.values())))

    @property
    def value(self) -> Any:
        self.group.guard()
        require(self.group.state == "FINALIZED", "whole-group-sealed-response")
        return mapping.from_impl(next(iter(self.requests.values()))["response"])

    @property
    def captured_json(self) -> Any:
        self.group.guard()
        require(self.capture_json and self.group.state == "FINALIZED", "whole-group-sealed-json")
        return next(iter(self.requests.values()))["json"]

    def observe_body(self, member: dict, *, notify: bool = True) -> None:
        """Retrieve each original outcome once, including after local abort."""
        task = member["body_task"]
        if task is None or not task.done() or "body_outcome" in member:
            return
        cancelled = task.cancelled()
        error = Error("original-body-cancelled") if cancelled else task.exception()
        member["body_outcome"] = "cancelled" if cancelled else "error" if error is not None else "success"
        self.note("body-settled", request_sequence=member["sequence"], outcome=member["body_outcome"],
                  error=type(error).__name__ if error is not None else None)
        active = notify and not self.aborted
        if error is not None:
            if active:
                if self.group.failure is None:
                    self.group.failure = error
                self.browser.controller.record_error("original-body-primary", self.group.failure)
            # Abort-induced cancellation is local cleanup, not another failure.
            if active or not cancelled:
                self.browser.controller.record_error("original-body-outcome", error)
        if active:
            self.group.changed.set()

    def detach(self, abort: bool = False) -> None:
        self.aborted |= abort
        if abort:
            for member in self.requests.values():
                task = member["body_task"]
                if task is None:
                    continue
                if "wake" in member:
                    member["wake_active"] = False
                    task.remove_done_callback(member["wake"])
                if task.done():
                    self.observe_body(member, notify=False)
                elif not member.get("cleanup_attached"):
                    # Consume eventual outcomes only: no owner wake, acceptance,
                    # dispatcher drive, gather or new time budget after poison.
                    member["cleanup_attached"] = True
                    task.add_done_callback(lambda _task, member=member: self.observe_body(member, notify=False))
                if not task.done() and not task.cancelling():
                    task.cancel()
            self.note("local-cleanup", aborted=True,
                      tasks=[dict(done=t.done(), cancelled=t.cancelled()) for t in self.group.tasks])


class Browser:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.events: list[dict] = []
        self.poisoned = False
        self._scopes: list[_ResponseObservation] = []
        self._group: _ObservationGroup | None = None
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
            (self.page._impl_obj, "requestfinished", self._finished),
            (self.page._impl_obj, "requestfailed", self._failed),
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
                scope.group.fail("page-closed/crashed")
            # The live group latch wakes its owner; the caller then poisons
            # locally without reusing uncertain native transport.

    def poison(self, error: BaseException) -> None:
        incoming = error
        if self._group is not None:
            for scope in self._group.scopes:
                for member in scope.requests.values():
                    scope.observe_body(member)
            if self._group.failure is not None:
                error = self._group.failure
        self.controller.record_error("native-observation", error)
        if incoming is not error:
            self.controller.record_error("native-observation-secondary", incoming)
        if not self.poisoned:
            self.events.append(dict(event="native-unavailable", reason=type(error).__name__, at=time.monotonic()))
        self.poisoned = True
        if self._group is not None and self._group.failure is None:
            self._group.failure = error
            self._group.changed.set()
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
                query_matches = scope.query_matches(parsed, self._sequence)
                eligible = (query_matches and main and request.method == "GET" and scope.current()
                            and request.redirected_from is None and request.redirected_to is None)
                if eligible:
                    if scope.group.state != "OPEN":
                        scope.group.fail("late-request")
                    elif request in scope.requests:
                        scope.group.fail("duplicate-request-start")
                    else:
                        scope.requests[request] = dict(sequence=self._sequence, response=None,
                                                      body_task=None, finished=False)
                        scope.group.changed.set()
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
            self.events.append(dict(event="response", path=parsed.path, status=response.status))
            for scope in self._scopes:
                request = response.request
                if request not in scope.requests:
                    continue  # A prearm/unadmitted object never becomes a member.
                member = scope.requests[request]
                valid = ((parsed.scheme, parsed.netloc, parsed.path) == (base.scheme, base.netloc, scope.path)
                         and scope.query_matches(parsed, member["sequence"]) and scope.current()
                         and request.method == "GET" and request.frame is self.page.main_frame._impl_obj
                         and response.status == 200 and request.redirected_from is None
                         and request.redirected_to is None and member["response"] is None)
                scope.note("selected" if valid else "ineligible-response", status=response.status,
                           method=request.method, request_sequence=member["sequence"])
                if not valid:
                    scope.group.fail("invalid-original-response")
                else:
                    member["response"] = response
                    task = self._loop.create_task(response._channel.send("body", None))
                    member["body_task"] = task
                    scope.group.tasks.append(task)
                    def wake(_task: Any, member: dict = member, scope: _ResponseObservation = scope) -> None:
                        if member["wake_active"]:
                            scope.observe_body(member)
                    member["wake"], member["wake_active"] = wake, True
                    task.add_done_callback(wake)
                    scope.note("body-begin", request_sequence=member["sequence"])
                scope.group.changed.set()
        except BaseException as exc:
            self.poison(exc)

    def _finished(self, request: Any) -> None:
        self._sequence += 1
        for scope in self._scopes:
            if request in scope.requests:
                if request.redirected_from is not None or request.redirected_to is not None:
                    scope.group.fail("redirected-member")
                scope.requests[request]["finished"] = True
                scope.note("request-finished", request_sequence=scope.requests[request]["sequence"])
                scope.group.changed.set()

    def _failed(self, request: Any) -> None:
        self._sequence += 1
        for scope in self._scopes:
            if request in scope.requests:
                scope.group.fail("request-failed")
                scope.note("request-failed", request_sequence=scope.requests[request]["sequence"])

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

    def set_page_timeout(self, cap: float) -> None:
        # Preserve the actual page default, including an already clipped cap,
        # for later callers sharing this page (Pause and retained task views).
        self.page_timeout = self.controller.remaining(cap)
        self.page.set_default_timeout(self.page_timeout * 1000)
        self.controller.remaining()

    @contextmanager
    def observation_group(self, expected: dict, *, mode: str, document: str) -> Any:
        c = self.controller
        require(self._group is None and not self._scopes, "one-outer-observation-owner")
        require(mode in {"action", "new-document"} and document.startswith("/orgs/")
                and urlsplit(document).path == document and not urlsplit(document).query
                and not urlsplit(document).fragment and expected, "literal-predeclared-group")
        self._healthy()
        if mode == "action":
            source, base = urlsplit(self._url), urlsplit(self.base)
            require(self._lineage is not None and self._url == self.page.url
                    and (source.scheme, source.netloc) == (base.scheme, base.netloc)
                    and source.path.startswith("/orgs/") and not source.query and not source.fragment,
                    "action-current-document")
        group = self._group = _ObservationGroup(self, expected, mode, document)
        self._group_end = c.observation_end = group.end
        try:
            with c.phase(group.end, native=True):
                yield group
            group.finalize()
        except BaseException as exc:
            # Reconcile already-done tasks even if the owner unwinds before
            # queued completion callbacks run. An observed first failure wins.
            for scope in group.scopes:
                for member in scope.requests.values():
                    scope.observe_body(member)
            error = group.failure if group.failure is not None else exc
            group.state = "REFUSED"
            for scope in group.scopes:
                scope.note("observation-error", error=type(error).__name__)
            self.poison(error)
            if error is not exc:
                raise error from exc
            raise
        finally:
            if group.state != "FINALIZED":
                for dispose in group.disposals:
                    c._attempt("group-local-detach", dispose, blocking=False)
                self._scopes.clear()
            self._group = None
            self._group_end = float("inf")
            del c.observation_end
            c._timer_sync()

    @contextmanager
    def response(self, path: str, *, capture_json: bool = False, tree: bool = False,
                 ordinary_roots: bool = False) -> Any:
        group = self._group
        require(group is not None and group.state == "OPEN", "registered-outer-group")
        require(path.startswith("/api/v1/orgs/") and urlsplit(path).path == path
                and not urlsplit(path).query and not urlsplit(path).fragment, "literal-api-path")
        require(not any(s.path == path for s in group.scopes), "distinct-endpoint-scope")
        scope = _ResponseObservation(group, path, capture_json, tree, ordinary_roots)
        group.scopes.append(scope)
        self._scopes.append(scope)
        scope.note("arm")
        yield scope  # Inner lexical exit has no acceptance/cleanup authority.

    def _task_shell(self) -> Any:
        return self.page.locator('main:not(main main)')

    def _task_surface(self, surface: str) -> Any:
        require(surface in {'list', 'detail', 'recall'}, 'task-surface')
        return self._task_shell().locator(':scope > div > main' if surface == 'list' else ':scope > div > div')

    def _rendered_once(self, expected: dict) -> dict:
        c, main = self.controller, self._task_shell()
        expect(main).to_have_count(1, timeout=c.remaining(5)*1000)
        c.remaining()
        surface, locale = expected['surface'], expected.get('locale', 'en')
        owner = self._task_surface(surface)
        expect(owner).to_have_count(1, timeout=c.remaining(5)*1000)
        c.remaining()
        url = f"{self.base}/orgs/{expected['org']}/tasks"
        if surface != 'list':
            url += '/' + expected['task']
        expect(self.page).to_have_url(url, timeout=c.remaining(5)*1000)
        c.remaining()
        # main/URL/headings can precede the independent roots/Recall query.
        # Wait on the exact data owner before the one-shot strict snapshot.
        href = f"/orgs/{expected['org']}/tasks/{expected['task']}"
        if surface == 'list':
            row = owner.locator(f'a[href="{href}"]')
            expect(row).to_be_visible(timeout=c.remaining(5)*1000)
            c.remaining()
            expect(row.locator(':scope > div > span.text-task-title')).to_be_visible(timeout=c.remaining(5)*1000)
            c.remaining()
        else:
            label = ({'detail': 'Brief', 'recall': 'Recall tree'} if locale == 'en' else
                     {'detail': '简报', 'recall': '回溯树'})[surface]
            heading = owner.get_by_role('heading', name=label, exact=True)
            expect(heading).to_be_visible(timeout=c.remaining(5)*1000)
            c.remaining()
            section = heading.locator('..')
            if surface == 'detail':
                expect(section.locator(':scope > div > .gl-prose')).to_be_visible(timeout=c.remaining(5)*1000)
                c.remaining()
            else:
                root = section.locator(':scope > div')
                expect(root.locator(f':scope > div:first-child > a[href="{href}"]')).to_be_visible(timeout=c.remaining(5)*1000)
                c.remaining()
                # TaskRecallTree mounts the whole tree from one recallNode;
                # descendants cannot stand in for this root or its own bodies.
                for key, wrapper in (('brief', 'mt-1'), ('output_summary', 'mt-2')):
                    if expected['node'].get(key):
                        expect(root.locator(f':scope > div.{wrapper} > div > .gl-prose')).to_be_visible(timeout=c.remaining(5)*1000)
                        c.remaining()
        observed = main.evaluate(_RENDERED_DOM, {key: expected.get(key, 'en') for key in
                                               ('org', 'task', 'surface', 'locale')},
                                 timeout=c.remaining(5)*1000)
        c.remaining()
        _rendered_validate(observed, expected)
        paths = []
        def body(value: dict | None) -> None:
            if value:
                paths.extend([value['path'], value['prose'], *value['blocks'], *value['toggles']])
        def node(value: dict) -> None:
            paths.extend([value['header'], value['identityPath'], value['statusPath']])
            paths.extend(p for p in (value['agentPath'], value['outcomeLabelPath']) if p)
            body(value['brief'])
            body(value['outcome'])
            for child in value['children']:
                node(child)
        if surface == 'list':
            paths.extend([observed['path'], observed['titlePath']])
        elif surface == 'detail':
            body(observed['body'])
        else:
            node(observed['node'])
        for path in paths:
            expect(main.locator(path)).to_be_visible(timeout=c.remaining(5)*1000)
            c.remaining()
        return observed

    def rendered(self, expected: dict) -> None:
        """Own-body assertions, including full/recollapse for supported long inputs."""
        wanted = deepcopy(expected)
        observed = self._rendered_once(wanted)
        bodies: list[tuple[dict, str, str]] = []
        if wanted['surface'] == 'detail':
            if len(wanted['raw']) > 600:
                bodies.append((wanted, 'expanded', observed['body']['path']))
        elif wanted['surface'] == 'recall':
            def collect(node: dict, want: dict) -> None:
                for key, slot in (('brief', 'brief'), ('output_summary', 'outcome')):
                    if len(want.get(key) or '') > 240:
                        bodies.append((want, slot + '_expanded', node[slot]['path']))
                for child, child_want in zip(node['children'], want['children'], strict=True):
                    collect(child, child_want)
            collect(observed['node'], wanted['node'])
        for want, flag, path in bodies:
            toggle = self._task_shell().locator(path).locator(':scope > button')
            toggle.click(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            want[flag] = True
            self._rendered_once(wanted)
            toggle.click(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            want[flag] = False
            self._rendered_once(wanted)

    def open_task(self, org: str, task: str, brief: str, *, expected: dict, ready: Any = None,
                  switch: bool = False, cached_recall: bool = False, alien: str | None = None) -> None:
        roots = f"/api/v1/orgs/{org}/tasks/roots"
        with (self.observation_group(expected, mode="action" if switch else "new-document",
                                     document=f"/orgs/{org}/tasks") as group,
              self.response(roots, ordinary_roots=True)):
            if switch:
                self.page.get_by_role("combobox", name="Active org", exact=True).click(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
                self.page.get_by_role("option", name=org, exact=True).click(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
            else:
                self.page.goto(f"{self.base}/orgs/{org}/tasks", timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
            main = self._task_surface('list')
            self.rendered(dict(surface='list', org=org, task=task, raw=brief, headline=brief))
            if alien:
                expect(main.get_by_text(alien, exact=False)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
                other = "beta" if org == "alpha" else "alpha"
                expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0, timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
            link = main.locator(f'a[href="/orgs/{org}/tasks/{task}"]')
            expect(link).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            require(self.page.url == self.base+f"/orgs/{org}/tasks", "exact-task-list-url")
            group.checkpoint("1")
        # IDs2/3 deliberately provide DOM evidence only. Preserve the old
        # pair's one absolute bound; cached-return passive provenance is weaker.
        end = min(self.controller.effective_end(), time.monotonic()+self.page_timeout)
        with self.controller.bounded(end), self.controller.phase(end, native=True):
            if cached_recall:
                for path in (f"/api/v1/orgs/{org}/tasks/{task}", f"/api/v1/orgs/{org}/tasks/{task}/recall"):
                    require(any(row.get("event") == "response" and row.get("path") == path
                                and row.get("status") == 200 for row in self.events),
                            "cached detail/Recall has target-org real HTTP provenance", path)
                self.events.append(dict(event="navigation", action="cached-return", org=org, task=task))
            link.click(timeout=self.controller.remaining(self.page_timeout)*1000)
            self.controller.remaining()
            expect(self.page).to_have_url(self.base+f"/orgs/{org}/tasks/{task}", timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            self.rendered(dict(surface='detail', org=org, task=task, raw=brief))
            main = self._task_shell()
            if ready is not None:
                ready()
            else:
                require(expected["task"]["status"] == "in_progress", "terminal-dom-readiness-required")
                expect(main.locator("h1").get_by_text("in_progress", exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
                self.rendered(dict(surface='recall', org=org, task=task, node=dict(task_id=task,
                    brief=brief, status='in_progress', assigned_agent='case_manager',
                    output_summary=None, children=[])))
                for text in expected["absent"]:
                    expect(main.get_by_text(text, exact=False)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
                    self.controller.remaining()

    def revisit_after_cache_expiry(self, org: str, task: str, *, expected: dict, ready: Any) -> None:
        require(self.controller.remaining(32) >= 31, "cache revisit fits action budget")
        begin = time.monotonic()
        main = self._task_shell()
        expect(main).to_have_count(1, timeout=self.controller.remaining(5)*1000)
        self.controller.remaining()
        main.locator(f'a[href="/orgs/{org}/tasks"]').click(timeout=self.controller.remaining(self.page_timeout)*1000)
        self.controller.remaining()
        self.page.wait_for_timeout(31000)
        self.controller.remaining()
        self.events.append(dict(event="navigation", action="cache-expired-revisit", org=org, task=task))
        with (
            self.observation_group(expected, mode="action", document=f"/orgs/{org}/tasks/{task}") as group,
            self.response(f"/api/v1/orgs/{org}/tasks/{task}"),
            self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall", tree=True),
        ):
            main = self._task_surface('list')
            expect(self._task_shell()).to_have_count(1, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(main).to_have_count(1, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            main.locator(f'a[href="/orgs/{org}/tasks/{task}"]').click(timeout=self.controller.remaining(self.page_timeout)*1000)
            self.controller.remaining()
            ready()
            group.checkpoint("4/5")
        self.controller.phases.append(dict(name="browser-cache-revisit", seconds=time.monotonic() - begin))

    def completed(self, org: str, task: str, brief: str, summary: str, *, child: str | None = None,
                  child_summary: str | None = None, alien: str | None = None, shot: str,
                  refresh: bool = False, expected: dict | None = None) -> None:
        if refresh:
            # Task/Recall queries do not poll while mounted. A normal user
            # refresh observes final state through the real browser transport;
            # this makes no claim about automatic live-view invalidation.
            require(self.page.url == f"{self.base}/orgs/{org}/tasks/{task}", "refresh owns expected task")
            self.events.append(dict(event="navigation", action="reload", org=org, task=task))
            require(expected is not None, "completion-reload-expectation")
            with (
                self.observation_group(expected, mode="new-document", document=f"/orgs/{org}/tasks/{task}") as group,
                self.response(f"/api/v1/orgs/{org}/tasks/{task}"),
                self.response(f"/api/v1/orgs/{org}/tasks/{task}/recall", tree=True),
            ):
                self.page.reload(timeout=self.controller.remaining(self.page_timeout)*1000)
                self.controller.remaining()
                self.completed(org, task, brief, summary, child=child, child_summary=child_summary,
                               alien=alien, shot=shot, expected=expected)
                group.checkpoint("6/7")
            return
        require(self.page.url == f"{self.base}/orgs/{org}/tasks/{task}", "exact-terminal-task-url")
        main = self._task_shell()
        self.rendered(dict(surface='detail', org=org, task=task, raw=brief))
        # There is no separate detail Outcome section: preserve the root
        # summary obligation through its own Recall Outcome, never a copy.
        children = [] if child is None else [dict(task_id=child, status='completed',
            assigned_agent='case_worker', output_summary=child_summary, children=[])]
        self.rendered(dict(surface='recall', org=org, task=task, node=dict(task_id=task,
            brief=brief, status='completed', assigned_agent='case_manager',
            output_summary=summary, children=children)))
        if alien:
            expect(main.get_by_text(alien, exact=False)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            other = "beta" if org == "alpha" else "alpha"
            expect(main.locator(f'a[href^="/orgs/{other}/tasks/"]')).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        self.page.screenshot(path=str(self.controller.root / "raw" / f"{shot}.png"), full_page=True, timeout=self.controller.remaining(self.page_timeout)*1000)
        self.controller.remaining()
        require(not any(row["event"] == "pageerror" for row in self.events), "built-browser JavaScript error", self.events)

    def job_open(self, job: str, status: str, *, expected: dict, ready: Any = None) -> dict:
        self.set_page_timeout(10)
        path = f"/api/v1/orgs/alpha/jobs/{job}"
        with (self.observation_group(expected, mode="new-document", document=f"/orgs/alpha/jobs/{job}") as group,
              self.response(path, capture_json=True) as response):
            self.page.goto(f"{self.base}/orgs/alpha/jobs/{job}", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
            require(self.page.url == f"{self.base}/orgs/alpha/jobs/{job}", "exact-job-url")
            expect(self.page.get_by_role("main").get_by_text(status, exact=True).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            for label in ("Approve & run", "Run"):
                expect(self.page.get_by_role("main").get_by_role("button", name=label, exact=True)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
            if ready is not None:
                ready()
            group.checkpoint("8")
        with self.controller.phase(group.end):
            body = response.captured_json
            require(body["id"] == job and body["status"] == status, "fresh browser job state")
            self.events.append(dict(event="job-read", path=urlsplit(response.value.url).path,
                                    method=response.value.request.method, status=response.value.status,
                                    response=body, at=time.monotonic()))
            return body

    def job_action(self, job: str, cap: int, pending_check: Any, *, expected: dict, reject: bool = False) -> dict:
        def ready() -> None:
            if not reject:
                expect(self.page.get_by_role("main").get_by_role("button", name="Stop", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
        body = self.job_open(job, "pending" if reject else "running", expected=expected, ready=ready)
        main = self.page.get_by_role("main")
        for label in ("Approve & run", "Run"):
            expect(main.get_by_role("button", name=label, exact=True)).to_have_count(0, timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
        if not reject:
            expect(main.get_by_role("button", name="Stop", exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            pending_check()  # Genuine already-running script; no manual start request.
            read = next(e for e in reversed(self.events) if e.get("event") == "job-read")
            record = dict(path=read["path"], method=read["method"], request=None,
                          status=read["status"], response=body, no_run_controls=True, at=time.monotonic())
        else:
            main.get_by_role("button", name="Reject", exact=True).click(timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
            dialog = self.page.get_by_role("dialog")
            expect(dialog).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            expect(dialog.get_by_text("Reject " + job, exact=True)).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            dialog.get_by_label("Reason", exact=True).fill("E04_REJECTED_BY_REVIEW", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
            pending_check()  # Historical fixture must still prove no launch/files/audit.
            path = f"/api/v1/orgs/alpha/jobs/{job}/reject"
            with self.page.expect_response(lambda r: r.url == self.base + path and r.request.method == "POST", timeout=self.controller.remaining(10)*1000) as received:
                dialog.get_by_role("button", name="Reject", exact=True).click(timeout=self.controller.remaining(10)*1000)
                self.controller.remaining()
            self.controller.remaining()
            response = received.value
            record = dict(path=path, method="POST", request=response.request.post_data_json,
                          status=response.status, response=response.json(), no_run_controls=True, at=time.monotonic())
            require(record["request"] == {"reason": "E04_REJECTED_BY_REVIEW"} and response.status == 200, "actual browser individual rejection", record)
        self.events.append(dict(event="job-action", **record))
        return record

    def job_terminal(self, job: str, status: str, exit_code: int | None, out: str, err: str, *, expected: dict) -> dict:
        def ready() -> None:
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
            pending["text"] = main.inner_text(timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
        pending: dict = {}
        end = min(self.controller.effective_end(), time.monotonic()+10, time.monotonic()+self.page_timeout)
        with self.controller.bounded(end):
            body = self.job_open(job, status, expected=expected, ready=ready)
            with self.controller.phase(end):
                return dict(body=body, text=pending["text"], screenshot=f"e04-{job}.png", at=time.monotonic())

    def jobs_waiting(self, task: str, ids: list[str], *, expected: dict) -> dict:
        path = f"/api/v1/orgs/alpha/tasks/{task}"
        with (
            self.observation_group(expected, mode="new-document", document=f"/orgs/alpha/tasks/{task}") as group,
            self.response(path, capture_json=True) as response,
            self.response(path + "/recall", tree=True),
        ):
            self.page.goto(f"{self.base}/orgs/alpha/tasks/{task}", timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
            main = self.page.get_by_role("main")
            expect(main.get_by_text("waiting on jobs", exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
            self.controller.remaining()
            for job in ids:
                expect(main.get_by_text(job, exact=False).first).to_be_visible(timeout=self.controller.remaining(5)*1000)
                self.controller.remaining()
            text = main.inner_text(timeout=self.controller.remaining(10)*1000)
            self.controller.remaining()
            group.checkpoint("10/11")
        with self.controller.phase(group.end):
            result = dict(body=response.captured_json, text=text, at=time.monotonic())
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


    def e07_detail(self, task: str, status: str, shot: str | None = None, *, expected: dict) -> dict:
        c = self.controller
        self.set_page_timeout(4)
        self.page.set_default_navigation_timeout(c.remaining(4) * 1000)
        path = f"/api/v1/orgs/alpha/tasks/{task}"
        url = self.base + f"/orgs/alpha/tasks/{task}"
        self.page.goto(url, timeout=self.controller.remaining(4)*1000)
        self.controller.remaining()
        # Real reload and real responses even if a previous React cache exists.
        with (
            self.observation_group(expected, mode="new-document", document=f"/orgs/alpha/tasks/{task}") as group,
            self.response(path, capture_json=True) as detail,
            self.response(path + "/recall", capture_json=True, tree=True) as recall,
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
            record = dict(task=task, url=self.page.url, detail=detail.provisional_json(),
                          recall=recall.provisional_json(), text=main.inner_text(timeout=self.controller.remaining(4)*1000),
                          cancel_disabled=cancel.is_disabled(timeout=self.controller.remaining(4)*1000), cancel_title=cancel.get_attribute("title", timeout=self.controller.remaining(4)*1000),
                          badges=self._native(lambda: main.get_by_text(status,exact=True)._impl_obj.all_text_contents(), group.end), at=time.monotonic())
            self.controller.remaining()
            if shot:
                self.page.screenshot(path=str(c.root / "raw" / shot),full_page=True,timeout=c.remaining(4)*1000)
                self.controller.remaining()
                record["screenshot"] = shot
            require(record["detail"]["task"]["task_id"] == task and record["detail"]["task"]["status"] == status,
                    "E07 current actual browser response")
            group.checkpoint("15/16")
        with c.phase(group.end):
            record.update(detail_url=detail.value.url, detail=detail.captured_json,
                          detail_method=detail.value.request.method, detail_status=detail.value.status,
                          recall_url=recall.value.url, recall=recall.captured_json,
                          recall_method=recall.value.request.method, recall_status=recall.value.status)
            c.e07.setdefault("browser_events",[]).append(dict(event="detail-reload",**record))
            require(record["detail"]["task"]["task_id"] == task and record["detail"]["task"]["status"] == status,
                    "E07 current actual browser response",record)
            return record

    def e07_cancel(self, parent: str, child: str, *, expected: dict) -> dict:
        c = self.controller
        child_view = self.e07_detail(child,"in_progress", expected=expected[child])
        parent_view = self.e07_detail(parent,"in_progress", expected=expected[parent])
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

    def e07_terminal(self, parent: str, child: str, *, expected: dict) -> list[dict]:
        c = self.controller
        views = [self.e07_detail(parent,"cancelled","e07-parent.png", expected=expected[parent]),self.e07_detail(child,"cancelled","e07-child.png", expected=expected[child])]
        path = "/api/v1/orgs/alpha/tasks/roots"
        self.page.goto(self.base+"/orgs/alpha/tasks",timeout=c.remaining(4)*1000)
        self.controller.remaining()
        with (self.observation_group(expected[parent], mode="new-document", document="/orgs/alpha/tasks") as group,
              self.response(path, capture_json=True, ordinary_roots=True) as roots):
            self.page.reload(timeout=c.remaining(4)*1000)
            self.controller.remaining()
            main = self._task_surface('list')
            # Share the original status operation's four-second cap with
            # shell/list readiness; additional owners cannot extend it.
            status_end = time.monotonic() + c.remaining(4)
            expect(self._task_shell()).to_have_count(1, timeout=c.remaining(status_end-time.monotonic())*1000)
            self.controller.remaining()
            expect(main).to_have_count(1, timeout=c.remaining(status_end-time.monotonic())*1000)
            self.controller.remaining()
            require(self.page.url == self.base+"/orgs/alpha/tasks", "exact-task-list-url")
            link = main.locator(f'a[href="/orgs/alpha/tasks/{parent}"]')
            expect(link.locator(':scope > div:first-child').get_by_text("cancelled",exact=True)).to_be_visible(timeout=c.remaining(status_end-time.monotonic())*1000)
            self.controller.remaining()
            expect(link).to_be_visible(timeout=c.remaining(4)*1000)
            self.controller.remaining()
            self.page.screenshot(path=str(c.root/"raw/e07-list.png"),full_page=True,timeout=c.remaining(4)*1000)
            self.controller.remaining()
            record = dict(url=self.page.url, text=main.inner_text(timeout=self.controller.remaining(4)*1000),parent_href=link.get_attribute("href", timeout=self.controller.remaining(4)*1000),
                          screenshot="e07-list.png",at=time.monotonic())
            self.controller.remaining()
            group.checkpoint("18")
        with c.phase(group.end):
            record.update(roots_url=roots.value.url, roots=roots.captured_json,
                          method=roots.value.request.method, status=roots.value.status)
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
