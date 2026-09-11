#!/usr/bin/env python3
"""Safely operate an already-authorized Jenkins job; standard library only."""
from __future__ import annotations
import argparse, base64, fcntl, json, math, os, secrets, sys, tempfile, time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION="2"; TERMINAL={"SUCCESS","FAILURE","UNSTABLE","ABORTED"}
SUPPORTED={"hudson.model.StringParameterDefinition","hudson.model.TextParameterDefinition","hudson.model.BooleanParameterDefinition","hudson.model.ChoiceParameterDefinition","hudson.model.PasswordParameterDefinition","StringParameterDefinition","TextParameterDefinition","BooleanParameterDefinition","ChoiceParameterDefinition"}
class JenkinsError(Exception): pass
class ValidationError(JenkinsError): pass
class ReceiptError(JenkinsError): pass
class TransportError(JenkinsError): pass
def finite(x:float,n:str)->float:
    if not math.isfinite(x) or x<=0: raise ValidationError(f"{n} must be finite and positive")
    return x
def bad(s:str)->bool:
    return not s or s in {".",".."} or "\\" in s or any(x in s.lower() for x in ("%2f","%5c","%2e"))
def job_path(n:str)->str:
    ps=n.split("/")
    if any(bad(p) for p in ps): raise ValidationError("unsafe job name")
    return "".join("/job/"+quote(p,safe="") for p in ps)
def validate_controller(u:str,allow_http:bool=False)->str:
    p=urlsplit(u)
    if p.scheme not in ({"https","http"} if allow_http else {"https"}) or not p.netloc or p.username or p.password or p.query or p.fragment or any(bad(x) for x in p.path.split("/") if x): raise ValidationError("controller must be a credential-free safe HTTPS URL")
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip("/"),"",""))
def controller_url(c:str,j:str)->str:return validate_controller(c,True)+job_path(j)
class Controller:
    def __init__(self,b:str):self.base=validate_controller(b,True);self.p=urlsplit(self.base)
    def checked(self,u:str,kind:str,job:str|None=None,ident:int|None=None)->str:
        q=urlsplit(u); root=self.p.path.rstrip("/")
        if q.scheme!=self.p.scheme or q.netloc!=self.p.netloc or q.username or q.password or q.query or q.fragment or not q.path.startswith(root+"/") or any(bad(x) for x in q.path.split("/") if x):raise ValidationError("untrusted Jenkins URL")
        tail=q.path[len(root):].rstrip("/")
        ok=(kind=="queue" and tail==f"/queue/item/{ident}") or (kind=="build" and job is not None and tail==job_path(job)+f"/{ident}") or (kind=="job" and job is not None and tail==job_path(job))
        if not ok:raise ValidationError("Jenkins URL does not match exact identity")
        return urlunsplit((q.scheme,q.netloc,q.path,"",""))
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*a:Any,**k:Any)->None:return None
class Client:
    def __init__(self,c:Controller,user:str|None,token:str|None,timeout:float,api:int):
        self.controller,self.timeout,self.api,self.end=c,finite(timeout,"timeout"),api,None;self.opener=build_opener(NoRedirect)
        if bool(user)!=bool(token):raise ValidationError("both Jenkins username and API token references are required")
        if user and any("\n" in x or "\r" in x for x in (user,token or "")):raise ValidationError("unsafe credential header input")
        self.auth="Basic "+base64.b64encode(f"{user}:{token}".encode()).decode() if user else None
    def remaining(self)->float:
        if self.end is None:return self.timeout
        r=self.end-time.monotonic()
        if r<=0:raise TimeoutError
        return min(r,self.timeout)
    def request(self,m:str,u:str,data:bytes|None=None,limit:int|None=None)->tuple[int,dict[str,str],bytes]:
        cap=self.api if limit is None else limit
        if not isinstance(cap,int) or cap<=0:raise ValidationError("invalid response bound")
        h={"Accept":"application/json"}
        if self.auth:h["Authorization"]=self.auth
        try:r=self.opener.open(Request(u,data=data,headers=h,method=m),timeout=self.remaining())
        except HTTPError as e:
            try:b=e.read(cap+1)
            except Exception:b=b""
            return e.code,dict(e.headers.items()),b
        except (URLError,OSError,ValueError,TimeoutError) as e:raise TransportError("transport failed; identity retained") from e
        chunks=[];size=0
        try:
            with r:
                while True:
                    self.remaining();b=r.read(min(65536,cap-size+1))
                    if not b:break
                    size+=len(b)
                    if size>cap:raise TransportError("response exceeded configured bound")
                    chunks.append(b)
        except (OSError,ValueError,TimeoutError) as e:raise TransportError("bounded response read failed") from e
        return r.status,dict(r.headers.items()),b"".join(chunks)
    def api_get(self,u:str)->dict[str,Any]:
        s,_,b=self.request("GET",u)
        if s!=200:raise TransportError(f"Jenkins API returned HTTP {s}")
        try:x=json.loads(b)
        except (UnicodeDecodeError,json.JSONDecodeError) as e:raise TransportError("invalid Jenkins JSON") from e
        if not isinstance(x,dict):raise TransportError("Jenkins API was not an object")
        return x
def params(meta:dict[str,Any],given:dict[str,str])->dict[str,str]:
    ds=[d for p in (meta.get("property") or []) if isinstance(p,dict) for d in (p.get("parameterDefinitions") or [])]
    names=set()
    for d in ds:
        if not isinstance(d,dict) or not isinstance(d.get("name"),str) or d.get("type") not in SUPPORTED:raise ValidationError("unsupported/malformed Jenkins parameter definition")
        names.add(d["name"])
    if set(given)-names:raise ValidationError("parameter is not declared by this job")
    return given
prepare_parameters=params
def valid_receipt(x:Any)->dict[str,Any]:
    if not isinstance(x,dict) or x.get("version")!=VERSION or not isinstance(x.get("controller"),str) or not isinstance(x.get("job"),str) or x.get("phase") not in {"intent","submission_uncertain","queued","building","terminal","cancel_requested"}:raise ReceiptError("malformed receipt")
    validate_controller(x["controller"],True);job_path(x["job"]);return x
def safe_parent(p:Path)->None:
    cur=Path(p.anchor) if p.is_absolute() else Path(".")
    for x in p.parent.parts[1 if p.is_absolute() else 0:]:
        cur=cur/x
        if cur.exists() and cur.is_symlink():raise ReceiptError("symlink ancestor refused")
    p.parent.mkdir(parents=True,exist_ok=True)
@contextmanager
def locked(p:Path):
    safe_parent(p);l=Path(str(p)+".lock")
    if l.is_symlink():raise ReceiptError("unsafe receipt lock")
    fd=os.open(l,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);fcntl.flock(fd,fcntl.LOCK_EX)
    try:yield
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
def write_receipt(p:Path,x:dict[str,Any],create:bool=False)->None:
    valid_receipt(x);safe_parent(p)
    with locked(p):
        if create:
            try:fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            except FileExistsError as e:raise ReceiptError("receipt exists; reattach rather than resubmit") from e
            try:os.write(fd,(json.dumps(x,sort_keys=True)+"\n").encode());os.fsync(fd)
            finally:os.close(fd)
        else:
            fd,tmp=tempfile.mkstemp(prefix=".receipt-",dir=p.parent);os.fchmod(fd,0o600)
            try:
                with os.fdopen(fd,"w") as h:json.dump(x,h,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
                os.replace(tmp,p);d=os.open(p.parent,os.O_RDONLY);os.fsync(d);os.close(d)
            except BaseException:
                try:os.unlink(tmp)
                except OSError:pass
                raise
def open_receipt(p:Path,c:str,j:str)->dict[str,Any]:
    x={"version":VERSION,"request_id":secrets.token_hex(16),"controller":validate_controller(c,True),"job":j,"phase":"intent","queue_id":None,"build_number":None,"jenkins_result":None,"collection":{"status":"not_started","artifacts":[]}};write_receipt(p,x,True);return x
def load_receipt(p:Path)->dict[str,Any]:
    safe_parent(p)
    try:
        if p.is_symlink():raise ReceiptError("receipt symlink refused")
        return valid_receipt(json.loads(p.read_text()))
    except (OSError,json.JSONDecodeError) as e:raise ReceiptError("receipt unreadable") from e
def save_receipt(p:Path,x:dict[str,Any])->None:write_receipt(p,x)
def bind(x:dict[str,Any],c:Controller,j:str)->None:
    if x["controller"]!=c.base or x["job"]!=j:raise ReceiptError("receipt does not bind this controller/job")
def queue(c:Controller,n:int)->str:return c.base+f"/queue/item/{n}"
def build(c:Controller,j:str,n:int)->str:return c.base+job_path(j)+f"/{n}"
def outcome(x:dict[str,Any])->str:
    if x.get("building") is True:return "BUILDING"
    if x.get("building") is not False:return "TRANSPORT_UNKNOWN"
    return x["result"] if x.get("result") in TERMINAL else "UNSUPPORTED_RESULT"
def submit(c:Client,p:Path,j:str,given:dict[str,str])->dict[str,Any]:
    x=open_receipt(p,c.controller.base,j);m=c.api_get(controller_url(c.controller.base,j)+"/api/json");g=params(m,given);parameterized=any(isinstance(a,dict) and a.get("parameterDefinitions") for a in (m.get("property") or []));u=controller_url(c.controller.base,j)+("/buildWithParameters" if parameterized else "/build");s,h,_=c.request("POST",u,urlencode(g).encode() if parameterized else None);loc=h.get("Location") or h.get("location")
    if s not in {200,201,202} or not loc:x.update(phase="submission_uncertain",submission_status=s);save_receipt(p,x);raise ReceiptError("submission uncertain; reconcile an exact build without resubmit")
    q=urlsplit(urljoin(u,loc))
    try:n=int(q.path.rstrip("/").split("/")[-1])
    except ValueError:x.update(phase="submission_uncertain");save_receipt(p,x);raise ReceiptError("Location lacks numeric queue identity")
    c.controller.checked(urlunsplit((q.scheme,q.netloc,q.path,"","")),"queue",ident=n);x.update(phase="queued",queue_id=n);save_receipt(p,x);return x
def reconcile(c:Client,p:Path,j:str,n:int)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j)
    if n<=0 or x.get("queue_id") is not None or x.get("build_number") is not None:raise ReceiptError("exact reconciliation is unavailable for this receipt")
    c.controller.checked(build(c.controller,j,n),"build",j,n);x.update(phase="building",build_number=n);save_receipt(p,x);return x
def wait(c:Client,p:Path,j:str,deadline:float,poll:float)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j);c.end=time.monotonic()+finite(deadline,"deadline");finite(poll,"poll")
    try:
        while True:
            if x.get("build_number") is not None:
                b=c.api_get(build(c.controller,j,x["build_number"])+"/api/json");o=outcome(b)
                if o!="BUILDING":x.update(phase="terminal",jenkins_result=o);save_receipt(p,x);return x
            elif x.get("queue_id") is not None:
                q=c.api_get(queue(c.controller,x["queue_id"])+"/api/json")
                if q.get("cancelled") is True:x.update(phase="terminal",jenkins_result="QUEUE_CANCELLED");save_receipt(p,x);return x
                e=q.get("executable")
                if isinstance(e,dict) and isinstance(e.get("number"),int):x.update(phase="building",build_number=e["number"]);save_receipt(p,x)
            else:raise ReceiptError("uncertain submit requires explicit exact-build reconciliation")
            time.sleep(min(poll,c.remaining()))
    except TimeoutError:x["collection"]={"status":"timeout","artifacts":[]};save_receipt(p,x);raise ReceiptError("deadline reached; remote cancellation was not sent")
    except (TransportError,ValidationError) as e:x["collection"]={"status":"transport_unknown","artifacts":[]};save_receipt(p,x);raise ReceiptError("transport/identity failure; identity retained") from e
def cancel(c:Client,p:Path,j:str,d:float)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j);c.end=time.monotonic()+finite(d,"deadline")
    if x.get("build_number") is not None:u=build(c.controller,j,x["build_number"])+"/stop";data=None
    elif x.get("queue_id") is not None:u=c.controller.base+"/queue/cancelItem";data=urlencode({"id":x["queue_id"]}).encode()
    else:raise ReceiptError("no identity to cancel")
    s,_,_=c.request("POST",u,data);x.update(phase="cancel_requested",cancel_requested_status=s);save_receipt(p,x)
    if s not in {200,201,202}:raise ReceiptError("Jenkins did not accept cancellation")
    return wait(c,p,j,max(.001,c.remaining()),.05)
def output_file(root:Path,rel:str)->Path:
    if not rel or rel.startswith("/") or any(bad(x) for x in rel.split("/")):raise ValidationError("unsafe artifact path")
    root.mkdir(parents=True,exist_ok=True)
    if root.is_symlink():raise ValidationError("output symlink refused")
    cur=root
    for part in rel.split("/")[:-1]:
        cur=cur/part
        if cur.exists() and cur.is_symlink():raise ValidationError("artifact symlink traversal refused")
        cur.mkdir(exist_ok=True)
    t=cur/rel.split("/")[-1]
    if t.exists() or t.is_symlink():raise ValidationError("artifact collision refused")
    return t
def store(p:Path,b:bytes)->None:
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.write(fd,b);os.fsync(fd)
    finally:os.close(fd)
def collect(c:Client,p:Path,j:str,out:Path,log:int,per:int,total:int,count:int,d:float)->dict[str,Any]:
    if any(not isinstance(v,int) or v<=0 for v in (log,per,total,count)):raise ValidationError("collection bounds must be positive")
    x=load_receipt(p);bind(x,c.controller,j)
    if x.get("jenkins_result") is None or x.get("build_number") is None:raise ReceiptError("collection needs terminal exact build")
    c.end=time.monotonic()+finite(d,"deadline");base=build(c.controller,j,x["build_number"]);man=[];failed=False
    def get(label:str,u:str,cap:int,rel:str)->None:
        nonlocal failed
        try:
            s,_,b=c.request("GET",u,limit=cap)
            if s!=200:man.append({"path":label,"status":"error","http_status":s});failed=True;return
            store(output_file(out,rel),b);man.append({"path":label,"status":"downloaded","bytes":len(b)})
        except (JenkinsError,OSError,TimeoutError):man.append({"path":label,"status":"error"});failed=True
    try:meta=c.api_get(base+"/api/json")
    except JenkinsError:x["collection"]={"status":"partial","artifacts":man};save_receipt(p,x);raise
    get("console.log",base+"/consoleText",log,"console.log");used=0;arts=meta.get("artifacts",[])
    if not isinstance(arts,list):raise TransportError("malformed artifact metadata")
    for idx,a in enumerate(arts):
        rel=a.get("relativePath") if isinstance(a,dict) else None
        if not isinstance(rel,str):man.append({"path":"unknown","status":"error"});failed=True;continue
        if idx>=count:man.append({"path":rel,"status":"skipped","reason":"count_limit"});continue
        cap=min(per,total-used)
        if cap<=0:man.append({"path":rel,"status":"skipped","reason":"aggregate_limit"});continue
        get(rel,base+"/artifact/"+"/".join(quote(z,safe="") for z in rel.split("/")),cap,rel)
        if man[-1]["status"]=="downloaded":used+=man[-1]["bytes"]
    x["collection"]={"status":"partial" if failed else "complete","artifacts":man};save_receipt(p,x);return x
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--controller",required=True);p.add_argument("--job",required=True);p.add_argument("--receipt",type=Path,required=True);p.add_argument("--username-env",default="JENKINS_USERNAME");p.add_argument("--token-env",default="JENKINS_API_TOKEN");p.add_argument("--allow-http",action="store_true");p.add_argument("--timeout",type=float,default=15);p.add_argument("--deadline",type=float,default=900);p.add_argument("--poll",type=float,default=2);p.add_argument("--api-bytes",type=int,default=1_000_000);s=p.add_subparsers(dest="command",required=True);z=s.add_parser("submit");z.add_argument("--parameter",action="append",default=[]);s.add_parser("wait");s.add_parser("cancel");z=s.add_parser("reconcile");z.add_argument("--build-number",type=int,required=True);s.add_parser("show");z=s.add_parser("collect");z.add_argument("--output",type=Path,required=True);z.add_argument("--log-bytes",type=int,default=1_000_000);z.add_argument("--artifact-bytes",type=int,default=10_000_000);z.add_argument("--artifact-total-bytes",type=int,default=50_000_000);z.add_argument("--artifact-count",type=int,default=32);a=p.parse_args(argv)
    try:
        finite(a.timeout,"timeout");finite(a.deadline,"deadline");finite(a.poll,"poll")
        if a.api_bytes<=0:raise ValidationError("api-bytes must be positive")
        c=Client(Controller(validate_controller(a.controller,a.allow_http)),os.environ.get(a.username_env),os.environ.get(a.token_env),a.timeout,a.api_bytes)
        if a.command=="submit":
            g={}
            for v in a.parameter:
                if "=" not in v:raise ValidationError("--parameter needs NAME=VALUE")
                k,val=v.split("=",1)
                if not k or k in g:raise ValidationError("invalid parameter names")
                g[k]=val
            x=submit(c,a.receipt,a.job,g)
        elif a.command=="wait":x=wait(c,a.receipt,a.job,a.deadline,a.poll)
        elif a.command=="cancel":x=cancel(c,a.receipt,a.job,a.deadline)
        elif a.command=="reconcile":x=reconcile(c,a.receipt,a.job,a.build_number)
        elif a.command=="collect":x=collect(c,a.receipt,a.job,a.output,a.log_bytes,a.artifact_bytes,a.artifact_total_bytes,a.artifact_count,a.deadline)
        else:x=load_receipt(a.receipt);bind(x,c.controller,a.job)
        print(json.dumps({"request_id":x["request_id"],"phase":x["phase"],"jenkins_result":x.get("jenkins_result"),"collection":x["collection"]["status"]}))
        return 0 if (a.command in {"submit","show","reconcile"} or (x.get("jenkins_result")=="SUCCESS" and (a.command=="wait" or x["collection"]["status"]=="complete"))) else 2
    except (JenkinsError,ValueError,OverflowError) as e:print("jenkins-jobs: "+str(e)[:240],file=sys.stderr);return 3
if __name__=="__main__":raise SystemExit(main())
