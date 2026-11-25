"""Closed Main event boundary: native replies and the shipped page/relay scripts."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gi.repository import GLib
import app_scheme_webkit as native
import happyranch_window as launcher


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

    def test_relaunch_is_closed_and_only_requests_existing_lifecycle(self):
        calls, replies = [], []
        self.app.relaunch = lambda: calls.append("relaunch")
        self.reply.return_value = replies.append
        def command(data="{}", **extra):
            item = {"token": self.app.bridge_token, "command": "app.relaunch", "data": data, **extra}
            value = SimpleNamespace(is_string=lambda: True, to_string=lambda: json.dumps(item), get_context=lambda: None)
            self.app.bridge_message(self.app.controller, value, self.reply)
        for data in ('{"extra":true}', '[]', 'null', '{', '{"x":1,"x":2}'):
            command(data)
        command(extra=True)
        self.assertEqual(calls, [], "invalid payloads/envelopes must have no lifecycle effect")
        self.assertEqual(len(self.errors), 6)
        command()
        self.assertEqual(calls, ["relaunch"])
        self.assertEqual(json.loads(replies[-1]), {})
        self.assertIs(self.app.status()["app_relaunch"], True)
        self.subscribe("app.stateChanged")
        self.assertIs(json.loads(self.delivered[-1]["raw"])["app_relaunch"], True)
        self.connection_boundary("relaunch")

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
 if(sources.case==='relaunch'){
  const before=native.length;
  await vm.runInContext("happyranchConnection.call('app.relaunch','{}').catch(()=>{})",contexts[0]);
  assert.equal(native.length,before+1,'Relaunch must cross page and named-world command admission');
  assert.equal(native.at(-1).command,'app.relaunch');
  for(const args of ['{"extra":true}', '[]', 'null', '{']){
   contexts[0].args=args;const count=native.length;
   await vm.runInContext("happyranchConnection.call('app.relaunch',args).catch(()=>{})",contexts[0]);
   assert.equal(native.length,count,'closed page payload rejects extras before dispatch');
  }
  return;
 }
 if(sources.case==='local'){
  for(const command of ['local.status','local.start','local.stop','local.restart','local.keepRunning.set','local.repair','local.turnOff','local.update.status']){
   for(const args of ['{}','{','{"schema":2,"enabled":false,"enabled":true}']){
    contexts[0].command=command;contexts[0].args=args;
    const before=native.length;
    await vm.runInContext("happyranchConnection.call(command,args).catch(()=>{})",contexts[0]);
    assert.equal(native.length,before+1,'each local command must reach native closed parsing');
    assert.equal(native.at(-1).data,args,'preserve duplicate/malformed argument bytes');
   }
  }
  return;
 }
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
 for(const category of ['residue','owner','provenance','custody','inventory','quiescence','timeout','unknown',null,'/private/canary']){
  contexts[1].failure={code:'hostStateUnrecognized',effect:'none',recovery:'refresh',category,private:'secret'};
  const error=await vm.runInContext("happyranchConnection.call('host.status','{}').catch(raw=>JSON.parse(raw))",contexts[0]);
  assert.deepEqual(JSON.parse(JSON.stringify(error)),{code:'hostStateUnrecognized',effect:'none',recovery:'refresh',
   category:!category || category.startsWith('/') ? 'unknown' : category});
 }
 for(const code of ['permissionDenied','owner_edits_pending','outcomeUnknown','hostStateUnrecognized']){
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

    def test_local_page_relay_admission_and_retired_effect_semantics(self):
        self.connection_boundary('local')
        for command in ('local.start', 'local.stop', 'local.restart', 'local.keepRunning.set',
                        'local.repair', 'local.turnOff'):
            self.connection_boundary('retirement-pagehide', command)

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


class LocalBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        EventTests.setUp(self)
        self.calls, self.results = [], []
        self.reply.return_value = self.results.append
        self.app.local_dispatch = lambda *args: self.calls.append(args)

    def call(self, command: str, data: str = "{}", **extra: object) -> None:
        item = {"token": self.app.bridge_token, "command": command, "data": data, **extra}
        value = SimpleNamespace(is_string=lambda: True, to_string=lambda: json.dumps(item), get_context=lambda: None)
        self.app.bridge_message(self.app.controller, value, self.reply)

    def test_closed_local_native_admission_and_retired_replies(self) -> None:
        for command in ("local.status", "local.start", "local.stop", "local.restart", "local.repair", "local.turnOff", "local.update.status"):
            self.call(command)
            self.assertEqual(self.errors, [], "admitted local commands must reach the supervisor")
            self.assertEqual(self.calls[-1][:2], (command, {}))
        self.call("local.keepRunning.set", '{"schema":2,"enabled":true}')
        self.assertEqual(self.calls[-1][:2], ("local.keepRunning.set", {"schema": 2, "enabled": True}))
        count = len(self.calls)
        for command, data in (("local.status", "[]"), ("local.restart", '{"host":"Studio"}'),
                ("local.stop", '{"x":1,"x":2}'), ("local.start", '{'),
                ("local.keepRunning.set", '{}'), ("local.keepRunning.set", '{"schema":2,"enabled":1}'),
                ("local.keepRunning.set", '{"schema":true,"enabled":true}'),
                ("local.keepRunning.set", '{"schema":2,"enabled":true,"enabled":false}'),
                ("local.keepRunning.set", '{"schema":2,"enabled":true,"target":"Studio"}')):
            self.call(command, data)
            self.assertEqual(json.loads(self.errors[-1]), {"code": "invalidRequest", "effect": "none", "recovery": "none"})
        self.assertEqual(len(self.calls), count)
        self.call("local.restart", token="stale")
        self.uri = "https://app/"
        self.call("local.restart")
        self.uri = native.ORIGIN + "/api/v1/health"
        self.call("local.restart")
        self.assertEqual(len(self.calls), count)
        self.uri = native.ORIGIN + "/"
        self.call("local.restart")
        finish = self.calls[-1][2]
        self.app.bridge_token = "replacement"
        finish({"schema": 2})
        self.assertEqual(json.loads(self.errors[-1]), {"code": "documentChanged", "effect": "unknown", "recovery": "nativeRecovery"})

    def test_local_interruption_reuses_fail_closed_owner_and_effect_gate(self) -> None:
        requests = queue.Queue()
        controls = launcher.LocalControls(SimpleNamespace(), Path("/unused"), requests,
            threading.Event(), lambda fn, *args: fn(*args), lambda *_args: True, lambda *_args: None)
        self.app.local_dispatch = controls.request
        for summary, expected in ((None, "owner_save_unknown"),
                ({"counts": {"pending": 1}}, "owner_save_pending"),
                ({"counts": {"unknown": 1}}, "owner_save_unknown"),
                ({"counts": {"dirty": 1}}, "owner_edits_pending"),
                ({"counts": {}}, None)):
            self.errors.clear()
            self.app.owners = summary
            self.call("local.restart")
            if expected:
                self.assertTrue(self.errors, "uncertain owners must refuse the local operation")
                self.assertEqual(json.loads(self.errors[-1]), {"code": expected, "effect": "none", "recovery": "refresh"})
                self.assertTrue(requests.empty())
            else:
                self.assertEqual(self.errors, [], "clean owners may enqueue the operation")
                self.assertEqual(requests.qsize(), 1)
        self.app.transfers["pending"] = SimpleNamespace(generation=self.app.generation, finished=False, method="POST")
        controls.process(requests.get_nowait())
        self.assertEqual(json.loads(self.errors[-1]), {"code": "non_get_pending", "effect": "none", "recovery": "refresh"})

    def test_interruption_fences_status_worker_before_bootstrap(self) -> None:
        for interruption in ("starting", "unknown"):
            with self.subTest(interruption=interruption):
                seen = []
                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self) -> None:
                        seen.append(self.path)
                        body = json.dumps({"api_contract": {"current": 1, "min_client": 1}}
                            if self.path == "/api/v1/health" else {"token": "synthetic-after-effect"}).encode()
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    def log_message(self, *_args: object) -> None:
                        pass
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
                server_thread.start()
                local = self.app.daemon = self.app.local_daemon = native.LocalDaemon(server.server_port)
                self.app.local["running"], self.app.local_changing = "running", False
                self.app.status_refresh = None
                self.app.subscribed = True
                self.delivered.clear()
                bootstrap = local.bootstrap
                arrived, release = threading.Event(), threading.Event()
                def delayed(stop: threading.Event | None = None) -> None:
                    arrived.set()
                    self.assertTrue(release.wait(3))
                    bootstrap(stop)
                local.bootstrap = delayed
                try:
                    self.app.refresh_status()
                    self.assertTrue(arrived.wait(3))
                    worker = next(t for t in threading.enumerate() if t.name == "app-status-admission")
                    self.app.local_service_changed(interruption)
                    # Reads still resolve while the old worker is paused.
                    self.call("app.status")
                    self.assertEqual(json.loads(self.results[-1])["local"]["running"], interruption)
                    release.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    context = GLib.MainContext.default()
                    while context.pending():
                        context.iteration(False)
                    self.assertIsNone(local._bearer, "pre-effect worker must not cache the old service bearer")
                    self.assertNotIn("ready", [json.loads(x["raw"])["state"] for x in self.delivered])
                    self.assertEqual(seen, [])
                    local.bootstrap = bootstrap
                    self.app.local_service_changed("running", f"http://127.0.0.1:{server.server_port}/", True)
                    self.assertEqual(self.app.status()["state"], "ready")
                    self.assertEqual(seen, ["/api/v1/health", "/api/v1/auth/bootstrap"])
                    self.app.refresh_status()
                    for thread in threading.enumerate():
                        if thread.name == "app-status-admission":
                            thread.join(3)
                    while context.pending():
                        context.iteration(False)
                    self.assertIsNone(self.app.status_refresh)
                finally:
                    release.set()
                    local.bootstrap = bootstrap
                    server.shutdown()
                    server_thread.join(3)
                    server.server_close()

    def test_local_action_readmission_belongs_to_this_computer(self) -> None:
        contract = {"current": 1, "min_client": 1}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if contract.get("unreachable") and self.path == "/api/v1/auth/bootstrap":
                    self.send_error(503)
                    return
                body = json.dumps({"api_contract": contract} if self.path == "/api/v1/health"
                                  else {"token": "synthetic-readmission"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args: object) -> None:
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/"
        local = self.app.local_daemon = native.LocalDaemon(server.server_port)
        remote = native.RemoteDaemon(None, ready=True, target_id="synthetic-remote", name="Studio Host")
        remote.state = "ready"
        running, effects, payloads = [True], [], []
        def effect() -> str:
            running[0] = True
            effects.append("start/restart")
            return url
        service = SimpleNamespace(start=lambda _cancel: effect(), restart=lambda _cancel: effect(),
            state=lambda: {"ActiveState": "active" if running[0] else "inactive"},
            check_legacy=lambda: None, validate_environment=lambda: None, refuse_external=lambda _pid: None,
            pid=lambda _state: 42 if running[0] else 0,
            verified_port=lambda _state: server.server_port if running[0] else None,
            lifetime_state=lambda: "off", current_port=lambda: server.server_port,
            close=lambda: running.__setitem__(0, False), set_keep_running=lambda _enabled: None)
        requests = queue.Queue()
        controls = launcher.LocalControls(service, Path("/unused"), requests, threading.Event(),
            lambda fn, *args: fn(*args), lambda *_args: True, self.app.local_service_changed)
        self.app.local_dispatch = controls.request
        self.app.owners = {"counts": {}}
        for target in (local, remote):
            self.app.daemon = target
            for command in ("local.start", "local.restart"):
                for state in ("ready", "incompatible", "unreachable", "unknown"):
                    with self.subTest(target=target is remote, command=command, state=state):
                        contract.clear()
                        contract.update(current=2 if state == "incompatible" else 1,
                                        min_client=2 if state == "incompatible" else 1)
                        if state == "unreachable":
                            contract["unreachable"] = True
                        original = local.bootstrap
                        if state == "unknown":
                            # Cancellation leaves admission unsettled, without replaying the service effect.
                            def cancelled(stop: threading.Event | None = None) -> None:
                                raise native.SchemeError(499, "cancelled")
                            local.bootstrap = cancelled
                        try:
                            before = len(effects)
                            self.call(command)
                            controls.process(requests.get_nowait())
                            result = json.loads(self.results[-1])
                            payloads.append(result)
                            self.assertEqual(len(effects), before + 1)
                            self.assertEqual(set(result), {"schema", "command", "service", "readmission"})
                            self.assertEqual(result["readmission"]["state"], state)
                            mismatch = result["readmission"]["incompatible"]
                            if state == "incompatible":
                                self.assertEqual(mismatch, {"side": "app", "host_contract": {"current": 2, "min_client": 2},
                                    "client_contract": 1, "min_daemon": 1})
                            else:
                                self.assertIsNone(mismatch)
                            status = self.app.status()
                            if target is remote:
                                self.assertEqual((status["target"]["id"], status["state"], status["incompatible"]),
                                                 ("synthetic-remote", "ready", None))
                            else:
                                self.assertEqual(status["state"], "target_starting" if state == "unknown" else state)
                                self.assertEqual(status["incompatible"], mismatch)
                        finally:
                            local.bootstrap = original
        for command in ("local.stop", "local.keepRunning.set", "local.repair", "local.turnOff"):
            with self.subTest(command=command):
                self.call(command, '{"schema":2,"enabled":false}' if command == "local.keepRunning.set" else "{}")
                controls.process(requests.get_nowait())
                result = json.loads(self.results[-1])
                payloads.append(result)
                self.assertEqual(set(result), {"schema", "command", "service"})
                self.assertEqual(result["command"], command)
        # Use the repository's existing Ajv schema venue (native-owners.test.mjs),
        # validating shipping callback payloads, not a test-side schema copy.
        root = Path(__file__).resolve().parents[3]
        check = subprocess.run(["node", "-e", r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const Ajv = require('./web/node_modules/ajv');
const schema = JSON.parse(fs.readFileSync('protocol/native-connection-bridge.v2.json', 'utf8').replaceAll('#/$defs/', '#/definitions/'));
schema.definitions = schema.$defs; delete schema.$defs; delete schema.$schema;
const ajv = new Ajv({allErrors:true}); ajv.addSchema(schema, 'bridge');
const valid = ajv.getSchema('bridge#/definitions/localActionResult');
const root = ajv.getSchema('bridge');
const payloads = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const result of payloads) {
  assert.ok(valid(result), JSON.stringify(valid.errors)); assert.ok(root(result), JSON.stringify(root.errors));
  const invalid = structuredClone(result);
  if (invalid.readmission) delete invalid.readmission;
  else invalid.readmission = {state:'ready', incompatible:null};
  assert.equal(valid(invalid), false, 'required for Start/Restart; forbidden for other commands');
}
for (const readmission of [
  {state:'ready', incompatible:{side:'app', host_contract:null, client_contract:1, min_daemon:1}},
  {state:'incompatible', incompatible:null}, {state:'busy', incompatible:null},
  {state:'ready', incompatible:null, url:'http://127.0.0.1:1234/'},
]) assert.equal(valid({...payloads[0], readmission}), false);
const section = fs.readFileSync('docs/specs/2026-11-17-web-native-bridge.md', 'utf8')
  .split('### v2 local service commands')[1].split('### v2 app locale setter')[0];
for (const match of section.matchAll(/#### (Valid|Invalid):[^\n]*\n\n```json\n([^]*?)\n```/g)) {
  assert.equal(root(JSON.parse(match[2])), match[1] === 'Valid', match[0]);
}
console.log(`Validated ${payloads.length} native localActionResult payloads and required/forbidden/closed negatives`);
"""], cwd=root, input=json.dumps(payloads), capture_output=True, text=True, timeout=20)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_service_readmission_uses_real_loopback_and_preserves_main_remote_and_owners(self) -> None:
        seen = []
        contract = {"current": 1, "min_client": 1}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append(self.path)
                body = json.dumps({"api_contract": contract} if self.path == "/api/v1/health"
                                  else {"token": "synthetic-refreshed-local"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args: object) -> None:
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        local = self.app.local_daemon = native.LocalDaemon(server.server_port)
        url = f"http://127.0.0.1:{server.server_port}/"
        remote = native.RemoteDaemon(None, ready=True, target_id="remote-test", name="Studio Host")
        remote.state = "ready"
        self.app.owners = {"counts": {}}
        self.app.candidate = {"kind": "remote", "id": "candidate", "name": "Candidate Host",
                              "phase": "ready_to_switch", "deadline": None}
        self.app.subscribed = True
        for target in (local, remote):
            self.app.daemon = target
            before = (self.app.view, self.app.document, self.app.generation, self.app.owners, self.app.candidate)
            local._bearer, local._compatibility, local.state = "synthetic-old-local", "", "ready"
            seen.clear()
            self.app.local_service_changed("starting")
            self.assertIsNone(local._bearer)
            self.app.local_service_changed("running", url, True)
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertEqual(seen, ["/api/v1/health", "/api/v1/auth/bootstrap"])
            self.assertEqual(local._bearer, "synthetic-refreshed-local")
            self.assertEqual(local.state, "ready")
            self.assertIs(self.app.daemon, target)
            self.assertEqual(self.delivered[-1]["event"], "app.stateChanged")
            self.assertEqual(json.loads(self.delivered[-1]["raw"])["target"]["id"],
                             "local" if target is local else "remote-test")
            self.assertEqual((self.app.view, self.app.document, self.app.generation, self.app.owners, self.app.candidate), before)
        self.app.candidate = None
        self.app.daemon = local
        contract.update(current=2, min_client=2)
        self.app.local_service_changed("starting")
        self.app.local_service_changed("running", url, True)
        self.assertEqual(self.app.status()["incompatible"]["side"], "app")
        self.assertIsNone(local._bearer)


if __name__ == "__main__":
    unittest.main()
