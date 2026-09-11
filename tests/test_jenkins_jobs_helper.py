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
    seen:list[tuple[str,str,str]]=[]; states:dict[str,object]={}
    def log_message(self,*a:object)->None:pass
    def reply(self,status:int,body:object=b"",headers:dict[str,str]|None=None)->None:
        b=body if isinstance(body,bytes) else json.dumps(body).encode();self.send_response(status)
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self)->None:
        self.seen.append(("GET",self.path,self.headers.get("Authorization","")))
        if self.states.get("auth") and not self.headers.get("Authorization","").startswith("Basic "):return self.reply(401)
        if self.path.endswith("/api/json") and "/queue/" not in self.path:
            return self.reply(200,{"property":self.states.get("properties",[]),"building":False,"result":self.states.get("result","SUCCESS"),"artifacts":self.states.get("artifacts",[])})
        if "/queue/item/" in self.path:return self.reply(200,self.states.get("queue",{"executable":{"number":1}}))
        if self.path.endswith("consoleText"):return self.reply(200,self.states.get("log",b"log"))
        if "/artifact/" in self.path:return self.reply(self.states.get("artifact_status",200),self.states.get("artifact",b"bytes"))
        return self.reply(404)
    def do_POST(self)->None:
        self.seen.append(("POST",self.path,self.headers.get("Authorization","")))
        if self.path.endswith("/build") or self.path.endswith("/buildWithParameters"):return self.reply(201,b"",{"Location":"/jenkins/queue/item/7/"})
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
    b,f=fake;f.states["auth"]=True;r=cli(b,tmp_path/"r","submit",env={"JENKINS_USERNAME":"u","JENKINS_API_TOKEN":"t"});assert r.returncode==0 and [x[1] for x in f.seen if x[0]=="POST"]==["/jenkins/job/folder/job/demo/build"] and all(x[2].startswith("Basic ") for x in f.seen)
def test_02_parameterized_declared_only(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["properties"]=[{"parameterDefinitions":[{"name":"branch","type":"StringParameterDefinition"}]}];assert cli(b,tmp_path/"r","submit","--parameter","branch=a").returncode==0;assert any("buildWithParameters" in x[1] for x in f.seen);assert cli(b,tmp_path/"x","submit","--parameter","no=x").returncode==3
def test_03_context_folder_and_identity_refusal()->None:
    assert jj.job_path("a b/c")=="/job/a%20b/job/c"
    c=jj.Controller("https://x.test/jenkins")
    for u in ("https://x.test/jenkins/job/a/lastBuild","https://x.test/jenkins/job/a/1/%2e%2e/config","https://x.test/jenkins/job/a/job/b/2"):
        with pytest.raises(jj.ValidationError):c.checked(u,"build","a",1)
def test_04_queue_build_success_collect(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["artifacts"]=[{"relativePath":"a.txt"}];p=tmp_path/"r";assert cli(b,p,"submit").returncode==0;assert cli(b,p,"--poll",".01","wait").returncode==0;assert cli(b,p,"collect","--output",str(tmp_path/"o")).returncode==0;assert (tmp_path/"o"/"a.txt").read_bytes()==b"bytes"
@pytest.mark.parametrize("result",["FAILURE","UNSTABLE","ABORTED"])
def test_05_terminal_results(fake:tuple[str,Fake],tmp_path:Path,result:str)->None:
    b,f=fake;f.states["result"]=result;p=tmp_path/"r";terminal(p,b);assert cli(b,p,"wait").returncode==2
def test_06_deadline_and_nonfinite(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,_=fake;p=tmp_path/"r";terminal(p,b);assert cli(b,p,"--deadline","nan","show").returncode==3
def test_07_uncertain_and_reuse(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;f.states["properties"]=[];p=tmp_path/"r";jj.open_receipt(p,b,"folder/demo");assert cli(b,p,"submit").returncode==3;assert not [x for x in f.seen if x[0]=="POST"]
def test_08_reconcile_no_resubmit(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x["phase"]="submission_uncertain";jj.save_receipt(p,x);assert cli(b,p,"reconcile","--build-number","1").returncode==0;assert not f.seen
def test_09_cancel_queue_race(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";x=jj.open_receipt(p,b,"folder/demo");x.update(phase="queued",queue_id=7);jj.save_receipt(p,x);assert cli(b,p,"cancel").returncode==2;assert any(x[1]=="/jenkins/queue/cancelItem" for x in f.seen)
def test_10_two_server_refusal_no_forward(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;c=jj.Controller(b)
    with pytest.raises(jj.ValidationError):c.checked("http://127.0.0.2:1/jenkins/queue/item/7","queue",ident=7)
    assert not f.seen
def test_11_bounds_symlink_and_secret(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,f=fake;p=tmp_path/"r";terminal(p,b);d=tmp_path/"o";d.mkdir();(d/"x").symlink_to(tmp_path);f.states["artifacts"]=[{"relativePath":"x/escape"}];assert cli(b,p,"collect","--output",str(d)).returncode==2;assert not (tmp_path/"escape").exists();assert "SECRET" not in cli(b,tmp_path/"n","--token-env","BAD\nSECRET","show").stderr
def test_12_copied_standalone_helper(fake:tuple[str,Fake],tmp_path:Path)->None:
    b,_=fake;copy=tmp_path/"tool.py";copy.write_bytes(HELPER.read_bytes());r=subprocess.run([sys.executable,str(copy),"--help"],capture_output=True,text=True,cwd=tmp_path);assert r.returncode==0 and "runtime" not in copy.read_text()
