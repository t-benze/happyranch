"""Closed Main event boundary: native replies and the shipped page/relay scripts."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gi.repository import GLib
import app_scheme_webkit as native


class EventTests(unittest.TestCase):
    def setUp(self):
        self.app = native.AppScheme.__new__(native.AppScheme)
        self.app.daemon, self.app.closed = None, False
        self.app.initialize_state()
        self.app.transfers = {}
        self.app.controller = object()
        self.app.GLib = GLib
        self.app.JSC = SimpleNamespace(Value=SimpleNamespace(new_string=lambda _context, raw: raw))
        self.delivered, self.errors = [], []
        self.uri = native.ORIGIN + "/"
        self.app.view = SimpleNamespace(get_uri=lambda: self.uri,
            call_async_javascript_function=lambda *args: self.delivered.append(args[2].unpack()))
        self.app.bridge_token = "native-probed-token"
        self.reply = SimpleNamespace(return_value=lambda raw: None,
                                     return_error_message=self.errors.append)

    def subscribe(self, event, enabled=True, **extra):
        item = {"token": self.app.bridge_token, "event": event, "enabled": enabled, **extra}
        value = SimpleNamespace(is_string=lambda: True, to_string=lambda: json.dumps(item),
                                get_context=lambda: None)
        self.app.bridge_message(self.app.controller, value, self.reply)

    def test_named_native_subscription_and_exact_payloads_without_target_effects(self):
        generation, document = self.app.generation, self.app.document
        self.subscribe("app.dialogRequested")
        self.assertEqual(self.errors, [], "native must admit the closed dialog event")
        self.subscribe("app.dialogRequested", False)
        self.subscribe("app.stateChanged")
        self.assertEqual(self.delivered[-1]["event"], "app.stateChanged")
        self.assertFalse(self.app.request_dialog("settings"), "status subscription is not dialog admission")
        self.delivered.clear()
        self.subscribe("app.dialogRequested")
        self.assertEqual(self.errors, [], "native must admit the closed dialog event")
        self.assertEqual(self.delivered, [], "subscribing must not replay a dialog")
        for dialog in ("connection", "settings"):
            self.assertTrue(self.app.request_dialog(dialog))
            self.assertEqual(self.delivered[-1], {"token": "native-probed-token", "event": "app.dialogRequested",
                                                "raw": '{"schema":2,"dialog":"' + dialog + '"}'})
        self.subscribe("app.stateChanged", False)
        self.assertTrue(self.app.request_dialog("connection"), "unsubscribing status cannot disable dialogs")
        self.subscribe("app.dialogRequested", False)
        count = len(self.delivered)
        self.app.emit_state()
        self.assertFalse(self.app.request_dialog("settings"))
        self.assertFalse(self.app.request_dialog("preferences"))
        self.assertEqual(len(self.delivered), count)
        self.assertEqual((self.app.generation, self.app.document, self.app.daemon, self.app.candidate),
                         (generation, document, None, None))

    def test_closed_subscription_and_document_retirement_drop_without_replay(self):
        for item in ({"event": "app.unknown"}, {"enabled": 1}, {"token": "old-document"}, {"extra": True}):
            self.subscribe("app.dialogRequested", **item) if "event" not in item else self.subscribe(item["event"])
        self.assertEqual(len(self.errors), 4)
        self.assertFalse(getattr(self.app, "dialog_subscribed", False))
        self.subscribe("app.dialogRequested")
        self.assertEqual(len(self.errors), 4, "valid event must reach its subscription branch")
        self.assertTrue(self.app.request_dialog("settings"))
        self.app.invalidate_document()
        count = len(self.delivered)
        self.assertFalse(self.app.request_dialog("connection"))
        self.assertFalse(self.app.subscribed)
        self.assertFalse(self.app.dialog_subscribed)
        self.app.bridge_token = "new-document"
        self.subscribe("app.dialogRequested")
        self.assertEqual(len(self.delivered), count, "new document cannot replay a retired request")
        self.uri = "http://127.0.0.1:43127/"
        self.subscribe("app.dialogRequested", False)
        self.assertEqual(len(self.errors), 5, "ordinary HTTP documents cannot change subscriptions")
        self.app.closed = True
        self.assertFalse(self.app.request_dialog("settings"))

    def test_dialog_entry_is_independent_of_every_target_readiness_state(self):
        self.subscribe("app.dialogRequested")
        self.assertEqual(self.errors, [], "dialog subscription must be admitted")
        for state in ("no_target", "target_starting", "ready", "unreachable", "incompatible", "busy"):
            self.app.daemon = None if state == "no_target" else native.LocalDaemon(43127)
            if self.app.daemon is not None:
                self.app.daemon.state = state
            before = self.app.status()
            with self.subTest(state=state), patch.object(self.app, "select_target", side_effect=AssertionError("entry changed target")), \
                    patch.object(self.app, "refresh_status", side_effect=AssertionError("entry started admission")):
                self.assertTrue(self.app.request_dialog("connection"))
                self.assertEqual(json.loads(self.delivered[-1]["raw"]), {"schema": 2, "dialog": "connection"})
            self.assertEqual(self.app.status(), before)

    def connection_boundary(self, case: str, command: str = 'host.revokeDevice') -> None:
        script = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const sources=JSON.parse(process.env.HR_EVENT_SCRIPTS);
const handlers=[new Map(),new Map()], native=[], traffic=[];
const contexts=handlers.map(h=>{
 const c={location:{origin:'happyranch-app://app'},crypto:{randomUUID:()=> 'token'},TextEncoder,
 MutationObserver:class{observe(){}disconnect(){}},document:{},
 addEventListener:(n,f)=>{if(!h.has(n))h.set(n,[]);h.get(n).push(f);}};
 c.window=c.top=c;
 c.webkit={messageHandlers:{appBridge:{postMessage:raw=>{
  const item=JSON.parse(raw);native.push(item);
  if(item.command && (sources.case.startsWith('retirement-') || sources.case==='locale'))
   return new Promise((resolve,reject)=>{c.finish=resolve;c.refuse=reject;});
  return item.command ? Promise.reject(new Error(JSON.stringify(c.failure || {code:'permissionDenied',effect:'unknown',recovery:'nativeRecovery'}))) : Promise.resolve('{}');
 }}}};return vm.createContext(c);
});
contexts.forEach(c=>c.postMessage=(data,origin)=>{
 traffic.push(data);contexts.forEach((target,i)=>{target.data=data;target.origin=origin;
 vm.runInContext('for(const f of callbacks)f({data,origin,source:window})',target);});
});
contexts.forEach(c=>c.callbacks=[]);
vm.runInContext(sources.page,contexts[0]);vm.runInContext(sources.relay,contexts[1]);
contexts.forEach((c,i)=>c.callbacks=handlers[i].get('message'));
(async()=>{
 if(sources.case==='events'){
 vm.runInContext("globalThis.hosts=[];globalThis.off=happyranchConnection.on('host.stateChanged',raw=>hosts.push(raw));",contexts[0]);
 vm.runInContext("hrAppBridge.emit('host.stateChanged','premature');hrAppBridge.admit(hrAppBridge.token);hrAppBridge.emit('host.stateChanged','snapshot');hrAppBridge.emit('unknown','wrong');",contexts[1]);
 assert.equal(JSON.stringify(contexts[0].hosts),'["snapshot"]');
 vm.runInContext('off()',contexts[0]);assert.equal(native.at(-1).enabled,false);
 return;
 }
 vm.runInContext('hrAppBridge.admit(hrAppBridge.token)',contexts[1]);
 if(sources.case==='locale'){
  vm.runInContext("globalThis.seen=[];happyranchConnection.on('app.stateChanged',raw=>seen.push(['event',JSON.parse(raw)]));",contexts[0]);
  for(const locale of ['zh-CN','en']){
   contexts[0].args=JSON.stringify({schema:2,locale});
   const result=vm.runInContext("happyranchConnection.call('app.locale.set',args).then(raw=>{seen.push(['reply',JSON.parse(raw)]);return raw;})",contexts[0]);
   assert.equal(native.at(-1).command,'app.locale.set','locale must cross both admission tables');
   contexts[1].state=JSON.stringify({schema:2,locale:{current:locale,persisted:locale==='en'}});
   const published=vm.runInContext("hrAppBridge.publishLocale(state,'true')",contexts[1]);
   await Promise.resolve();
   assert.equal(contexts[0].seen.length,locale==='en' ? 2 : 0,'publication must wait for reply delivery');
   contexts[1].finish(contexts[1].state);
   await result;await published;await new Promise(resolve=>setImmediate(resolve));
  }
  assert.equal(JSON.stringify(contexts[0].seen.map(item=>[item[0],item[1].locale.current])),
   JSON.stringify([['reply','zh-CN'],['event','zh-CN'],['reply','en'],['event','en']]));
  assert.equal(contexts[0].seen[1][1].locale.persisted,false,'session-only also publishes');
  for(const args of ['{','{"schema":2,"locale":"en","locale":"zh-CN"}','{"schema":2,"locale":"fr"}']){
   contexts[0].args=args;
   const result=vm.runInContext("happyranchConnection.call('app.locale.set',args).catch(raw=>JSON.parse(raw))",contexts[0]);
   assert.equal(native.at(-1).data,args,'native receives the exact string, including duplicate keys');
   contexts[1].refuse(new Error('{"code":"invalidRequest","effect":"none","recovery":"none"}'));
   assert.equal((await result).code,'invalidRequest');
  }
  for(const args of [null,{},'x'.repeat(4096),'\0'.repeat(1024)]){
   contexts[0].args=args;const before=native.length;
   const error=await vm.runInContext("happyranchConnection.call('app.locale.set',args).catch(raw=>JSON.parse(raw))",contexts[0]);
   assert.equal(error.code,'invalidRequest');assert.equal(error.effect,'none');assert.equal(error.recovery,'none');
   assert.equal(native.length,before,'wrong types and oversized escaped payloads refuse before dispatch');
  }
  return;
 }
 if(sources.case.startsWith('retirement-')){
  const command=sources.command;
  contexts[0].command=command;
  contexts[0].args=['client.connectSaved','client.removeSaved'].includes(command) ? '{"computerRef":"opaque"}'
   : command==='app.ownersChanged' ? '{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}' : '{}';
  const result=vm.runInContext('globalThis.settlements=0;happyranchConnection.call(command,args).catch(raw=>{settlements++;return JSON.parse(raw);})',contexts[0]);
  assert.equal(native.filter(item=>item.command===command).length,1,'retirement must follow native dispatch');
  if(sources.case==='retirement-csp'){
   contexts[1].retireCallbacks=handlers[1].get('securitypolicyviolation');
   vm.runInContext("for(const fn of retireCallbacks)fn({effectiveDirective:'frame-src'})",contexts[1]);
  }else{
   contexts.forEach((c,i)=>{c.retireCallbacks=handlers[i].get('pagehide');
    vm.runInContext('for(const fn of retireCallbacks)fn()',c);});
  }
  let deadline;
  const observed=await Promise.race([result,new Promise((_,reject)=>{
   deadline=setTimeout(()=>reject(new Error('retirement did not settle the pending call')),1000);
  })]).finally(()=>clearTimeout(deadline));
  const read=['app.status','client.status','client.savedComputers','client.health','host.status','host.nextDevice'].includes(command);
  assert.equal(observed.code,'documentChanged');
  assert.equal(observed.effect,read ? 'none' : 'unknown','a dispatched mutation has no no-effect receipt');
  assert.equal(observed.recovery,read ? 'refresh' : 'nativeRecovery');
  contexts[1].finish('{}');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(contexts[0].settlements,1,'late native completion cannot resettle the retired call');
  const refused=await vm.runInContext('happyranchConnection.call(command,args).catch(raw=>JSON.parse(raw))',contexts[0]);
  assert.equal(refused.effect,'none','a new call after retirement is refused before dispatch');
  assert.equal(native.filter(item=>item.command).length,1,'retirement never replays a mutation');
  return;
 }
 if(sources.case==='errors'){
 for(const code of ['permissionDenied','owner_edits_pending','outcomeUnknown']){
  contexts[1].failure={code,effect:'unknown',recovery:'nativeRecovery'};
  const error=await vm.runInContext("happyranchConnection.call('client.commitSwitch','{}').catch(raw=>JSON.parse(raw))",contexts[0]);
  assert.equal(error.code,code);assert.equal(error.effect,'unknown');assert.equal(error.recovery,'nativeRecovery');
 }return;
 }
 const commands=['host.status','host.enable','host.turnOff','host.createInvite','host.approveDevice','host.rejectDevice','host.nextDevice','host.revokeDevice','client.health','client.retrySave','client.removeSaved','app.ownersChanged'];
 for(const command of commands){
  contexts[0].command=command;
  contexts[0].args=command==='client.removeSaved' ? '{"computerRef":"opaque"}' : command==='app.ownersChanged' ? '{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}' : '{}';
  const error=await vm.runInContext('happyranchConnection.call(command,args).catch(raw=>JSON.parse(raw))',contexts[0]);
  assert.equal(error.code,'permissionDenied',command);assert.equal(error.effect,'unknown');assert.equal(error.recovery,'nativeRecovery');
  assert.equal(native.at(-1).command,command);
 }
 for(const [command,args] of [['host.status','{ }'],['client.removeSaved','{"computerRef":3}'],['client.removeSaved','{"computerRef":"x","extra":0}'],['host.enable','[]'],['app.ownersChanged','{}']]){
  const before=native.length;contexts[0].command=command;contexts[0].args=args;
  await vm.runInContext('happyranchConnection.call(command,args).catch(()=>{})',contexts[0]);assert.equal(native.length,before);
 }
 console.log('Connection command/event/error boundary OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], env={**os.environ, "HR_EVENT_SCRIPTS": json.dumps(
            {"page": native.APP_PAGE, "relay": native.APP_RELAY, "case": case, "command": command})}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def retirement_boundary(self, case: str) -> None:
        for command in ('host.revokeDevice', 'host.enable', 'host.turnOff', 'host.createInvite',
                        'host.approveDevice', 'host.rejectDevice', 'client.removeSaved', 'client.retrySave',
                        'client.acceptInvite', 'client.retry', 'client.connectSaved', 'client.cancelAttempt',
                        'client.commitSwitch', 'client.disconnect', 'app.ownersChanged',
                        'app.status', 'client.status', 'client.savedComputers', 'client.health',
                        'host.status', 'host.nextDevice'):
            with self.subTest(command=command):
                self.connection_boundary(case, command)

    def test_csp_retirement_preserves_dispatched_mutation_uncertainty(self) -> None:
        self.retirement_boundary('retirement-csp')

    def test_pagehide_retirement_preserves_dispatched_mutation_uncertainty(self) -> None:
        self.retirement_boundary('retirement-pagehide')

    def test_connection_event_admission(self):
        self.connection_boundary('events')

    def test_connection_command_admission(self):
        self.connection_boundary('commands')

    def test_connection_error_semantics(self):
        self.connection_boundary('errors')

    def test_locale_relay_delivers_reply_before_even_an_early_native_publication(self):
        self.connection_boundary('locale')

    def test_page_and_named_world_relay_keep_listeners_separate(self):
        # VM supplies only browser plumbing. Both facades under test are the
        # literal shipping scripts, with no copy of their event-routing logic.
        script = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const sources = JSON.parse(process.env.HR_EVENT_SCRIPTS);
const listeners = [new Map(), new Map()], native = [], traffic = [];
const contexts = listeners.map((handlers, index) => {
  const context = {location:{origin:'happyranch-app://app'}, crypto:{randomUUID:()=> 'document-token'},
    TextEncoder, MutationObserver:class {observe(){} disconnect(){}}, document:{},
    addEventListener:(name, callback)=> {if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(callback);}};
  context.window = context.top = context;
  context.webkit = {messageHandlers:{appBridge:{postMessage:raw=> {native.push(JSON.parse(raw)); return Promise.resolve('{}');}}}};
  return vm.createContext(context);
});
contexts.forEach((context, index)=> {
  context.postMessage = (data, origin)=> {
    traffic.push(data);
    contexts.forEach((target, targetIndex)=> {
      target.incomingData = data; target.incomingOrigin = origin;
      vm.runInContext('for (const fn of messageCallbacks) fn({data:incomingData,origin:incomingOrigin,source:window})', target);
    });
  };
  context.messageCallbacks = listeners[index].get('message') || [];
});
vm.runInContext(sources.page, contexts[0]);
vm.runInContext(sources.relay, contexts[1]);
contexts.forEach((context,index)=> context.messageCallbacks = listeners[index].get('message'));
vm.runInContext(`globalThis.states=[];globalThis.dialogs=[];
 try {
 globalThis.offState=happyranchConnection.on('app.stateChanged', raw=>states.push(raw));
 globalThis.offDialog=happyranchConnection.on('app.dialogRequested', raw=>dialogs.push(raw));
 } catch(error) {globalThis.subscriptionError=error;}
 assertUnknown = (()=>{try {happyranchConnection.on('app.unknown',()=>{});return false;}catch{return true;}})();`, contexts[0]);
assert.equal(contexts[0].subscriptionError, undefined, 'page facade must accept dialog subscriptions');
assert.equal(contexts[0].assertUnknown, true);
assert.equal(native.length, 0, 'subscriptions await document admission');
vm.runInContext(`hrAppBridge.emit('app.dialogRequested','premature');hrAppBridge.admit(hrAppBridge.token);`, contexts[1]);
assert.equal(JSON.stringify(contexts[0].dialogs), '[]');
assert.deepEqual(native.map(item=>item.event), ['app.stateChanged','app.dialogRequested']);
vm.runInContext(`hrAppBridge.emit('app.stateChanged','state');hrAppBridge.emit('app.dialogRequested','dialog');hrAppBridge.emit('app.unknown','wrong');`, contexts[1]);
assert.equal(JSON.stringify(contexts[0].states), '["state"]');
assert.equal(JSON.stringify(contexts[0].dialogs), '["dialog"]');
vm.runInContext(`offState();globalThis.other=happyranchConnection.on('app.dialogRequested', raw=>dialogs.push('other:'+raw));offDialog();`, contexts[0]);
assert.equal(native.at(-1).enabled, true, 'one remaining dialog listener keeps its subscription');
vm.runInContext(`hrAppBridge.emit('app.dialogRequested','again');`, contexts[1]);
assert.equal(JSON.stringify(contexts[0].dialogs), '["dialog","other:again"]');
vm.runInContext(`other();`, contexts[0]);
assert.deepEqual(native.at(-1), {token:'document-token',event:'app.dialogRequested',enabled:false});
vm.runInContext(`hrAppBridge.emit('app.dialogRequested','dropped');`, contexts[1]);
assert.equal(JSON.stringify(contexts[0].dialogs), '["dialog","other:again"]');
const before = native.length;
vm.runInContext(`postMessage({channel:'hr-app-subscribe',event:'app.unknown',enabled:true},location.origin);`, contexts[0]);
assert.equal(native.length, before);
for (const fn of listeners[0].get('pagehide')) fn();
vm.runInContext(`hrAppBridge.emit('app.dialogRequested','retired');`, contexts[1]);
assert.equal(JSON.stringify(contexts[0].dialogs), '["dialog","other:again"]');
console.log('page/relay: closed event names, admission, independent unsubscribe, drop, teardown OK');
'''
        result = subprocess.run(["node", "-e", script], env={**os.environ, "HR_EVENT_SCRIPTS": json.dumps(
            {"page": native.APP_PAGE, "relay": native.APP_RELAY})}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LocaleBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.config = Path(self.scratch.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config), "LANG": "en_US.UTF-8"})
        env.start()
        self.addCleanup(env.stop)
        spec = importlib.util.spec_from_file_location("locale_fixture", Path(native.__file__).with_name("app_locale.py"))
        self.store = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.store)
        dependency = patch.object(native, "_locale", self.store, create=True)
        dependency.start()
        self.addCleanup(dependency.stop)
        EventTests.setUp(self)
        self.app.subscribed = True
        self.results, self.trace, self.publications = [], [], []
        self.app.view.call_async_javascript_function = self.publish

    def publish(self, *args) -> None:
        payload = args[2].unpack()
        if payload.get("enabled", "true") == "true":
            self.delivered.append(payload)
            self.trace.append(("event", json.loads(payload["raw"])["locale"]["current"]))
        if args[-2] is not None:
            self.publications.append(lambda: args[-2](self.app.view, None, None))

    def call(self, raw: str, **extra: object) -> None:
        item = {"token": self.app.bridge_token, "command": "app.locale.set", "data": raw, **extra}
        value = SimpleNamespace(is_string=lambda: True, to_string=lambda: json.dumps(item), get_context=lambda: None)
        def result(raw: str) -> None:
            self.results.append(json.loads(raw))
            self.trace.append(("reply", self.results[-1]["locale"]["current"]))
        reply = SimpleNamespace(return_value=result, return_error_message=self.errors.append)
        self.app.bridge_message(self.app.controller, value, reply)

    def drain(self, count: int, release: bool = True) -> None:
        deadline = time.monotonic() + 3
        context = GLib.MainContext.default()
        while len(self.results) + len(self.errors) < count and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if release:
                while self.publications:
                    self.publications.pop(0)()
            time.sleep(.001)
        self.assertEqual(len(self.results) + len(self.errors), count, "locale call must settle")

    def test_set_acknowledges_store_snapshot_then_full_state(self) -> None:
        before = self.app.status()
        self.call('{"schema":2,"locale":"zh-CN"}')
        self.drain(1)
        self.assertEqual(self.errors, [], "valid locale choice must be admitted")
        self.assertEqual(self.results, [{"schema": 2, "locale": {"current": "zh-CN", "persisted": True}}])
        self.assertEqual((self.config / "happyranch/locale").read_bytes(), b"zh-CN\n")
        expected = {**before, "locale": self.results[0]["locale"]}
        self.assertEqual(self.app.status(), expected)
        self.assertEqual(json.loads(self.delivered[-1]["raw"]), expected)
        self.assertEqual(self.trace, [("reply", "zh-CN"), ("event", "zh-CN")])

    def test_write_failure_stays_live_and_publishes_session_only(self) -> None:
        self.store.set("en")
        with patch.object(self.store.os, "replace", side_effect=OSError("synthetic write failure")):
            self.call('{"schema":2,"locale":"zh-CN"}')
            self.drain(1)
        self.assertEqual(self.errors, [], "write failure must resolve session-only, not reject")
        expected = {"current": "zh-CN", "persisted": False}
        self.assertEqual(self.results, [{"schema": 2, "locale": expected}])
        self.assertEqual(self.store.current(), expected)
        self.assertEqual(self.app.status()["locale"], expected)
        self.assertEqual(json.loads(self.delivered[-1]["raw"])["locale"], expected)
        self.assertEqual((self.config / "happyranch/locale").read_bytes(), b"en\n")

    def test_rapid_sets_serialize_write_reply_publication_before_next_write(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = self.store.set
        def slow(value: str) -> dict:
            self.trace.append(("write", value))
            if value == "zh-CN":
                entered.set()
                self.assertTrue(release.wait(3))
            return original(value)
        with patch.object(self.store, "set", side_effect=slow):
            self.call('{"schema":2,"locale":"zh-CN"}')
            self.call('{"schema":2,"locale":"en"}')
            try:
                self.assertTrue(entered.wait(1), "first admitted choice must reach store")
                self.app.emit_state()  # A concurrent health/target observation cannot jump the reply.
                self.assertEqual(self.trace, [("write", "zh-CN")])
            finally:
                release.set()
            self.drain(1, release=False)
            self.assertEqual(self.trace, [("write", "zh-CN"), ("reply", "zh-CN"), ("event", "zh-CN")],
                             "next write waits for publication delivery, not thread scheduling")
            self.publications.pop(0)()
            self.drain(2)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.trace, [("write", "zh-CN"), ("reply", "zh-CN"), ("event", "zh-CN"),
                                     ("write", "en"), ("reply", "en"), ("event", "en")])
        self.assertEqual(self.store.current(), {"current": "en", "persisted": True})
        self.assertEqual((self.config / "happyranch/locale").read_bytes(), b"en\n")
        self.assertEqual(json.loads(self.delivered[-1]["raw"])["locale"], self.store.current())

    def test_invalid_payloads_refuse_without_store_disk_state_or_event_effects(self) -> None:
        self.store.set("en")
        before, saved = self.app.status(), (self.config / "happyranch/locale").stat()
        with patch.object(self.store, "set", side_effect=AssertionError("invalid input called store")) as setter:
            for raw in ('{}', '[]', 'null', '{', '{"schema":2,"locale":"fr"}',
                        '{"schema":true,"locale":"en"}', '{"schema":2.0,"locale":"en"}',
                        '{"schema":2,"locale":"en","extra":0}', '{"locale":"en"}',
                        '{"schema":2,"locale":"en","locale":"zh-CN"}',
                        '{"schema":2,"schema":2,"locale":"en"}'):
                with self.subTest(raw=raw):
                    self.call(raw)
                    self.assertEqual(json.loads(self.errors[-1]),
                        {"code": "invalidRequest", "effect": "none", "recovery": "none"})
            self.call('{"schema":2,"locale":"zh-CN"}', extra=True)
            self.assertEqual(json.loads(self.errors[-1])["recovery"], "none")
            setter.assert_not_called()
        self.assertEqual(self.app.status(), before)
        self.assertEqual((self.config / "happyranch/locale").stat(), saved)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.results, [])

    def test_retired_document_drops_queued_choice_and_events(self) -> None:
        self.call('{"schema":2,"locale":"zh-CN"}')
        self.call('{"schema":2,"locale":"en"}')
        self.app.invalidate_document()
        self.drain(2)
        self.assertEqual(self.results, [])
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.store.current(), {"current": "zh-CN", "persisted": True})
        self.assertEqual([json.loads(raw)["code"] for raw in self.errors], ["documentChanged"] * 2)

    def test_recreated_main_cannot_overtake_a_retired_main_write(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = self.store.set
        def slow(value: str) -> dict:
            self.trace.append(("write", value))
            if value == "zh-CN":
                entered.set()
                self.assertTrue(release.wait(3))
            return original(value)
        with patch.object(self.store, "set", side_effect=slow):
            self.call('{"schema":2,"locale":"zh-CN"}')
            try:
                self.assertTrue(entered.wait(1), "first admitted choice must reach store")
                old = self.app
                old.invalidate_document()
                replacement = native.AppScheme.__new__(native.AppScheme)
                replacement.daemon, replacement.closed = None, False
                replacement.initialize_state()
                replacement.controller, replacement.JSC, replacement.GLib = old.controller, old.JSC, old.GLib
                replacement.view, replacement.bridge_token, replacement.subscribed = old.view, "replacement", True
                self.app = replacement
                self.call('{"schema":2,"locale":"en"}')
                self.assertEqual(self.trace, [("write", "zh-CN")])
            finally:
                release.set()
            self.drain(2)
        self.assertEqual(self.trace, [("write", "zh-CN"), ("write", "en"), ("reply", "en"), ("event", "en")])
        self.assertEqual(self.store.current(), {"current": "en", "persisted": True})
        self.assertEqual(json.loads(self.errors[0])["effect"], "unknown")


if __name__ == "__main__":
    unittest.main()
