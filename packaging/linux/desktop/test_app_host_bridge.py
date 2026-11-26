"""Main Connection admission through the actual native dispatch boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from gi.repository import GLib
import app_scheme_webkit as native
import test_app_events as events
import test_client_model as clients


class MainHostTests(unittest.TestCase):
    def test_enable_consumes_production_trace(self):
        import host_bridge
        from test_connection_cli import Bus, terminal_frame
        target = os.environ.get("ENABLE_PREPARATION_TRACE")
        trace = json.loads(Path(target).read_text()) if target else None
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            desktop = prefix / 'share/happyranch/desktop'
            desktop.mkdir(parents=True)
            (desktop / 'native-connection-bridge.v1.json').write_bytes(
                (Path(__file__).parents[3] / 'protocol/native-connection-bridge.v1.json').read_bytes())
            client = SimpleNamespace(prompt=None, model=SimpleNamespace(confirming_target=False, host_prompt_document=None))
            bridge = self.app.host_adapter = host_bridge.HostBridge(client, prefix, lambda en, zh: en)
            bus = bridge.connection = Bus()
            bus.terminal_frames = [trace["signal"]["raw"] if trace else terminal_frame()]
            bus.early = True
            parent = SimpleNamespace(get_display=lambda: SimpleNamespace(is_closed=lambda: False), get_visible=lambda: True)
            self.app.view.get_toplevel = lambda: parent
            self.subscribe("host.enableFinished")
            outcomes = self.call("host.enable")
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertEqual(len(outcomes), 1)
            self.assertTrue(outcomes[0][0])
            deliveries = [entry for entry in self.delivered if entry.get("event") == "host.enableFinished"]
            self.assertEqual(len(deliveries), 1)
            event = json.loads(deliveries[0]["raw"])
            self.assertEqual(event["operationId"], outcomes[0][1]["operationId"])
            self.assertEqual(event["error"], dict(code="enablePreparationFailed", category="inventory",
                                                 effect="unknown", recovery="nativeRecovery"))
            self.assertEqual([call[3] for call in bus.calls], ["Enable"])
            self.subscribe("host.enableFinished", False)
            if trace:
                trace["nativeAdmission"] = outcomes[0][1]
                trace["nativeEvent"] = deliveries[0]["raw"]
                Path(target).write_text(json.dumps(trace, sort_keys=True) + "\n")

    def test_enable_main_terminal_projection(self):
        from test_connection_cli import PREPARATION_TOKENS
        for category in PREPARATION_TOKENS:
            with self.subTest(category=category):
                self.app.host_adapter = SimpleNamespace(host_request=lambda method, finish, **kw: finish(
                    None, dict(code="enablePreparationFailed", category=category), "unknown", "nativeRecovery"))
                self.assertEqual(self.call("host.enable"), [(False, dict(code="enablePreparationFailed",
                    category=category, effect="unknown", recovery="nativeRecovery"))])

    def setUp(self):
        events.EventTests.setUp(self)
        self.app.connection_protocol = native._contract.Protocol(Path(__file__).parents[3] /
            'protocol/native-connection-bridge.v1.json')
        self.app.view.get_toplevel = lambda: SimpleNamespace(get_visible=lambda: True)
    subscribe = events.EventTests.subscribe

    def call(self, command, data="{}", token=None):
        outcomes = []
        reply = SimpleNamespace(return_value=lambda raw: outcomes.append((True, json.loads(raw))),
            return_error_message=lambda raw: outcomes.append((False, json.loads(raw))))
        value = SimpleNamespace(is_string=lambda: True, get_context=lambda: None,
            to_string=lambda: json.dumps(dict(token=token or self.app.bridge_token, command=command, data=data)))
        self.app.bridge_message(self.app.controller, value, reply)
        return outcomes

    def test_host_routing_closed_arguments_and_document_fence(self):
        calls = []
        snapshot = native._contract.Protocol(__import__('pathlib').Path(__file__).parents[3] /
            'protocol/native-connection-bridge.v1.json').host_unavailable()
        def request(method, finish, **kwargs):
            calls.append(method)
            finish(snapshot)
        self.app.host_adapter = SimpleNamespace(host_request=request)
        self.assertEqual(self.call('host.status'), [(True, snapshot)])
        self.assertEqual(calls, ['Status'])
        for data, token, uri in (('{"extra":1}', None, native.ORIGIN + '/'),
                                 ('{}', 'retired', native.ORIGIN + '/'),
                                 ('{}', None, native.ORIGIN + '/api/v1/health'),
                                 ('{}', None, 'https://example.invalid/')):
            self.uri = uri
            self.assertEqual(self.call('host.status', data, token)[0][1]['code'], 'invalidRequest')
        self.assertEqual(calls, ['Status'])

    def test_host_errors_preserve_effect_recovery_and_validate_results(self):
        def request(method, finish, **kwargs):
            finish(None, 'outcomeUnknown', 'unknown', 'nativeRecovery')
        self.app.host_adapter = SimpleNamespace(host_request=request)
        self.assertEqual(self.call('host.enable'), [(False, dict(code='outcomeUnknown', effect='unknown',
                                                               recovery='nativeRecovery'))])
        self.app.host_adapter.host_request = lambda method, finish, **kwargs: finish(None, 'hostStateUnrecognized')
        self.assertEqual(self.call('host.status'), [(False, dict(code='hostStateUnrecognized', effect='none',
                                                               recovery='refresh'))])
        for category in ('residue', 'owner', 'provenance', 'custody', 'inventory', 'quiescence', 'timeout', 'status_unavailable', 'unknown'):
            with self.subTest(category=category):
                self.app.host_adapter.host_request = lambda method, finish, **kwargs: finish(
                    None, dict(code='hostStateUnrecognized', category=category))
                self.assertEqual(self.call('host.status'), [(False, dict(code='hostStateUnrecognized',
                    category=category, effect='none', recovery='refresh'))])
        self.app.host_adapter.host_request = lambda method, finish, **kwargs: finish({'secret': 'synthetic'})
        result = self.call('host.status')
        self.assertFalse(result[0][0])
        self.assertNotIn('secret', result[0][1])

    def test_subscription_snapshot_and_document_teardown(self):
        subscriptions, removals = [], []
        self.app.host_adapter = SimpleNamespace(
            host_epoch=0,
            subscribe=lambda owner, **kwargs: subscriptions.append((owner, kwargs)),
            unsubscribe=lambda owner: removals.append(owner), detach=lambda owner: removals.append(owner))
        self.subscribe('host.stateChanged')
        self.assertEqual(self.errors, [], 'Main admits Host events')
        self.assertEqual(len(subscriptions), 1)
        callback = subscriptions[0][1]['emit']
        callback('{"snapshot":"first"}')
        self.assertEqual(self.delivered[-1]['event'], 'host.stateChanged')
        self.subscribe('host.stateChanged', False)
        self.assertEqual(removals, [self.app])
        self.subscribe('host.stateChanged')
        callback = subscriptions[-1][1]['emit']
        self.app.invalidate_document()
        count = len(self.delivered)
        self.app.bridge_token = 'new-document'
        self.subscribe('host.stateChanged')
        callback('{"snapshot":"late"}')
        self.assertEqual(len(self.delivered), count)
        self.app.timer = GLib.timeout_add_seconds(60, lambda: False)
        self.app.close()
        self.assertFalse(self.app.host_subscribed)
        self.assertIs(removals[-1], self.app)

    def summary(self, **counts):
        data = dict(dirty=0, failed=0, pending=0, unknown=0)
        data.update(counts)
        blocking = 'hold' if data['pending'] or data['unknown'] else 'prompt' if data['dirty'] or data['failed'] else 'none'
        return dict(schema=2, blocking=blocking, counts=data)

    def test_owners_admission_malformed_refusal_and_retirement(self):
        clean = self.summary()
        self.assertEqual(self.call('app.ownersChanged', json.dumps(clean)), [(True, {})])
        self.assertEqual(self.app.owner_snapshot(), ())
        invalid = [dict(clean, extra=True), dict(clean, schema=True), dict(clean, blocking='hold'),
                   dict(clean, counts=dict(clean['counts'], pending=True)),
                   dict(clean, counts=dict(clean['counts'], unknown=256)),
                   dict(clean, gate=dict(requestId='unissued', decision='keep'))]
        for item in invalid:
            self.call('app.ownersChanged', json.dumps(clean))
            self.assertEqual(self.call('app.ownersChanged', json.dumps(item))[0][1]['code'], 'invalidRequest')
            self.assertIsNone(self.app.owner_snapshot(), 'bad current summary retires clearance')
        self.call('app.ownersChanged', json.dumps(clean))
        self.assertEqual(self.call('app.ownersChanged', '{"schema":2,"schema":2}')[0][1]['code'], 'invalidRequest')
        self.assertIsNone(self.app.owner_snapshot())
        self.call('app.ownersChanged', json.dumps(clean))
        self.call('app.ownersChanged', json.dumps(self.summary(unknown=1)), token='stale')
        self.assertEqual(self.app.owner_snapshot(), (), 'foreign input cannot retire current evidence')
        self.app.invalidate_document()
        self.assertIsNone(self.app.owner_snapshot())

    def test_owner_refusal_order_and_retained_uncertainty(self):
        for counts, expected in ((dict(dirty=1, pending=1, unknown=1), 'owner_save_unknown'),
                                 (dict(dirty=1, pending=1), 'owner_save_pending'),
                                 (dict(failed=1), 'owner_edits_pending'),
                                 (dict(dirty=1), 'owner_edits_pending')):
            self.assertEqual(self.call('app.ownersChanged', json.dumps(self.summary(**counts))), [(True, {})])
            self.assertEqual(self.app.select_target(None, owner_saves=self.app.owner_snapshot()), expected)
        self.call('app.ownersChanged', json.dumps(self.summary(pending=1)))
        self.app.invalidate_document()
        self.app.bridge_token = 'next-document'
        self.call('app.ownersChanged', json.dumps(self.summary()))
        self.assertEqual(self.app.select_target(None, owner_saves=self.app.owner_snapshot()), 'owner_save_unknown')

    def test_commit_and_disconnect_consume_the_admitted_summary(self):
        for command in ('client.commitSwitch', 'client.disconnect'):
            for counts, code in ((dict(unknown=1, pending=1, dirty=1), 'owner_save_unknown'),
                                 (dict(pending=1, dirty=1), 'owner_save_pending'),
                                 (dict(dirty=1), 'owner_edits_pending'),
                                 (dict(failed=1), 'owner_edits_pending')):
                ui = clients.UI()
                model = ui.model
                model.main = self.app
                self.app.client_dispatch = model.dispatch
                model.stage = 'connected'
                if command == 'client.commitSwitch':
                    model.session = model.candidate_target = object()
                    model.switch_candidate = {'phase': 'ready_to_switch'}
                else:
                    model.active_session = object()
                before = self.app.generation
                self.assertEqual(self.call('app.ownersChanged', json.dumps(self.summary(**counts))), [(True, {})])
                outcomes = self.call(command)
                if command == 'client.disconnect':
                    self.assertEqual(outcomes, [], 'disconnect still requires native confirmation')
                    ui.confirmed(True)
                self.assertEqual(outcomes[0][1], dict(code=code, effect='none', recovery='refresh'))
                self.assertEqual(self.app.generation, before)


class SharedHostTests(unittest.TestCase):
    def test_enable_observer_survives_admission(self):
        for old_setup in ('off', 'ready'):
            for old_reply_first in (False, True):
                for fresh_setup in ('off', 'ready', 'failed'):
                    with self.subTest(old_setup=old_setup, old_reply_first=old_reply_first,
                                      fresh_setup=fresh_setup):
                        self.assert_enable_observer_survives_admission(
                            old_setup, old_reply_first, fresh_setup)

    def assert_enable_observer_survives_admission(self, old_setup: str, old_reply_first: bool,
                                                fresh_setup: str) -> None:
        import host_bridge
        from test_connection_cli import Bus, terminal_frame
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            desktop = prefix / 'share/happyranch/desktop'
            desktop.mkdir(parents=True)
            (desktop / 'native-connection-bridge.v1.json').write_bytes(
                (Path(__file__).parents[3] / 'protocol/native-connection-bridge.v1.json').read_bytes())
            client = SimpleNamespace(prompt=None, model=SimpleNamespace(confirming_target=False, host_prompt_document=None))
            bridge = host_bridge.HostBridge(client, prefix, lambda en, zh: en)
            bus = bridge.connection = Bus()
            bus.snapshot = dict(phase=old_setup, ready=old_setup == 'ready', devices=[], pending=None, selected=None)
            bus.setup_state = old_setup
            bus.hold = True
            parent = SimpleNamespace(get_display=lambda: SimpleNamespace(is_closed=lambda: False))
            owner, other = object(), object()
            current, events, strangers, admissions = [True], [], [], []
            fresh, joined, before, before_joined = [], [], [], []
            def terminal(raw):
                events.append(raw)
                # The real GUI asks for Status from terminal delivery. Both
                # direct callers must receive a genuinely post-terminal read.
                bridge.host_request('Status', lambda *a: fresh.append(a), owner=owner,
                                    current=lambda: current[0], window=parent)
                bridge.host_request('Status', lambda *a: joined.append(a), owner=other,
                                    current=lambda: current[0], window=parent)
            bridge.subscribe_enable(owner, current=lambda: current[0], emit=terminal)
            bridge.subscribe_enable(other, current=lambda: True, emit=strangers.append)
            self.addCleanup(bridge.unsubscribe_enable, owner)
            self.addCleanup(bridge.unsubscribe_enable, other)
            self.addCleanup(bridge.unsubscribe, other)
            context = GLib.MainContext.default()
            def drain():
                while bus.held:
                    bus.held.pop(0)()
                while context.pending():
                    context.iteration(False)
            def request():
                bridge.host_request('Enable', lambda *a: admissions.append(a), owner=owner,
                                    current=lambda: current[0], window=parent)
                drain()
            request()
            self.assertEqual(len(admissions), 1)
            self.assertIsNone(bridge.active_operation)
            operation_id = admissions[-1][0]["operationId"]
            state_events = []
            bridge.subscribe(other, current=lambda: True, visible=lambda: False, emit=state_events.append, window=parent)
            while context.pending():
                context.iteration(False)
            state_events.clear()
            # A separate Status may own the active operation at terminal arrival.
            bridge.host_request('Status', lambda *a: before.append(a), owner=other, current=lambda: True, window=parent)
            bridge.host_request('Status', lambda *a: before_joined.append(a), owner=other,
                                current=lambda: True, window=parent)
            old_operation = bridge.active_operation
            old_reply = bus.held.pop(0)
            def old_observation():
                old_reply()
                self.assertEqual(bus.calls[-1][3], 'StatusV2')
                self.assertEqual(before, [], 'v1 alone cannot finish the native observation')
                self.assertTrue(bridge.busy)
                # Capture the old detail before the next physical read changes.
                bus.setup_state = fresh_setup
                bus.held.pop(0)()
            # Bus captured the older bytes at dispatch. Change only the next
            # physical read's IO result, never the production projection.
            bus.snapshot = dict(phase=fresh_setup, ready=fresh_setup == 'ready', devices=[], pending=None, selected=None)
            if fresh_setup == 'failed':
                bus.status_error = 'ai.happyranch.RemoteAccess1.Error.unavailable'
            bus.signal(terminal_frame())
            if old_reply_first:
                old_observation()
            while context.pending():
                context.iteration(False)
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0])["operationId"], operation_id)
            self.assertEqual(json.loads(events[0])["error"]["effect"], "unknown")
            self.assertEqual(strangers, [])
            self.assertEqual(len(admissions), 1)
            self.assertEqual(fresh, [], 'terminal refresh must await a new native read')
            self.assertEqual(joined, [])
            if not old_reply_first:
                self.assertIs(bridge.active_operation, old_operation)
                self.assertEqual([call[3] for call in bus.calls], ['Enable', 'Status'])
                old_observation()
            self.assertEqual(fresh, [], 'an older Status cannot satisfy the post-terminal direct caller')
            self.assertEqual(joined, [], 'an older Status cannot satisfy a post-terminal coalescing waiter')
            self.assertEqual(before, before_joined)
            self.assertEqual(len(before), 1)
            if not old_reply_first:
                self.assertEqual(before, [(None, 'unavailable')])
            else:
                self.assertEqual(before[0][0]['setup'], old_setup)
            self.assertEqual([call[3] for call in bus.calls], ['Enable', 'Status', 'StatusV2', 'Status'])
            self.assertEqual([call[7] for call in bus.calls if call[3] == 'Status'], [3000, 3000])
            self.assertEqual(len(bus.held), 1, 'new-epoch callers still share one native Status operation')
            self.assertTrue(bridge.busy)
            self.assertIsNot(bridge.active_operation, old_operation)
            self.assertEqual(bridge.host_snapshot['observation'], 'unavailable')
            self.assertEqual(state_events, [], "a Status begun before terminal cannot publish fresh state")
            drain()
            self.assertEqual([call[3] for call in bus.calls],
                ['Enable', 'Status', 'StatusV2', 'Status'] + ([] if fresh_setup == 'failed' else ['StatusV2']))
            self.assertTrue(all(call[7] == 3000 for call in bus.calls if call[3] == 'StatusV2'))
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh, joined)
            self.assertEqual(len(state_events), 1)
            if fresh_setup == 'failed':
                self.assertEqual(fresh, [(None, dict(code='hostStateUnrecognized', category='status_unavailable'))])
                self.assertEqual(bridge.host_snapshot['observation'], 'unavailable')
                self.assertEqual(bridge.host_snapshot['setup'], 'unknown')
            else:
                self.assertEqual(fresh[0][0]['observation'], 'known')
                self.assertEqual(fresh[0][0]['setup'], fresh_setup)
                self.assertEqual(bridge.host_snapshot, fresh[0][0])
            self.assertEqual(json.loads(state_events[0])['snapshot'], bridge.host_snapshot)
            self.assertFalse(bridge.busy)
            bus.signal(terminal_frame())
            while context.pending():
                context.iteration(False)
            self.assertEqual(len(events), 1)
            # Unsubscribe/reattach has no replay. Queued old observation is dead.
            bridge.unsubscribe_enable(owner)
            bridge.subscribe_enable(owner, current=lambda: True, emit=events.append)
            request()
            bus.signal(terminal_frame())
            bridge.unsubscribe_enable(owner)
            while context.pending():
                context.iteration(False)
            self.assertEqual(len(events), 1)
            self.assertEqual([call[3] for call in bus.calls].count('Enable'), 2)
            bridge.unsubscribe(other)

    def test_one_slot_status_waiters_poll_and_subscription_fence(self):
        import host_bridge
        from test_connection_cli import Bus
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            desktop = prefix / 'share/happyranch/desktop'
            desktop.mkdir(parents=True)
            (desktop / 'native-connection-bridge.v1.json').write_bytes(
                (Path(__file__).parents[3] / 'protocol/native-connection-bridge.v1.json').read_bytes())
            client = SimpleNamespace(prompt=None, model=SimpleNamespace(confirming_target=False, host_prompt_document=None))
            main = host_bridge.shared_bridge(client, prefix, lambda en, zh: en)
            window = host_bridge.shared_bridge(client, prefix, lambda en, zh: en)
            self.assertIs(main, window)
            bus = main.connection = Bus()
            bus.hold = True
            parent = SimpleNamespace(get_display=lambda: SimpleNamespace(is_closed=lambda: False))
            owner = object()
            outcomes = []
            request = lambda bridge, method: bridge.host_request(method, lambda *args: outcomes.append(args),
                owner=owner, current=lambda: True, window=parent)
            request(main, 'Enable')
            request(window, 'Status')
            request(main, 'Status')
            request(window, 'Disable')
            self.assertEqual(outcomes, [(None, 'busy')] * 3)
            self.assertEqual([call[3] for call in bus.calls], ['Enable'])
            while bus.held:
                bus.held.pop(0)()
            outcomes.clear()
            request(window, 'Status')
            request(main, 'Status')
            self.assertEqual([call[3] for call in bus.calls], ['Enable', 'Status'])
            while bus.held:
                bus.held.pop(0)()
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0], outcomes[1])
            events = []
            visible = [False]
            main.subscribe(owner, current=lambda: True, visible=lambda: visible[0], emit=events.append, window=parent)
            timer = main.host_timer
            main.subscribe(owner, current=lambda: True, visible=lambda: True, emit=events.append, window=parent)
            self.assertEqual(main.host_timer, timer)
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertEqual(json.loads(events[-1])['snapshot']['setup'], 'ready')
            count = len(bus.calls)
            main.poll_host()
            self.assertEqual(len(bus.calls), count, 'hidden Main does not poll')
            visible[0] = True
            main.poll_host()
            self.assertEqual(bus.calls[-1][3], 'Status')
            main.unsubscribe(owner)
            self.assertIsNone(main.host_timer)
            count = len(events)
            while bus.held:
                bus.held.pop(0)()
            while context.pending():
                context.iteration(False)
            self.assertEqual(len(events), count, 'late observation cannot reach a retired subscriber')


class DispatchTests(unittest.TestCase):
    def test_new_client_commands_use_existing_paths_and_retry_refusal(self):
        ui = clients.UI()
        model = ui.model
        calls, results = [], []
        model.health = lambda finish: (calls.append('health'), finish({'health': 'synthetic'}))
        model.remove_saved = lambda ref, document, finish: (calls.append((ref, document)), finish({'removed': True}))
        model.save = lambda token, finish: (calls.append(('save', token)), finish({'saved': True}))
        finish = lambda *args: results.append(args)
        model.dispatch('client.health', 7, finish)
        self.assertEqual(calls, ['health'])
        model.dispatch('client.removeSaved', 7, finish, computerRef='opaque')
        self.assertEqual(calls[-1], ('opaque', 7))
        model.dispatch('client.retrySave', 7, finish)
        self.assertEqual(results[-1], (None, 'stateChanged'))
        model.stage, model.saved_access = 'connected', 'failed'
        model.dispatch('client.retrySave', 7, finish)
        self.assertEqual(calls[-1], ('save', model.token))


if __name__ == '__main__':
    unittest.main()
