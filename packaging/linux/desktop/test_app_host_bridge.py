"""Main Connection admission through the actual native dispatch boundary."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from gi.repository import GLib
import app_scheme_webkit as native
import test_app_events as events
import test_client_model as clients


class MainHostTests(unittest.TestCase):
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
        self.app.host_adapter.host_request = lambda method, finish, **kwargs: finish({'secret': 'synthetic'})
        result = self.call('host.status')
        self.assertFalse(result[0][0])
        self.assertNotIn('secret', result[0][1])

    def test_subscription_snapshot_and_document_teardown(self):
        subscriptions, removals = [], []
        self.app.host_adapter = SimpleNamespace(
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
    def test_one_slot_status_waiters_poll_and_subscription_fence(self):
        import host_bridge
        import test_client_gtk as gtk
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
            bus = main.connection = gtk.HostBus()
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
            bus.held.pop()()
            outcomes.clear()
            request(window, 'Status')
            request(main, 'Status')
            self.assertEqual([call[3] for call in bus.calls], ['Enable', 'Status'])
            bus.held.pop()()
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
            bus.held.pop()()
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
