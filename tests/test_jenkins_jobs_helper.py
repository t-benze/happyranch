"""Real helper-seam fake HTTP/CLI regression cases for PR852."""
from __future__ import annotations
import base64, importlib.util, json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

HELPER=Path(__file__).parents[1]/"scripts"/"jenkins_jobs.py"
spec=importlib.util.spec_from_file_location("jj",HELPER);assert spec and spec.loader
jj=importlib.util.module_from_spec(spec);sys.modules["jj"]=jj;spec.loader.exec_module(jj)
class Fake(BaseHTTPRequestHandler):
    seen:list[tuple[str,str,str,bytes]]=[]; states:dict[str,object]={}
    def log_message(self,*a:object)->None:pass
    def reply(self,status:int,body:object=b"",headers:dict[str,str]|None=None)->None:
        b=body if isinstance(body,bytes) else json.dumps(body).encode();self.send_response(status)
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self)->None:
        self.seen.append(("GET",self.path,self.headers.get("Authorization",""),b""))
        if self.states.get("auth") and not self.headers.get("Authorization","").startswith("Basic "):return self.reply(401)
        if self.path.endswith("/api/json") and "/queue/" not in self.path:
            builds=self.states.get("builds")
            is_build=self.path.rstrip("/").split("/")[-3].isdigit()
            if isinstance(builds,list) and builds and is_build:
                value=builds.pop(0)
                value.setdefault("number",1);value.setdefault("url",f"http://{self.headers['Host']}"+self.path.removesuffix("api/json"))
                return self.reply(200,value)
            url=self.states.get("url",f"http://{self.headers['Host']}"+self.path.removesuffix("api/json"))
            return self.reply(200,{"_class":self.states.get("class","hudson.model.FreeStyleProject"),"number":self.states.get("number",1),"url":url,"property":self.states.get("properties",[]),"building":False,"result":self.states.get("result","SUCCESS"),"artifacts":self.states.get("artifacts",[])})
        if "/queue/item/" in self.path:
            queues=self.states.get("queues")
            if isinstance(queues,list) and queues:return self.reply(200,queues.pop(0))
            if self.states.get("queue_404"):return self.reply(404)
            return self.reply(200,self.states.get("queue",{"executable":{"number":1}}))
        if self.path.endswith("consoleText"):return self.reply(200,self.states.get("log",b"log"))
        if "/artifact/" in self.path:return self.reply(self.states.get("artifact_status",200),self.states.get("artifact",b"bytes"))
        return self.reply(404)
    def do_POST(self)->None:
        body=self.rfile.read(int(self.headers.get("Content-Length","0")))
        self.seen.append(("POST",self.path,self.headers.get("Authorization",""),body))
        if self.path.endswith("/build") or self.path.endswith("/buildWithParameters"):
            if self.states.get("submit_status"):return self.reply(int(self.states["submit_status"]),b"",{"Location":str(self.states.get("location",""))})
            return self.reply(201,b"",{"Location":str(self.states.get("location","/jenkins/queue/item/7/"))})
        if self.path.endswith("/queue/cancelItem"):self.states["queue"]={"cancelled":True};return self.reply(200)
        if self.path.endswith("/stop"):self.states["result"]="ABORTED";return self.reply(200)
        return self.reply(404)
@pytest.fixture
def fake() -> tuple[str,Fake]:
    Fake.seen=[];Fake.states={};s=ThreadingHTTPServer(("127.0.0.1",0),Fake);t=threading.Thread(target=s.serve_forever);t.start()
    try:yield f"http://127.0.0.1:{s.server_port}/jenkins",Fake
    finally:s.shutdown();t.join();s.server_close()
def cli(base:str,p:Path,*args:str,env:dict[str,str]|None=None)->subprocess.CompletedProcess[str]:
    e=os.environ.copy();e.update(env or {});return subprocess.run([sys.executable,str(HELPER),"--controller",base,"--allow-http","--job","folder/demo","--receipt",str(p),*args],capture_output=True,text=True,env=e,timeout=5)
def terminal(p:Path,base:str)->None:
    x=jj.open_receipt(p,base,"folder/demo");x.update(phase="terminal",build_number=1,jenkins_result="SUCCESS");jj.save_receipt(p,x)
def test_01_plain_submit_auth(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["auth"]=True;r=cli(b,tmp_path/"r","submit",env={"JENKINS_USERNAME":"u","JENKINS_API_TOKEN":"t"})
    assert r.returncode==0
    assert [x[1] for x in f.seen if x[0]=="POST"]==["/jenkins/job/folder/job/demo/build"]
    assert all(x[2]=="Basic "+base64.b64encode(b"u:t").decode() for x in f.seen)
def test_02_parameterized_declared_only(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["properties"]=[{"parameterDefinitions":[{"name":"branch","type":"StringParameterDefinition"}]}]
    assert cli(b,tmp_path/"r","submit","--parameter","branch=a b&c").returncode==0
    posts=[x for x in f.seen if x[0]=="POST"]
    assert len(posts)==1 and posts[0][1].endswith("/buildWithParameters") and posts[0][3]==b"branch=a+b%26c"
    f.seen=[];assert cli(b,tmp_path/"x","submit","--parameter","no=x").returncode==3;assert not [x for x in f.seen if x[0]=="POST"]
    f.states["properties"]=[{"parameterDefinitions":[{"name":"bad","type":"UnsupportedParameterDefinition"}]}];f.seen=[]
    assert cli(b,tmp_path/"y","submit","--parameter","bad=x").returncode==3;assert not [x for x in f.seen if x[0]=="POST"]
def test_03_context_folder_and_identity_refusal()->None:
    assert jj.job_path("a b/c")=="/job/a%20b/job/c"
    c=jj.Controller("https://x.test/jenkins")
    for u in ("https://x.test/jenkins/job/a/lastBuild","https://x.test/jenkins/job/a/1/%2e%2e/config","https://x.test/jenkins/job/a/job/b/2"):
        with pytest.raises(jj.ValidationError):c.checked(u,"build","a",1)
def test_04_queue_build_success_collect(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["artifacts"]=[{"relativePath":"a.txt"}];f.states["queues"]=[{"executable":{"number":1,"url":b+"/job/folder/job/demo/1/"}}];f.states["builds"]=[{"building":True},{"building":False,"result":"SUCCESS","artifacts":[{"relativePath":"a.txt"}]}]
    p=tmp_path/"r";assert cli(b,p,"submit").returncode==0;assert cli(b,p,"--poll",".01","wait").returncode==0;assert cli(b,p,"collect","--output",str(tmp_path/"o")).returncode==0
    assert (tmp_path/"o"/"a.txt").read_bytes()==b"bytes";x=json.loads(p.read_text());assert x["build_number"]==1 and x["jenkins_result"]=="SUCCESS"
@pytest.mark.parametrize("result",["FAILURE","UNSTABLE","ABORTED"])
def test_05_terminal_results(fake:tuple[str,Fake],tmp_path:Path,result:str)->None:
    b,f=fake;f.states["result"]=result;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="building",build_number=1);jj.save_receipt(p,x)
    result_run=cli(b,p,"wait");assert result_run.returncode==2
    assert json.loads(p.read_text())["jenkins_result"]==result and json.loads(result_run.stdout)["jenkins_result"]==result
def test_06_deadline_and_nonfinite(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="building",build_number=1);jj.save_receipt(p,x);assert cli(b,p,"--deadline","nan","show").returncode==3
    # A response which continuously makes progress must still consume the one
    # absolute operation budget; socket inactivity timeouts are insufficient.
    f.states["builds"]=[{"building":True}]
    original=Fake.reply; sent: list[int]=[]
    def dribble(self:Fake,status:int,body:object=b"",headers:dict[str,str]|None=None)->None:
        if self.path.endswith("/api/json") and "/queue/" not in self.path:
            raw=json.dumps({"building":True}).encode();self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers()
            for byte in raw:
                try:self.wfile.write(bytes([byte]));self.wfile.flush()
                except OSError:break
                sent.append(1)
                time.sleep(.008)
            return
        original(self,status,body,headers)
    Fake.reply=dribble
    try:
        r=cli(b,p,"--deadline",".04","--timeout","1","wait")
        # The subprocess startup cost is host-dependent.  The server-side
        # observation is not: a 17-byte stream at 8ms/byte is still incomplete
        # when the helper returns, proving the absolute deadline stopped a
        # continuously progressing response rather than an inactivity timer.
        assert r.returncode==3 and len(sent)<len(json.dumps({"building":True}).encode())
        assert json.loads(p.read_text())["build_number"]==1
    finally:Fake.reply=original
def test_06b_deadline_also_bounds_dribbling_http_error(fake:tuple[str,Fake],tmp_path:Path)->None:
    """The HTTPError body is a stream too, so it cannot evade the budget."""
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="building",build_number=1);jj.save_receipt(p,x)
    original=Fake.reply; sent: list[int]=[]
    def slow_error(self:Fake,status:int,body:object=b"",headers:dict[str,str]|None=None)->None:
        if self.path.endswith("/api/json") and "/queue/" not in self.path:
            raw=b"x"*64;self.send_response(500);self.send_header("Content-Length",str(len(raw)));self.end_headers()
            for byte in raw:
                try:self.wfile.write(bytes([byte]));self.wfile.flush()
                except OSError:break
                sent.append(1)
                time.sleep(.008)
            return
        original(self,status,body,headers)
    Fake.reply=slow_error
    try:
        r=cli(b,p,"--deadline",".04","--timeout","1","wait")
        assert r.returncode==3 and len(sent)<64
        receipt=json.loads(p.read_text())
        assert receipt["build_number"]==1 and receipt["jenkins_result"] is None
        assert receipt["local_error"]=="deadline"
    finally:Fake.reply=original
def test_07_uncertain_and_reuse(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["properties"]=[];p=tmp_path/"r";jj.open_receipt(p,b,"folder/demo");assert cli(b,p,"submit").returncode==3;assert not [x for x in f.seen if x[0]=="POST"]
    for status,location in ((500,""),(201,""),(201,"https://elsewhere.invalid/queue/item/7/"),(201,"/jenkins/queue/item/7/?x=1")):
        f.seen=[];f.states.update(submit_status=status,location=location);q=tmp_path/f"u{status}{len(location)}";r=cli(b,q,"submit")
        assert r.returncode==3 and len([x for x in f.seen if x[0]=="POST"])==1
        assert cli(b,q,"submit").returncode==3 and len([x for x in f.seen if x[0]=="POST"])==1
    f.states.pop("submit_status",None);f.states.pop("location",None)
def test_08_reconcile_no_resubmit(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="submission_uncertain",queue_id=7);jj.save_receipt(p,x);f.states["queue_404"]=True
    assert cli(b,p,"wait").returncode==3
    assert any("/queue/item/7/" in seen[1] for seen in f.seen)
    assert cli(b,p,"reconcile","--build-number","1").returncode==0
    assert any(x[0]=="GET" and x[1].endswith("/job/folder/job/demo/1/api/json") for x in f.seen)
    assert not [x for x in f.seen if x[0]=="POST"] and json.loads(p.read_text())["build_number"]==1
def test_08b_reconcile_requires_complete_exact_observation(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake
    for key,value in (("number",2),("url",b+"/job/folder/job/other/1/"),("url",None)):
        p=tmp_path/f"r-{key}-{value is None}"
        x=jj.open_receipt(p,b,"folder/demo");x.update(phase="submission_uncertain");jj.save_receipt(p,x)
        f.states.clear();f.states[key]=value
        r=cli(b,p,"reconcile","--build-number","1")
        assert r.returncode==3
        saved=json.loads(p.read_text())
        assert saved["build_number"] is None and saved["phase"]=="submission_uncertain"
        assert not [seen for seen in f.seen if seen[0]=="POST"]
def test_09_cancel_queue_race(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="queued",queue_id=7);jj.save_receipt(p,x)
    f.states["queues"]=[{"executable":{"number":1,"url":b+"/job/folder/job/demo/1/"}}]
    assert cli(b,p,"--deadline","1","cancel").returncode==2
    paths=[x[1] for x in f.seen];assert "/jenkins/queue/cancelItem" in paths and "/jenkins/job/folder/job/demo/1/stop" in paths
    x=json.loads(p.read_text());assert x["build_number"]==1 and x["jenkins_result"]=="ABORTED"

def test_09b_two_process_lifecycle_contention_preserves_identity(fake:tuple[str,Fake],tmp_path:Path)->None:
    """A stale waiter/canceller must not erase an observed exact build identity."""
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="queued",queue_id=7);jj.save_receipt(p,x)
    f.states["queues"]=[{"executable":{"number":1,"url":b+"/job/folder/job/demo/1/"}},{"executable":{"number":1,"url":b+"/job/folder/job/demo/1/"}}]
    f.states["builds"]=[{"building":False,"result":"SUCCESS"},{"building":False,"result":"ABORTED"}]
    common=[sys.executable,str(HELPER),"--controller",b,"--allow-http","--job","folder/demo","--receipt",str(p),"--deadline","1","--poll",".001"]
    waiter=subprocess.Popen([*common,"wait"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    until=time.monotonic()+2
    while not f.seen and time.monotonic()<until:time.sleep(.001)
    canceller=subprocess.Popen([*common,"cancel"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    waiter.communicate(timeout=5);canceller.communicate(timeout=5)
    receipt=json.loads(p.read_text())
    assert waiter.returncode==canceller.returncode==0
    assert receipt["build_number"]==1 and receipt["phase"]=="terminal" and receipt["jenkins_result"]=="SUCCESS"
    assert not [x for x in f.seen if x[0]=="POST"]
    assert not [x for x in f.seen if x[0]=="POST" and x[1].endswith("/build")]
def test_10_two_server_refusal_no_forward(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;other_seen:list[str]=[]
    class Other(BaseHTTPRequestHandler):
        def log_message(self,*a:object)->None:pass
        def do_GET(self)->None:other_seen.append(self.headers.get("Authorization",""));self.send_response(200);self.end_headers()
    other=ThreadingHTTPServer(("127.0.0.1",0),Other);thread=threading.Thread(target=other.serve_forever);thread.start()
    try:
        p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="queued",queue_id=7);jj.save_receipt(p,x)
        f.states["queues"]=[{"executable":{"number":1,"url":f"http://127.0.0.1:{other.server_port}/job/folder/job/demo/1/"}}]
        r=cli(b,p,"wait",env={"JENKINS_USERNAME":"u","JENKINS_API_TOKEN":"real-secret"})
        assert r.returncode==3 and other_seen==[] and "real-secret" not in r.stderr
    finally:other.shutdown();thread.join();other.server_close()
def test_11_bounds_symlink_and_secret(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";terminal(p,b);d=tmp_path/"o";d.mkdir();(d/"x").symlink_to(tmp_path);f.states["artifacts"]=[{"relativePath":"x/escape"}]
    assert cli(b,p,"collect","--output",str(d)).returncode==2;assert not (tmp_path/"escape").exists()
    assert "real-secret" not in cli(b,tmp_path/"n","--token-env","BAD\nSECRET","show",env={"BAD\nSECRET":"real-secret"}).stderr
    # The root itself may be swapped after preflight; descriptor-relative
    # traversal must refuse rather than publish outside the output tree.
    safe=tmp_path/"safe";safe.mkdir();outside=tmp_path/"outside";outside.mkdir();root=tmp_path/"root";root.mkdir()
    original=jj.store
    def swap_store(path:Path,data:bytes)->None:
        root.rename(safe);root.symlink_to(outside,target_is_directory=True);original(path,data)
    jj.store=swap_store
    try:
        try:jj.store(jj.output_file(root,"a"),b"x")
        except (jj.JenkinsError,OSError,ValueError):pass
        assert not (outside/"a").exists()
    finally:jj.store=original
def test_11b_malformed_collection_persists_partial_observation(fake:tuple[str,Fake],tmp_path:Path)->None:
    """A malformed artifact list cannot erase already-observed console state."""
    b,f=fake;p=tmp_path/"r";terminal(p,b);f.states["artifacts"]=None
    r=cli(b,p,"collect","--output",str(tmp_path/"o"))
    assert r.returncode==3
    collection=json.loads(p.read_text())["collection"]
    assert collection["status"]=="partial"
    assert any(entry["path"]=="console.log" and entry["status"]=="downloaded" for entry in collection["artifacts"])
    assert any(entry.get("reason")=="malformed_metadata" for entry in collection["artifacts"])

def test_11c_subprocess_ancestor_swap_never_publishes_outside(tmp_path:Path)->None:
    """A real competing process swapping the output root cannot redirect writes.

    This deliberately uses the copied standalone helper in a second process,
    rather than a mocked Path method: the observable is that the adversarial
    directory remains empty even while the root name changes ownership.
    """
    root=tmp_path/"root";outside=tmp_path/"outside";root.mkdir();outside.mkdir()
    driver=tmp_path/"writer.py"
    driver.write_text(
        "import importlib.util,sys\n"
        "from pathlib import Path\n"
        f"s=importlib.util.spec_from_file_location('j',{str(HELPER)!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        "r=Path(sys.argv[1])\n"
        "for n in range(800):\n"
        " try:\n"
        "  m.store_artifact(r, f'part-{n}/payload', b'x')\n"
        " except (m.JenkinsError,OSError,ValueError): pass\n"
    )
    writer=subprocess.Popen([sys.executable,str(driver),str(root)])
    try:
        deadline=time.monotonic()+1.5
        generation=0
        while writer.poll() is None and time.monotonic()<deadline:
            try:
                if root.is_symlink():
                    root.unlink();root.mkdir()
                # Keep each displaced safe directory: the writer can create a
                # fresh root between these syscalls, which is exactly the
                # adversarial scheduling this probe is meant to exercise.
                parked=tmp_path/f"parked-{generation}";generation+=1
                root.rename(parked);root.symlink_to(outside,target_is_directory=True)
                time.sleep(.0005);root.unlink()
            except (FileNotFoundError,FileExistsError):
                pass
        writer.wait(timeout=5)
    finally:
        if writer.poll() is None:writer.kill();writer.wait()
    assert writer.returncode==0
    assert not list(outside.rglob("*"))
def test_12_copied_standalone_helper(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,_=fake;copy=tmp_path/"tool.py";copy.write_bytes(HELPER.read_bytes())
    r=subprocess.run([sys.executable,str(copy),"--controller",b,"--allow-http","--job","folder/demo","--receipt",str(tmp_path/"r"),"submit"],capture_output=True,text=True,cwd=tmp_path,timeout=5)
    assert r.returncode==0 and "runtime" not in copy.read_text()


# TASK7682 shipping reproductions, with complete valid build-identity fixtures.
import fcntl, tempfile, hashlib
residual_seen=[]; residual_mode='normal'; residual_qcount=0; residual_wire=0; residual_on_get=None
class residual_Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def send(self,data,status=200):
  global residual_wire
  b=data if isinstance(data,bytes) else json.dumps(data).encode()
  self.send_response(status);self.send_header('Content-Length',str(len(b)));self.end_headers()
  try:self.wfile.write(b);residual_wire+=len(b)
  except OSError:pass
 def do_GET(self):
  global residual_qcount,residual_on_get
  residual_seen.append(('GET',self.path))
  if residual_on_get:
   action=residual_on_get;residual_on_get=None;action()
  if '/queue/' in self.path:
   residual_qcount+=1
   if residual_mode=='expired':return self.send(b'',404)
   if residual_mode=='delayed_cancel' and residual_qcount==1:return self.send({})
   return self.send({'executable':{'number':1,'url':('http://evil.invalid/job/other/1/' if residual_mode=='wrong_queue' else residual_base+'/job/demo/1/')}})
  if self.path.endswith('consoleText'):return self.send(b'log')
  if '/artifact/' in self.path:return self.send(b'x'*20 if residual_mode=='aggregate' else b'bytes')
  if residual_mode=='slow':
   raw=json.dumps({'building':True}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
   for byte in raw:
    try:self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.008)
    except OSError:break
   return
  if residual_mode=='slow_headers':
   started=time.monotonic()
   try:
    for byte in b'HTTP/1.0 200 OK\r\nX-Padding: '+b'x'*50+b'\r\nContent-Length: 2\r\n\r\n{}':
     self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.005)
   except OSError:pass
   print('slow headers server streaming seconds',round(time.monotonic()-started,3));return
  if residual_mode=='incomplete':
   self.send_response(200);self.send_header('Content-Length','100');self.end_headers();self.wfile.write(b'{"building":false,"result":"SUCCESS"}');return
  if residual_mode=='reconcile_incomplete':return self.send({'number':1,'url':residual_base+'/job/demo/1/'})
  if residual_mode=='reconcile_valid':return self.send({'number':1,'url':residual_base+'/job/demo/1/','building':False,'result':'SUCCESS'})
  if residual_mode=='property':return self.send({'property':42})
  if residual_mode=='missing_building':return self.send({'result':'SUCCESS'})
  if residual_mode=='missing_result':return self.send({'building':False})
  if residual_mode=='list_result':return self.send({'number':1,'url':residual_base+self.path.removesuffix('api/json'),'building':False,'result':['secret-value']})
  if residual_mode=='reconcile_wrong':return self.send({'url':'http://evil.invalid/job/other/999/','number':999,'building':False,'result':'SUCCESS'})
  arts=None if residual_mode=='null_artifacts' else [{'relativePath':str(i)} for i in range(4)] if residual_mode=='aggregate' else [{'relativePath':'a'},{'relativePath':'b'}] if residual_mode=='count' else [{'relativePath':'../../config.xml'}] if residual_mode=='traversal' else []
  return self.send({'number':1,'url':residual_base+self.path.removesuffix('api/json'),'building':False,'result':'ABORTED' if residual_mode=='aborted' else 'SUCCESS','artifacts':arts,'property':[]})
 def do_POST(self):
  global residual_mode
  if self.path.endswith('/stop'):residual_mode='aborted'
  residual_seen.append(('POST',self.path));self.send_response(201);self.send_header('Location','/queue/item/7/');self.send_header('Content-Length','0');self.end_headers()
@pytest.fixture(scope="module")
def residual_server():
 global residual_base
 server=ThreadingHTTPServer(('127.0.0.1',0),residual_Handler)
 threading.Thread(target=server.serve_forever,daemon=True).start()
 residual_base=f'http://127.0.0.1:{server.server_port}'
 yield
 server.shutdown();server.server_close()

import pytest
@pytest.fixture
def residual_reset(residual_server):
 global residual_mode,residual_qcount,residual_wire,residual_on_get
 residual_mode='normal';residual_qcount=residual_wire=0;residual_on_get=None;residual_seen.clear()
 yield
@pytest.fixture
def residual_receipt(tmp_path,residual_reset):
 def make(**kw):
  p=tmp_path/str(time.monotonic_ns());x=jj.open_receipt(p,residual_base,'demo');x.update(kw);jj.save_receipt(p,x);return p
 return make
def residual_cli(p,*args,timeout=3,tool=HELPER):
 r=subprocess.run([sys.executable,str(tool),'--controller',residual_base,'--allow-http','--job','demo','--receipt',str(p),*args],capture_output=True,text=True,timeout=timeout,cwd=p.parent)
 print(json.dumps({'exit':r.returncode,'stderr':r.stderr[-350:],'requests':residual_seen,'receipt':json.loads(p.read_text()) if p.exists() and p.is_file() else None}))
 return r
def residual_saved(p):return json.loads(p.read_text())
@pytest.mark.usefixtures("residual_reset")
def test_residual_body_deadline(residual_receipt):
 global residual_mode
 residual_mode='slow';p=residual_receipt(build_number=1,phase='building');t=time.monotonic();r=residual_cli(p,'--deadline','.04','wait');assert r.returncode==3 and time.monotonic()-t<.4 and residual_saved(p)['build_number']==1
@pytest.mark.usefixtures("residual_reset")
def test_residual_wrong_queue(residual_receipt):
 global residual_mode
 residual_mode='wrong_queue';p=residual_receipt(queue_id=7,phase='queued');r=residual_cli(p,'wait');assert r.returncode==3 and residual_saved(p)['build_number'] is None and residual_seen==[('GET','/queue/item/7/api/json')]
@pytest.mark.usefixtures("residual_reset")
def test_residual_malicious_receipt(residual_receipt):
 p=residual_receipt();x=residual_saved(p);x.update(build_number='1/../../other/2',phase='building');p.write_text(json.dumps(x));assert residual_cli(p,'wait').returncode==3 and not residual_seen
@pytest.mark.usefixtures("residual_reset")
def test_residual_delayed_cancel(residual_receipt):
 global residual_mode
 residual_mode='delayed_cancel';p=residual_receipt(queue_id=7,phase='queued');r=residual_cli(p,'--poll','.001','cancel');assert ('POST','/job/demo/1/stop') in residual_seen and residual_saved(p)['build_number']==1 and r.returncode==2
@pytest.mark.parametrize('m,ok',[('reconcile_wrong',False),('reconcile_incomplete',False),('reconcile_valid',True)])
@pytest.mark.usefixtures("residual_reset")
def test_residual_reconcile(residual_receipt,m,ok):
 global residual_mode
 residual_mode=m;p=residual_receipt(phase='submission_uncertain',queue_id=7);r=residual_cli(p,'reconcile','--build-number','1');assert r.returncode==(0 if ok else 3);assert residual_saved(p)['build_number']==(1 if ok else None);assert residual_seen==[('GET','/job/demo/1/api/json')]
@pytest.mark.usefixtures("residual_reset")
def test_residual_terminal_monotonic(residual_receipt):
 global residual_mode
 p=residual_receipt(queue_id=7,build_number=1,phase='terminal',jenkins_result='SUCCESS');r=residual_cli(p,'cancel');residual_mode='aborted';r2=residual_cli(p,'cancel');assert not [x for x in residual_seen if x[0]=='POST'];assert residual_saved(p)['jenkins_result']=='SUCCESS';assert r.returncode==r2.returncode==0

@pytest.mark.usefixtures("residual_reset")
def test_residual_parent_generation(residual_receipt,tmp_path):
 global residual_on_get
 active=tmp_path/'active';active.mkdir();p=active/'r';x=jj.open_receipt(p,residual_base,'demo');x.update(build_number=1,phase='building');jj.save_receipt(p,x)
 def swap():
  active.rename(tmp_path/'parked');active.mkdir();y=dict(x);y['request_id']='replacement-request';p.write_text(json.dumps(y))
 residual_on_get=swap;r=residual_cli(p,'wait');assert residual_saved(p)['request_id']=='replacement-request';assert r.returncode!=0
@pytest.mark.parametrize('m',['missing_building','missing_result','list_result','incomplete'])
@pytest.mark.usefixtures("residual_reset")
def test_residual_bad_observation(residual_receipt,m):
 global residual_mode
 residual_mode=m;p=residual_receipt(build_number=1,phase='building');r=residual_cli(p,'wait');assert r.returncode==3 and residual_saved(p)['phase']=='building' and residual_saved(p)['jenkins_result'] is None

@pytest.mark.usefixtures("residual_reset")
def test_residual_parameter_schema(tmp_path):
 global residual_mode
 residual_mode='property';p=tmp_path/'r';r=residual_cli(p,'submit');assert r.returncode==3 and 'Traceback' not in r.stderr and 'secret-value' not in r.stderr and not [x for x in residual_seen if x[0]=='POST']
@pytest.mark.usefixtures("residual_reset")
def test_residual_persist_expiry(tmp_path):
 p=tmp_path/'r';before=time.time();r=residual_cli(p,'--deadline','900','submit');assert r.returncode==0;assert 899<residual_saved(p)['deadline_at']-before<901

@pytest.mark.usefixtures("residual_reset")
def test_residual_header_deadline(residual_receipt):
 global residual_mode
 residual_mode='slow_headers';p=residual_receipt(build_number=1,phase='building');t=time.monotonic();r=residual_cli(p,'--deadline','.04','wait');elapsed=time.monotonic()-t;print('elapsed',elapsed);assert r.returncode==3 and elapsed<.4

@pytest.mark.usefixtures("residual_reset")
def test_residual_fifo(tmp_path):
 p=tmp_path/'fifo';os.mkfifo(p)
 r=subprocess.run([sys.executable,str(HELPER),'--controller',residual_base,'--allow-http','--job','demo','--receipt',str(p),'--deadline','.04','show'],capture_output=True,text=True,timeout=.4);assert r.returncode==3

@pytest.mark.usefixtures("residual_reset")
def test_residual_expired_show(residual_receipt):
 p=residual_receipt(build_number=1,phase='terminal',jenkins_result='SUCCESS',deadline_at=time.time()-1);r=residual_cli(p,'show');assert r.returncode==0 and json.loads(r.stdout)['jenkins_result']=='SUCCESS' and not residual_seen

@pytest.mark.usefixtures("residual_reset")
def test_residual_inner_lock(residual_receipt):
 global residual_mode
 residual_mode='reconcile_valid';p=residual_receipt();fd=os.open(str(p)+'.lock',os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX)
 try:r=residual_cli(p,'--deadline','.04','reconcile','--build-number','1',timeout=.4);assert r.returncode==3
 finally:os.close(fd)
@pytest.mark.parametrize('m',['null_artifacts','aggregate','count','traversal'])
@pytest.mark.usefixtures("residual_reset")
def test_residual_collection(residual_receipt,tmp_path,m):
 global residual_mode
 residual_mode=m;p=residual_receipt(build_number=1,phase='terminal',jenkins_result='SUCCESS');r=residual_cli(p,'collect','--output',str(tmp_path/'out'),'--artifact-bytes','5','--artifact-total-bytes','5','--artifact-count','1' if m=='count' else '4');x=residual_saved(p);print('server bytes sent',residual_wire)
 assert r.returncode!=0 and x['jenkins_result']=='SUCCESS' and x['collection']['status']=='partial'
 if m=='aggregate':
  assert len([a for a in residual_seen if '/artifact/' in a[1]])<=1
  assert all('bytes' in a and 'reason' in a for a in x['collection']['artifacts'] if a['status']=='error')
 if m=='traversal':assert not [a for a in residual_seen if '/artifact/' in a[1]]
@pytest.mark.usefixtures("residual_reset")
def test_residual_receipt_schema(residual_receipt):
 p=residual_receipt();x=residual_saved(p);x.update(request_id='',phase='terminal',jenkins_result='SUCCESS',collection={'status':'complete','artifacts':[42]});p.write_text(json.dumps(x));assert residual_cli(p,'show').returncode==3 and not residual_seen

@pytest.mark.usefixtures("residual_reset")
def test_residual_actual_failed_bytes(residual_receipt,tmp_path):
 global residual_mode
 residual_mode='aggregate';p=residual_receipt(build_number=1,phase='terminal',jenkins_result='SUCCESS');driver=tmp_path/'driver.py';ledger=tmp_path/'bytes.json'
 driver.write_text('import importlib.util,sys,json\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location("j",'+repr(str(HELPER))+');j=importlib.util.module_from_spec(s);s.loader.exec_module(j)\noriginal=j.Client.request\nreads=[]\nclass Proxy:\n def __init__(self,r):self.r=r\n def read(self,n):\n  b=self.r.read(n);reads.append(len(b));return b\nclass Counting(j.Client):\n def _read(self,r,cap):\n  if "/artifact/" in r.url:return super()._read(Proxy(r),cap)\n  return super()._read(r,cap)\nj.Client=Counting\ntry:rc=j.main(sys.argv[1:])\nfinally:Path('+repr(str(ledger))+').write_text(json.dumps(reads))\nraise SystemExit(rc)\n')
 r=residual_cli(p,'collect','--output',str(tmp_path/'o'),'--artifact-bytes','5','--artifact-total-bytes','5',tool=driver);actual=sum(json.loads(ledger.read_text()));print('actual artifact bytes consumed by helper',actual)
 assert actual<=6 # even allowing a single overflow-detection byte, no later download is admitted
 assert sum(a.get('bytes',0) for a in residual_saved(p)['collection']['artifacts'] if a['path']!='console.log')==actual

@pytest.mark.parametrize('command',['wait','collect'])
@pytest.mark.usefixtures("residual_reset")
def test_residual_wrong_build_metadata(residual_receipt,tmp_path,command):
 global residual_mode
 residual_mode='reconcile_wrong';p=residual_receipt(build_number=1,phase='building' if command=='wait' else 'terminal',jenkins_result=None if command=='wait' else 'SUCCESS')
 r=residual_cli(p,command,*(['--output',str(tmp_path/'o')] if command=='collect' else []));assert r.returncode==3;assert residual_seen==[('GET','/job/demo/1/api/json')]


@pytest.mark.parametrize('kind',['hudson.model.FreeStyleProject','org.jenkinsci.plugins.workflow.job.WorkflowJob'])
@pytest.mark.parametrize('parameterized',[False,True])
def test_case12_copied_ordinary_folder_spaces(fake,tmp_path,kind,parameterized):
    """Same copied executable and ordinary endpoints for both existing job types."""
    b,f=fake;f.states['class']=kind
    if parameterized:f.states['properties']=[{'parameterDefinitions':[{'name':'branch','type':'StringParameterDefinition'}]}]
    copy=tmp_path/'copied-helper.py';copy.write_bytes(HELPER.read_bytes())
    args=[sys.executable,str(copy),'--controller',b,'--allow-http','--job','release folder/existing '+kind.split('.')[-1],'--receipt',str(tmp_path/'r'),'submit']
    if parameterized:args+=['--parameter','branch=release & test']
    run=subprocess.run(args,cwd=tmp_path,capture_output=True,text=True,timeout=3)
    assert run.returncode==0,run.stderr
    post=[r for r in f.seen if r[0]=='POST'];assert len(post)==1
    expected='/jenkins/job/release%20folder/job/existing%20'+kind.split('.')[-1]
    assert f.seen[0][1]==expected+'/api/json'
    assert post[0][1]==expected+('/buildWithParameters' if parameterized else '/build')
    assert post[0][3]==(b'branch=release+%26+test' if parameterized else b'')
    saved=json.loads((tmp_path/'r').read_text());assert saved['queue_id']==7 and saved['phase']=='queued'

@pytest.mark.parametrize('lost',['disconnect','killed'])
def test_case7_post_loss_crash_never_resubmits(fake,tmp_path,monkeypatch,lost):
    b,f=fake;reached=threading.Event();release=threading.Event();original=Fake.do_POST
    def interrupted(self):
        if self.path.endswith('/build'):
            self.seen.append(('POST',self.path,self.headers.get('Authorization',''),b''));reached.set()
            if lost=='killed':release.wait(2)
            self.close_connection=True;return
        return original(self)
    monkeypatch.setattr(Fake,'do_POST',interrupted)
    p=tmp_path/'r';cmd=[sys.executable,str(HELPER),'--controller',b,'--allow-http','--job','folder/demo','--receipt',str(p),'submit']
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert reached.wait(2)
        assert json.loads(p.read_text())['phase']=='submission_uncertain'
        if lost=='killed':proc.kill()
        proc.communicate(timeout=3)
    finally:
        release.set()
        if proc.poll() is None:proc.kill();proc.wait()
    assert proc.returncode!=0
    assert cli(b,p,'submit').returncode==3
    assert len([r for r in f.seen if r[0]=='POST'])==1

def test_case7_two_submitters_one_post(fake,tmp_path):
    b,f=fake;p=tmp_path/'r'
    cmd=[sys.executable,str(HELPER),'--controller',b,'--allow-http','--job','folder/demo','--receipt',str(p),'submit']
    procs=[subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for proc in procs:proc.communicate(timeout=3)
    assert sorted(proc.returncode for proc in procs)==[0,3]
    assert len([r for r in f.seen if r[0]=='POST'])==1

def test_case6_persisted_expiry_resume_and_offline_show(fake,tmp_path):
    b,f=fake;p=tmp_path/'r'
    assert cli(b,p,'--deadline','.1','submit').returncode==0
    expiry=json.loads(p.read_text())['deadline_at'];time.sleep(.12);f.seen=[]
    assert cli(b,p,'--deadline','900','wait').returncode==3
    assert not f.seen and json.loads(p.read_text())['deadline_at']==expiry
    shown=cli(b,p,'show');assert shown.returncode==0 and json.loads(shown.stdout)['phase']=='queued' and json.loads(shown.stdout)['queue_id']==7

def test_case9_local_interruption_no_remote_cancel(fake,tmp_path):
    b,f=fake;p=tmp_path/'r';x=jj.open_receipt(p,b,'folder/demo');x.update(phase='queued',queue_id=7);jj.save_receipt(p,x)
    f.states['queue']={}
    proc=subprocess.Popen([sys.executable,str(HELPER),'--controller',b,'--allow-http','--job','folder/demo','--receipt',str(p),'--poll','.005','wait'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        end=time.monotonic()+2
        while not f.seen and time.monotonic()<end:time.sleep(.001)
        assert f.seen
        proc.terminate();proc.communicate(timeout=2)
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
    assert not [r for r in f.seen if r[0]=='POST']
    saved=json.loads(p.read_text());assert saved['queue_id']==7 and saved['jenkins_result'] is None

def test_case5_observed_queue_cancellation(fake,tmp_path):
    b,f=fake;p=tmp_path/'r';x=jj.open_receipt(p,b,'folder/demo');x.update(phase='queued',queue_id=7);jj.save_receipt(p,x)
    run=cli(b,p,'cancel');assert run.returncode==2
    assert json.loads(p.read_text())['jenkins_result']=='QUEUE_CANCELLED'
    assert json.loads(run.stdout)['jenkins_result']=='QUEUE_CANCELLED'
    assert [r[1] for r in f.seen if r[0]=='POST']==['/jenkins/queue/cancelItem']

@pytest.mark.parametrize('boundary',['job_redirect','build_redirect','artifact_redirect','location','queue_url'])
def test_case10_two_servers_no_forwarding(fake,tmp_path,monkeypatch,boundary):
    b,f=fake;outside=[]
    class Sink(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):outside.append((self.path,self.headers.get('Authorization')));self.send_response(200);self.end_headers()
        do_POST=do_GET
    sink=ThreadingHTTPServer(('127.0.0.1',0),Sink);thread=threading.Thread(target=sink.serve_forever,daemon=True);thread.start()
    evil=f'http://127.0.0.1:{sink.server_port}/stolen';p=tmp_path/'r'
    try:
        original=Fake.do_GET
        def redirect(self):
            match=(boundary=='job_redirect' and self.path.endswith('/demo/api/json')) or (boundary=='build_redirect' and self.path.endswith('/1/api/json')) or (boundary=='artifact_redirect' and '/artifact/' in self.path)
            if match:
                self.seen.append(('GET',self.path,self.headers.get('Authorization',''),b''));return self.reply(302,b'',{'Location':evil})
            return original(self)
        monkeypatch.setattr(Fake,'do_GET',redirect)
        command=['submit']
        if boundary=='location':f.states['location']=evil
        elif boundary=='queue_url':
            x=jj.open_receipt(p,b,'folder/demo');x.update(phase='queued',queue_id=7);jj.save_receipt(p,x)
            f.states['queue']={'executable':{'number':1,'url':evil}};command=['wait']
        elif boundary=='build_redirect':
            x=jj.open_receipt(p,b,'folder/demo');x.update(phase='building',build_number=1);jj.save_receipt(p,x);command=['wait']
        elif boundary=='artifact_redirect':
            terminal(p,b);f.states['artifacts']=[{'relativePath':'a'}];command=['collect','--output',str(tmp_path/'out')]
        run=cli(b,p,*command,env={'JENKINS_USERNAME':'u','JENKINS_API_TOKEN':'not-for-other-server'})
        assert run.returncode in {2,3} and not outside
        assert 'not-for-other-server' not in run.stderr+run.stdout
        assert len([r for r in f.seen if r[0]=='POST'])==(1 if boundary=='location' else 0)
    finally:sink.shutdown();sink.server_close();thread.join()

@pytest.mark.parametrize('url',['/wrong/job/folder/job/demo/1/','/jenkins/job/folder/job/other/1/','/jenkins/job/folder/job/demo/2/','/jenkins/job/folder/job/demo/%2e%2e/1/'])
def test_case10_queue_identity_ingress(fake,tmp_path,url):
    b,f=fake;p=tmp_path/'r';x=jj.open_receipt(p,b,'folder/demo');x.update(phase='queued',queue_id=7);jj.save_receipt(p,x)
    f.states['queue']={'executable':{'number':1,'url':b.removesuffix('/jenkins')+url}}
    assert cli(b,p,'wait').returncode==3
    assert [r[:2] for r in f.seen]==[('GET','/jenkins/queue/item/7/api/json')]
    assert json.loads(p.read_text())['build_number'] is None

@pytest.mark.parametrize('path',['../secret','%2e%2e/secret','a/%2fsecret','/absolute','a//b','a/../b','%252e%252e/secret'])
def test_case11_artifact_path_before_get(fake,tmp_path,path):
    b,f=fake;p=tmp_path/'r';terminal(p,b);f.states['artifacts']=[{'relativePath':path}]
    assert cli(b,p,'collect','--output',str(tmp_path/'out')).returncode==2
    assert not [r for r in f.seen if '/artifact/' in r[1]]
    saved=json.loads(p.read_text());assert saved['jenkins_result']=='SUCCESS' and saved['collection']['status']=='partial'

@pytest.mark.parametrize('boundary',['api','log','artifact_error','collision','secret_error','bad_chunk'])
def test_case11_bounds_framing_errors(fake,tmp_path,monkeypatch,boundary):
    b,f=fake;p=tmp_path/'r';out=tmp_path/'out';terminal(p,b)
    f.states['artifacts']=[{'relativePath':str(i)} for i in range(4)]
    command=['collect','--output',str(out),'--artifact-bytes','5','--artifact-total-bytes','5']
    if boundary=='api':command=['--api-bytes','10',*command]
    elif boundary=='log':f.states['log']=b'x'*100;command+=['--log-bytes','5']
    elif boundary=='artifact_error':f.states.update(artifact_status=500,artifact=b'x'*20)
    elif boundary=='collision':out.mkdir();(out/'0').write_bytes(b'keep')
    elif boundary in {'secret_error','bad_chunk'}:
        original=Fake.do_GET
        def respond(self):
            if self.path.endswith('/1/api/json'):
                self.seen.append(('GET',self.path,self.headers.get('Authorization',''),b''))
                if boundary=='secret_error':return self.reply(500,b'not-for-other-server')
                self.wfile.write(b'HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20\r\n{}');self.wfile.flush();self.close_connection=True;return
            return original(self)
        monkeypatch.setattr(Fake,'do_GET',respond)
    run=cli(b,p,*command,env={'JENKINS_USERNAME':'u','JENKINS_API_TOKEN':'not-for-other-server'})
    assert run.returncode in {2,3} and 'not-for-other-server' not in run.stderr+run.stdout and 'Traceback' not in run.stderr
    saved=json.loads(p.read_text());assert saved['jenkins_result']=='SUCCESS' and saved['collection']['status']=='partial'
    if boundary=='artifact_error':
        assert len([r for r in f.seen if '/artifact/' in r[1]])==1
        assert sum(e.get('bytes',0) for e in saved['collection']['artifacts'] if e['path']!='console.log')==6
    if boundary=='collision':assert (out/'0').read_bytes()==b'keep'
    assert not [r for r in f.seen if r[0]=='POST']

@pytest.mark.parametrize('field,value',[('phase','cancel_requested'),('phase','building'),('request_id',''),('build_number',True),('queue_id',False),('cancel_intent','yes'),('stop_attempted',1),('collection',{'status':'complete','artifacts':[]})])
def test_case11_receipt_cross_fields_no_requests(fake,tmp_path,field,value):
    b,f=fake;p=tmp_path/'r';x=jj.open_receipt(p,b,'folder/demo');x[field]=value;p.write_text(json.dumps(x))
    assert cli(b,p,'show').returncode==3 and not f.seen

@pytest.mark.parametrize('boundary',['receipt_leaf','artifact_parent'])
def test_case11_replacement_during_http(fake,tmp_path,monkeypatch,boundary):
    b,f=fake;p=tmp_path/'r';out=tmp_path/'out';out.mkdir();replacement={}
    if boundary=='receipt_leaf':
        x=jj.open_receipt(p,b,'folder/demo');x.update(phase='building',build_number=1);jj.save_receipt(p,x);command=['wait']
    else:terminal(p,b);f.states['artifacts']=[{'relativePath':'a'}];command=['collect','--output',str(out)]
    original=Fake.do_GET
    def replace(self):
        if boundary=='receipt_leaf' and self.path.endswith('/1/api/json'):
            replacement.update(json.loads(p.read_text()));replacement['request_id']='new-owner';p.write_text(json.dumps(replacement))
        if boundary=='artifact_parent' and self.path.endswith('/artifact/a'):
            out.rename(tmp_path/'parked');out.mkdir()
        return original(self)
    monkeypatch.setattr(Fake,'do_GET',replace)
    run=cli(b,p,*command);assert run.returncode in {2,3}
    if boundary=='receipt_leaf':assert json.loads(p.read_text())['request_id']=='new-owner'
    else:assert not (out/'a').exists() and not (tmp_path/'parked'/'a').exists()
    assert not [r for r in f.seen if r[0]=='POST']

def test_case7_atomic_replacement_fault_retains_receipt(fake,tmp_path,monkeypatch):
    b,f=fake;p=tmp_path/'r';x=jj.open_receipt(p,b,'folder/demo');original=p.read_bytes();x.update(phase='submission_uncertain')
    def failure(*a,**k):raise OSError('injected publication failure')
    monkeypatch.setattr(jj.os,'replace',failure)
    with pytest.raises(OSError):jj.save_receipt(p,x)
    assert p.read_bytes()==original and not list(tmp_path.glob('.receipt-*')) and not f.seen

def test_case11_collection_metadata_timeout_preserves_terminal(fake,tmp_path,monkeypatch):
    b,f=fake;p=tmp_path/'r';terminal(p,b)
    def slow(self):
        self.seen.append(('GET',self.path,self.headers.get('Authorization',''),b''))
        self.send_response(200);self.send_header('Content-Length','100');self.end_headers()
        for _ in range(100):
            try:self.wfile.write(b' ');self.wfile.flush();time.sleep(.01)
            except OSError:break
    monkeypatch.setattr(Fake,'do_GET',slow)
    begin=time.monotonic();run=cli(b,p,'--deadline','.04','collect','--output',str(tmp_path/'out'))
    assert run.returncode==3 and time.monotonic()-begin<.5
    saved=json.loads(p.read_text());assert saved['jenkins_result']=='SUCCESS' and saved['build_number']==1 and saved['collection']=={'status':'partial','artifacts':[]}
    assert len(f.seen)==1 and f.seen[0][0]=='GET'
