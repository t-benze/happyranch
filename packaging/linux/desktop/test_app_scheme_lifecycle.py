"""Native lifecycle wire boundary; real relay payloads, no service effects."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
import unittest

import app_scheme_webkit as native
import test_app_events as fixtures
import test_happyranch_window as launcher_fixtures


def summary(state: str = "clean", gate: dict | None = None) -> dict:
    counts = dict.fromkeys(("dirty", "failed", "pending", "unknown"), 0)
    if state != "clean":
        counts[state] = 1
    value = {"schema": 2, "blocking": "hold" if state in ("pending", "unknown")
             else "prompt" if state in ("dirty", "failed") else "none", "counts": counts}
    if gate is not None:
        value["gate"] = gate
    return value


class LifecycleBridgeTests(unittest.TestCase):
    setUp = fixtures.EventTests.setUp
    subscribe = fixtures.EventTests.subscribe

    def push(self, body: dict, token: str | None = None) -> None:
        item = {"token": token or self.app.bridge_token, "command": "app.ownersChanged",
                "data": json.dumps(body)}
        value = SimpleNamespace(is_string=lambda: True, to_string=lambda: json.dumps(item),
                                get_context=lambda: None)
        self.app.bridge_message(self.app.controller, value, self.reply)

    def request(self, action: str = "close") -> dict:
        self.subscribe("app.lifecycleRequested")
        self.effects = []
        self.assertEqual(self.errors, [])
        self.app.request_lifecycle(action, lambda action: self.effects.append(action) or True)
        return json.loads(self.delivered[-1]["raw"])

    def test_each_action_needs_matching_decision_even_when_clean(self) -> None:
        ids = set()
        for action in ("close", "quit", "relaunch"):
            with self.subTest(action=action):
                self.push(summary())
                event = self.request(action)
                self.assertEqual(set(event), {"schema", "requestId", "action"})
                self.assertEqual((event["schema"], event["action"]), (2, action))
                self.assertNotIn(event["requestId"], ids)
                ids.add(event["requestId"])
                self.assertEqual(self.effects, [])
                # A count update is never standing consent.
                self.push(summary())
                self.assertEqual(self.effects, [])
                self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
                self.assertEqual(self.errors, [])
                self.assertEqual(self.effects, [action])
                self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
                self.assertEqual(self.effects, [action], "consumed consent cannot exit twice")
                self.assertIsNone(self.app.owner_snapshot())
                self.errors.clear()

    def test_keep_repeats_and_foreign_decisions_preserve_captured_action(self) -> None:
        self.push(summary("dirty"))
        event = self.request("quit")
        self.app.request_lifecycle("relaunch", lambda _action: self.fail("replaced action"))
        self.assertEqual(json.loads(self.delivered[-1]["raw"]), event)
        self.push(summary(gate={"requestId": "unissued-id", "decision": "proceed"}))
        self.assertEqual(self.effects, [], "another request ID cannot spend this consent")
        self.assertIsNone(self.app.owner_snapshot())
        self.errors.clear()
        self.push(summary("dirty"))
        self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}), "retired")
        self.assertEqual(self.effects, [])
        self.assertEqual(self.app.owners["blocking"], "prompt", "foreign input cannot retire current evidence")
        self.errors.clear()
        self.push(summary("dirty", {"requestId": event["requestId"], "decision": "keep"}))
        self.assertEqual(self.errors, [])
        self.assertIsNone(self.app.lifecycle_pending)
        self.assertEqual(self.effects, [])
        self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
        self.assertEqual(self.effects, [])
        self.assertEqual(json.loads(self.errors[-1]), {"code": "invalidRequest", "effect": "none", "recovery": "none"})

    def test_pending_unknown_and_native_transfer_refuse_proceed(self) -> None:
        for state in ("dirty", "failed", "pending", "unknown"):
            with self.subTest(state=state):
                self.push(summary(state))
                event = self.request()
                self.push(summary(state, {"requestId": event["requestId"], "decision": "proceed"}))
                self.assertEqual(self.effects, [])
                self.assertEqual(json.loads(self.errors[-1])["effect"], "none")
                self.errors.clear()
                self.app.lifecycle_pending = None
        self.push(summary())
        event = self.request()
        self.app.transfers["sent"] = SimpleNamespace(generation=self.app.generation, finished=False, method="POST")
        self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
        self.assertEqual(self.effects, [])
        self.assertEqual(json.loads(self.errors[-1])["code"], "non_get_pending")

    def test_retirement_drops_request_and_preserves_unresolved_latch(self) -> None:
        for state in ("clean", "pending", "unknown"):
            with self.subTest(state=state):
                self.app.owner_uncertain = False
                self.push(summary(state))
                event = self.request()
                self.app.invalidate_document()
                self.assertIsNone(self.app.lifecycle_pending)
                self.assertFalse(self.app.lifecycle_subscribed)
                self.app.bridge_token = "replacement"
                self.push(summary())
                self.assertEqual(self.app.owner_uncertain, state != "clean")
                self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
                self.assertEqual(self.effects, [])
                self.errors.clear()

    def test_unavailable_subscription_does_not_queue_or_auto_exit(self) -> None:
        effects = []
        self.subscribe("app.lifecycleRequested")
        self.assertEqual(self.errors, [], "the lifecycle subscription must be admitted")
        self.subscribe("app.lifecycleRequested", False)
        self.assertFalse(self.app.request_lifecycle("quit", effects.append))
        self.assertIsNone(self.app.lifecycle_pending)
        self.subscribe("app.lifecycleRequested")
        self.assertEqual(self.delivered, [])
        self.push(summary())
        self.app.request_lifecycle("quit", lambda _action: False)
        event = json.loads(self.delivered[-1]["raw"])
        self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
        self.assertIsNone(self.app.lifecycle_pending)
        self.assertEqual(json.loads(self.errors[-1])["effect"], "none")
        self.assertEqual(effects, [])

    def test_owner_keep_running_local_remote_matrix(self) -> None:
        for keep in ("off", "on"):
            for target in ("local", "remote"):
                for state in ("clean", "dirty", "pending", "unknown", "no-summary"):
                    with self.subTest(keep=keep, target=target, owners=state):
                        self.setUp()
                        service_fixture = launcher_fixtures.ServiceTests()
                        service_fixture.setUp()
                        try:
                            # This new harness supports the current bounded stop
                            # timeout. The legacy fixture's timeout=10 assertion
                            # remains untouched (and is reported as inherited).
                            def run_command(argv: list, **kwargs: object) -> object:
                                if argv[:3] == ["systemctl", "--user", "stop"]:
                                    self.assertEqual(kwargs["timeout"], 60)
                                    service_fixture.calls.append(argv)
                                    return subprocess.CompletedProcess(argv, 0, "", "")
                                return service_fixture.run_command(argv, **kwargs)
                            service_fixture.service.runner = run_command
                            service_fixture.state["UnitFileState"] = "enabled" if keep == "on" else "disabled"
                            service_fixture.linger = "yes" if keep == "on" else "no"
                            service_fixture.start()
                            self.app.daemon = (native.LocalDaemon(43127) if target == "local" else
                                native.RemoteDaemon(None, target_id="stand-in", name="Stand-in Host"))
                            self.subscribe("app.lifecycleRequested")
                            self.assertEqual(self.errors, [], "matrix requires native lifecycle admission")
                            if state != "no-summary":
                                self.push(summary(state))
                            admitted = []
                            def exit_action(action: str) -> bool:
                                service_fixture.service.close_window()
                                admitted.append(action)
                                return True
                            self.assertTrue(self.app.request_lifecycle("close", exit_action))
                            event = json.loads(self.delivered[-1]["raw"])
                            self.assertEqual(admitted, [])
                            if state == "clean":
                                self.push(summary(gate={"requestId": event["requestId"], "decision": "proceed"}))
                                self.assertEqual(admitted, ["close"])
                                self.assertEqual("stop" in service_fixture.verbs(), keep == "off")
                                result = "closed/service-" + ("retained" if keep == "on" else "stopped")
                            else:
                                self.assertNotIn("stop", service_fixture.verbs())
                                result = "web-prompt/no-effect" if state == "dirty" else "held/no-effect"
                            print(f"MATRIX keep={keep} target={target} owners={state} observed={result}")
                        finally:
                            service_fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
