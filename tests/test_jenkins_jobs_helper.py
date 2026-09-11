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
            if isinstance(builds,list) and builds:return self.reply(200,builds.pop(0))
            return self.reply(200,{"property":self.states.get("properties",[]),"building":False,"result":self.states.get("result","SUCCESS"),"artifacts":self.states.get("artifacts",[])})
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
            if self.states.get("submit_status"):return self.reply(int(self.states["submit_status"]))
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
    b,f=fake;f.states["result"]=result;p=tmp_path/"r";terminal(p,b);assert cli(b,p,"wait").returncode==2
def test_06_deadline_and_nonfinite(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";terminal(p,b);assert cli(b,p,"--deadline","nan","show").returncode==3
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
    b,f=fake;p=tmp_path/"r";terminal(p,b)
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
        assert receipt["build_number"]==1 and receipt["jenkins_result"]=="SUCCESS"
        assert receipt["collection"]["status"]=="timeout"
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
    assert cli(b,p,"reconcile","--build-number","1").returncode==0
    assert any(x[0]=="GET" and x[1].endswith("/job/folder/job/demo/1/api/json") for x in f.seen)
    assert not [x for x in f.seen if x[0]=="POST"] and json.loads(p.read_text())["build_number"]==1
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
    canceller=subprocess.Popen([*common,"cancel"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    waiter.communicate(timeout=5);canceller.communicate(timeout=5)
    receipt=json.loads(p.read_text())
    assert receipt["build_number"]==1 and receipt["phase"] in {"building","terminal","cancel_requested"}
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
