#!/usr/bin/env python3
"""Safely operate an already-authorized Jenkins job; standard library only."""
from __future__ import annotations
import argparse, base64, fcntl, json, math, os, secrets, signal, stat, sys, time
from http.client import HTTPException
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit, unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION="2"; TERMINAL={"SUCCESS","FAILURE","UNSTABLE","ABORTED"}
SUPPORTED={"hudson.model.StringParameterDefinition","hudson.model.TextParameterDefinition","hudson.model.BooleanParameterDefinition","hudson.model.ChoiceParameterDefinition","hudson.model.PasswordParameterDefinition","StringParameterDefinition","TextParameterDefinition","BooleanParameterDefinition","ChoiceParameterDefinition"}
class JenkinsError(Exception): pass
class ValidationError(JenkinsError): pass
class ReceiptError(JenkinsError): pass
class TransportError(JenkinsError): pass
RECEIPT_BYTES = 2_000_000
ACTIVE = None

@contextmanager
def alarm(seconds):
    """Bound DNS/connect/headers/body, including continuously arriving bytes.

    This Unix CLI already requires flock; it owns SIGALRM only during an HTTP
    exchange and restores the previous handler/timer for imported callers.
    """
    def expired(*_):
        raise TimeoutError("absolute request deadline reached")
    previous = signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    old = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if old[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, old[0] - (time.monotonic()-started)), old[1])

def finite(x:float,n:str)->float:
    if not math.isfinite(x) or x<=0: raise ValidationError(f"{n} must be finite and positive")
    return x
def bad(s:str)->bool:
    # Jenkins/proxies may decode more than once. Reject traversal at any
    # decoding depth without rejecting legitimate encoded spaces.
    if len(s)>8192:return True
    for _ in range(4):
        if not s or s in {".",".."} or "/" in s or "\\" in s or any(ord(c)<32 or ord(c)==127 for c in s):return True
        decoded=unquote(s)
        if decoded==s:return False
        s=decoded
    return True
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
        if r<=0:raise TimeoutError("absolute operation deadline reached; inspect receipt with show")
        return min(r,self.timeout)
    def request(self,m:str,u:str,data:bytes|None=None,limit:int|None=None)->tuple[int,dict[str,str],bytes]:
        cap=self.api if limit is None else limit
        if not positive(cap):raise ValidationError("invalid response bound")
        # Every caller constructs an exact job/queue path. Also defend the
        # final credential boundary independently of those callers.
        target=urlsplit(u); origin=self.controller.p
        if (target.scheme,target.netloc)!=(origin.scheme,origin.netloc) or target.username or target.password or target.query or target.fragment or not target.path.startswith(origin.path.rstrip("/")+"/") or any(bad(v) for v in target.path.split("/") if v):
            raise ValidationError("untrusted request target")
        h={"Accept":"application/json"}
        if self.auth:h["Authorization"]=self.auth
        self.last_bytes=0
        try:
            with alarm(self.remaining()):
                try:r=self.opener.open(Request(u,data=data,headers=h,method=m),timeout=self.remaining())
                except HTTPError as e:r=e
                with r:
                    lengths=r.headers.get_all("Content-Length",[])
                    encodings=r.headers.get_all("Transfer-Encoding",[])
                    if len(lengths)>1 or (encodings and encodings!=["chunked"]):raise TransportError("ambiguous response framing")
                    self.response_length=r.headers.get("Content-Length")
                    self.response_chunked=bool(getattr(r,"chunked",False))
                    if self.response_length is not None and (not self.response_length.isdecimal() or self.response_chunked):
                        raise TransportError("invalid response framing")
                    b=self._read(r,cap)
                    return r.status,dict(r.headers.items()),b
        except TimeoutError:raise
        except (URLError,OSError,ValueError,HTTPException) as e:
            raise TransportError("transport/framing failed; identity retained") from e
    def _read(self,r:Any,cap:int)->bytes:
        data=bytearray()
        while True:
            self.remaining()
            # read1 does not wait for an entire buffer. The signal deadline
            # covers both read/read1 and header parsing; one cap+1 overflow
            # byte is charged, even when the request subsequently fails.
            b=getattr(r,"read1",r.read)(min(65536,cap-len(data)+1))
            if not b:break
            self.last_bytes+=len(b);data.extend(b)
            if len(data)>cap:raise TransportError("response exceeded configured bound")
        length=getattr(self,"response_length",None)
        if length is not None and len(data)!=int(length):raise TransportError("incomplete response body")
        return bytes(data)
    def api_get(self,u:str)->dict[str,Any]:
        s,_,b=self.request("GET",u)
        if s!=200:raise TransportError(f"Jenkins API returned HTTP {s}")
        try:x=json.loads(b)
        except (UnicodeDecodeError,json.JSONDecodeError,RecursionError) as e:raise TransportError("invalid Jenkins JSON") from e
        if not isinstance(x,dict):raise TransportError("Jenkins API was not an object")
        return x
def params(meta:dict[str,Any],given:dict[str,str])->dict[str,str]:
    properties=meta.get("property",[])
    if not isinstance(properties,list):raise ValidationError("malformed Jenkins properties")
    names=set()
    for prop in properties:
        if not isinstance(prop,dict):raise ValidationError("malformed Jenkins property")
        definitions=prop.get("parameterDefinitions",[])
        if not isinstance(definitions,list):raise ValidationError("malformed Jenkins parameters")
        for d in definitions:
            if not isinstance(d,dict) or not isinstance(d.get("name"),str) or not d["name"] or d["name"] in names or d.get("type") not in SUPPORTED:
                raise ValidationError("unsupported/malformed Jenkins parameter definition")
            names.add(d["name"])
            if d["name"] in given:
                val=given[d["name"]]
                if "Boolean" in d["type"] and val not in {"true","false"}:raise ValidationError("boolean parameter must be true or false")
                if "Choice" in d["type"] and (not isinstance(d.get("choices"),list) or val not in d["choices"]):raise ValidationError("invalid choice parameter")
    if set(given)-names:raise ValidationError("parameter is not declared by this job")
    return given
prepare_parameters=params
def positive(v:Any)->bool:return isinstance(v,int) and not isinstance(v,bool) and v>0
def valid_receipt(x:Any)->dict[str,Any]:
    if not isinstance(x,dict) or x.get("version")!=VERSION or not isinstance(x.get("controller"),str) or not isinstance(x.get("job"),str) or not isinstance(x.get("request_id"),str) or not x["request_id"] or x.get("phase") not in {"intent","submission_uncertain","queued","building","terminal","cancel_requested"}:raise ReceiptError("malformed receipt")
    for key in ("queue_id","build_number"):
        if key not in x or (x[key] is not None and not positive(x[key])):raise ReceiptError("malformed receipt identity")
    result=x.get("jenkins_result")
    if result is not None and (not isinstance(result,str) or result not in TERMINAL|{"QUEUE_CANCELLED"}):raise ReceiptError("malformed receipt result")
    if (x["phase"]=="terminal") != (result is not None):raise ReceiptError("inconsistent terminal receipt")
    if result in TERMINAL and x["build_number"] is None:raise ReceiptError("terminal build identity absent")
    if result=="QUEUE_CANCELLED" and (x["queue_id"] is None or x["build_number"] is not None):raise ReceiptError("inconsistent queue cancellation")
    if x["phase"]=="queued" and (x["queue_id"] is None or x["build_number"] is not None):raise ReceiptError("inconsistent queue receipt")
    if x["phase"]=="building" and x["build_number"] is None:raise ReceiptError("build identity absent")
    if x["phase"]=="intent" and (x["queue_id"] is not None or x["build_number"] is not None):raise ReceiptError("intent already has remote identity")
    if x["phase"]=="submission_uncertain" and x["build_number"] is not None:raise ReceiptError("uncertain receipt already has build")
    if x["phase"]=="cancel_requested" and x["queue_id"] is None and x["build_number"] is None:raise ReceiptError("cancel receipt lacks identity")
    for key in ("cancel_intent","queue_cancel_attempted"):
        if key in x and not isinstance(x[key],bool):raise ReceiptError("malformed cancellation intent")
    if "stop_attempted" in x and (not positive(x["stop_attempted"]) or x["stop_attempted"]!=x["build_number"]):raise ReceiptError("malformed cancellation identity")
    col=x.get("collection")
    if not isinstance(col,dict) or col.get("status") not in {"not_started","complete","partial","timeout","transport_unknown"} or not isinstance(col.get("artifacts"),list):raise ReceiptError("malformed receipt collection")
    if col["status"]=="complete" and (x["phase"]!="terminal" or x["build_number"] is None):raise ReceiptError("complete collection lacks terminal build")
    for entry in col["artifacts"]:
        if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or entry.get("status") not in {"error","skipped","downloaded"}:raise ReceiptError("malformed receipt artifact")
        if entry["status"]=="downloaded" and "bytes" not in entry:raise ReceiptError("download byte count absent")
        if col["status"]=="complete" and entry["status"]!="downloaded":raise ReceiptError("complete collection contains omissions")
        if "bytes" in entry and (not isinstance(entry["bytes"],int) or isinstance(entry["bytes"],bool) or entry["bytes"]<0):raise ReceiptError("malformed byte count")
    if x.get("deadline_at") is not None and (not isinstance(x["deadline_at"],(int,float)) or isinstance(x["deadline_at"],bool) or not math.isfinite(x["deadline_at"])):raise ReceiptError("malformed receipt deadline")
    validate_controller(x["controller"],True);job_path(x["job"]);return x
@contextmanager
def parent_fd(p:Path,create:bool=True):
    """Open p's parent component-by-component; never follow an ancestor."""
    parts=p.parent.parts
    start="/" if p.is_absolute() else "."
    fd=os.open(start,os.O_RDONLY|os.O_DIRECTORY)
    skip=1 if p.is_absolute() else 0
    try:
        for part in parts[skip:]:
            if part in {"", ".", ".."}:raise ReceiptError("unsafe parent component")
            if create:
                try:os.mkdir(part,0o700,dir_fd=fd)
                except FileExistsError:pass
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        yield fd,p.name
    finally:os.close(fd)
def read_fd(d,n):
    fd=os.open(n,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=d)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>RECEIPT_BYTES:raise ReceiptError("receipt must be a bounded regular file")
        data=bytearray()
        while len(data)<=RECEIPT_BYTES:
            b=os.read(fd,min(65536,RECEIPT_BYTES+1-len(data)))
            if not b:return bytes(data)
            data.extend(b)
        raise ReceiptError("receipt exceeded byte bound")
    finally:os.close(fd)

def read_at(p:Path)->bytes:
    if ACTIVE is not None and ACTIVE["path"]==p:
        verify_owner(p)
        return read_fd(ACTIVE["dir"],ACTIVE["name"])
    with parent_fd(p,False) as (d,n):return read_fd(d,n)

def verify_owner(p):
    with parent_fd(p,False) as (d,_):
        if (os.fstat(d).st_dev,os.fstat(d).st_ino)!=ACTIVE["generation"]:raise ReceiptError("receipt parent replaced; original identity retained")
    try:data=read_fd(ACTIVE["dir"],ACTIVE["name"])
    except FileNotFoundError:data=None
    if data!=ACTIVE["bytes"]:raise ReceiptError("receipt ownership changed")

@contextmanager
def locked(p:Path):
    if ACTIVE is not None and ACTIVE["path"]==p:
        verify_owner(p)
        yield ACTIVE["dir"],ACTIVE["name"]
        return
    with parent_fd(p) as (d,n):
        fd=os.open(n+".lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=d)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise ReceiptError("lock must be regular")
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield d,n
        finally:os.close(fd)

@contextmanager
def operation_lock(p:Path,c:Client,deadline:float,offline:bool=False):
    """Pin parent generation and both cooperative locks through publication."""
    global ACTIVE
    c.end=time.monotonic()+finite(deadline,"deadline")
    c.expiry=time.time()+deadline
    with parent_fd(p) as (d,n):
        held=[]
        try:
            # Read only bounded regular bytes before lock admission, then
            # reread after acquiring ownership (another owner may have updated).
            try:prior=valid_receipt(json.loads(read_fd(d,n))).get("deadline_at")
            except FileNotFoundError:prior=None
            if prior is not None and not offline:
                c.expiry=min(c.expiry,prior);c.end=min(c.end,time.monotonic()+prior-time.time())
            for suffix in (".operation.lock",".lock"):
                fd=os.open(n+suffix,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=d);held.append(fd)
                if not stat.S_ISREG(os.fstat(fd).st_mode):raise ReceiptError("lock must be regular")
                while True:
                    c.remaining()
                    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:time.sleep(min(.01,c.remaining()))
            try:data=read_fd(d,n)
            except FileNotFoundError:data=None
            st=os.fstat(d)
            ACTIVE={"path":p,"dir":d,"name":n,"generation":(st.st_dev,st.st_ino),"bytes":data}
            verify_owner(p)
            if data is not None and not offline:
                x=valid_receipt(json.loads(data));prior=x.get("deadline_at")
                if prior is not None:
                    c.expiry=min(c.expiry,prior);c.end=min(c.end,time.monotonic()+prior-time.time())
                c.remaining()
                if prior is None or c.expiry<prior:x["deadline_at"]=c.expiry;save_receipt(p,x)
            yield
        finally:
            ACTIVE=None
            for fd in reversed(held):os.close(fd)

def write_receipt(p:Path,x:dict[str,Any],create:bool=False)->None:
    valid_receipt(x)
    with locked(p) as (d,n):
        data=(json.dumps(x,sort_keys=True)+"\n").encode()
        if len(data)>RECEIPT_BYTES:raise ReceiptError("receipt exceeds byte bound")
        if not create:
            old=valid_receipt(json.loads(read_fd(d,n)))
            if any(old[k]!=x[k] for k in ("request_id","controller","job")) or any(old[k] is not None and old[k]!=x[k] for k in ("queue_id","build_number","jenkins_result")):
                raise ReceiptError("receipt identity/result cannot regress")
        tmp=".receipt-"+secrets.token_hex(12)
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)
        try:
            with os.fdopen(fd,"wb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            if ACTIVE is not None:verify_owner(p)
            if create:
                try:os.link(tmp,n,src_dir_fd=d,dst_dir_fd=d,follow_symlinks=False)
                except FileExistsError as e:raise ReceiptError("receipt exists; reattach rather than resubmit") from e
                os.unlink(tmp,dir_fd=d)
            else:os.replace(tmp,n,src_dir_fd=d,dst_dir_fd=d)
            os.fsync(d)
            if ACTIVE is not None:ACTIVE["bytes"]=data
        finally:
            try:os.unlink(tmp,dir_fd=d)
            except FileNotFoundError:pass
def open_receipt(p:Path,c:str,j:str)->dict[str,Any]:
    x={"version":VERSION,"request_id":secrets.token_hex(16),"controller":validate_controller(c,True),"job":j,"phase":"intent","queue_id":None,"build_number":None,"jenkins_result":None,"deadline_at":None,"collection":{"status":"not_started","artifacts":[]}};write_receipt(p,x,True);return x
def load_receipt(p:Path)->dict[str,Any]:
    try:
        return valid_receipt(json.loads(read_at(p)))
    except (OSError,json.JSONDecodeError,UnicodeDecodeError) as e:raise ReceiptError("receipt unreadable") from e
def save_receipt(p:Path,x:dict[str,Any])->None:write_receipt(p,x)
def bind(x:dict[str,Any],c:Controller,j:str)->None:
    if x["controller"]!=c.base or x["job"]!=j:raise ReceiptError("receipt does not bind this controller/job")
def queue(c:Controller,n:int)->str:return c.base+f"/queue/item/{n}"
def build(c:Controller,j:str,n:int)->str:return c.base+job_path(j)+f"/{n}"
def outcome(x:dict[str,Any])->str:
    if x.get("building") is True:
        if x.get("result") is not None:raise TransportError("running build has terminal result")
        return "BUILDING"
    result=x.get("result")
    if x.get("building") is not False or not isinstance(result,str) or result not in TERMINAL:
        raise TransportError("incomplete or unsupported build observation")
    return result

def observed_build(c,j,n,meta):
    if not positive(meta.get("number")) or meta["number"]!=n or not isinstance(meta.get("url"),str):raise TransportError("build observation identity mismatch")
    c.controller.checked(meta["url"],"build",j,n)
    return outcome(meta)

def submit(c:Client,p:Path,j:str,given:dict[str,str])->dict[str,Any]:
    x=open_receipt(p,c.controller.base,j)
    x["deadline_at"]=getattr(c,"expiry",time.time()+c.timeout);save_receipt(p,x)
    m=c.api_get(controller_url(c.controller.base,j)+"/api/json")
    if not isinstance(m.get("url"),str):raise TransportError("job observation lacks identity")
    c.controller.checked(m["url"],"job",j)
    g=params(m,given)
    parameterized=any(a.get("parameterDefinitions") for a in m.get("property",[]))
    u=controller_url(c.controller.base,j)+("/buildWithParameters" if parameterized else "/build")
    # Publish uncertainty BEFORE POST. A crash/lost response can never reopen
    # the admission window; an existing intent is also never auto-submitted.
    x["phase"]="submission_uncertain";save_receipt(p,x)
    s,h,_=c.request("POST",u,urlencode(g).encode() if parameterized else None)
    loc=h.get("Location") or h.get("location")
    if s not in {200,201,202} or not loc:raise ReceiptError("submission uncertain; reconcile an exact build without resubmit")
    raw=urlsplit(loc)
    if raw.query or raw.fragment or raw.username or raw.password:raise ReceiptError("Location has unsafe components")
    q=urlsplit(urljoin(u,loc))
    try:n=int(q.path.rstrip("/").split("/")[-1])
    except ValueError:raise ReceiptError("Location lacks numeric queue identity")
    if not positive(n):raise ReceiptError("Location lacks positive queue identity")
    c.controller.checked(urlunsplit((q.scheme,q.netloc,q.path,"","")),"queue",ident=n)
    x.update(phase="queued",queue_id=n);save_receipt(p,x);return x

def reconcile(c:Client,p:Path,j:str,n:int)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j)
    if not positive(n) or x.get("build_number") is not None or x["phase"]=="terminal":raise ReceiptError("exact reconciliation is unavailable for this receipt")
    meta=c.api_get(build(c.controller,j,n)+"/api/json");result=observed_build(c,j,n,meta)
    x.update(phase="building" if result=="BUILDING" else "terminal",build_number=n,jenkins_result=None if result=="BUILDING" else result)
    save_receipt(p,x);return x

def stop_build(c,p,j,x):
    n=x["build_number"]
    if x.get("stop_attempted")==n:return
    # Preserve attempted intent before POST; uncertain stop is observed on
    # resume, not blindly repeated after a lost response.
    x["stop_attempted"]=n;save_receipt(p,x)
    status,_,_=c.request("POST",build(c.controller,j,n)+"/stop")
    x["cancel_requested_status"]=status;save_receipt(p,x)
    if status not in {200,201,202}:raise ReceiptError("Jenkins did not accept build cancellation")

def wait(c:Client,p:Path,j:str,deadline:float,poll:float)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j)
    if x["phase"]=="terminal":return x
    if c.end is None:c.end=time.monotonic()+finite(deadline,"deadline")
    finite(poll,"poll")
    try:
        while True:
            if x.get("build_number") is not None:
                if x.get("cancel_intent"):stop_build(c,p,j,x)
                b=c.api_get(build(c.controller,j,x["build_number"])+"/api/json");o=observed_build(c,j,x["build_number"],b)
                if o!="BUILDING":x.update(phase="terminal",jenkins_result=o);save_receipt(p,x);return x
            elif x.get("queue_id") is not None:
                q=c.api_get(queue(c.controller,x["queue_id"])+"/api/json")
                if not positive(q.get("id")) or q["id"]!=x["queue_id"]:raise TransportError("queue identity mismatch")
                if "url" in q:c.controller.checked(q["url"],"queue",ident=x["queue_id"])
                if "cancelled" in q and not isinstance(q["cancelled"],bool):raise TransportError("malformed queue cancellation")
                e=q.get("executable")
                if e is not None:
                    if not isinstance(e,dict) or not positive(e.get("number")) or not isinstance(e.get("url"),str) or q.get("cancelled") is True:raise TransportError("malformed queue executable")
                    number=e["number"];c.controller.checked(e["url"],"build",j,number)
                    x.update(phase="building",build_number=number);save_receipt(p,x)
                    if x.get("cancel_intent"):stop_build(c,p,j,x)
                elif q.get("cancelled") is True:
                    x.update(phase="terminal",jenkins_result="QUEUE_CANCELLED");save_receipt(p,x);return x
            else:raise ReceiptError("uncertain submit requires explicit exact-build reconciliation")
            time.sleep(min(poll,c.remaining()))
    except (TimeoutError,TransportError,ValidationError) as e:
        x["local_error"]="deadline" if isinstance(e,TimeoutError) else "transport_or_identity"
        save_receipt(p,x)
        raise ReceiptError("local observation failed; remote identity retained; inspect show/reconcile") from e

def cancel(c:Client,p:Path,j:str,d:float)->dict[str,Any]:
    x=load_receipt(p);bind(x,c.controller,j)
    if x["phase"]=="terminal":return x
    if c.end is None:c.end=time.monotonic()+finite(d,"deadline")
    if x.get("build_number") is None and x.get("queue_id") is None:raise ReceiptError("no identity to cancel")
    x.update(phase="cancel_requested",cancel_intent=True);save_receipt(p,x)
    if x.get("build_number") is not None:stop_build(c,p,j,x)
    elif not x.get("queue_cancel_attempted"):
        x["queue_cancel_attempted"]=True;save_receipt(p,x)
        status,_,_=c.request("POST",c.controller.base+"/queue/cancelItem",urlencode({"id":x["queue_id"]}).encode())
        x["cancel_requested_status"]=status;save_receipt(p,x)
        if status not in {200,201,202}:raise ReceiptError("Jenkins did not accept queue cancellation")
    return wait(c,p,j,d,.01)
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
    # Re-check immediately at the write boundary.  This closes the check/use
    # gap for a swapped output-root before the final O_NOFOLLOW create.
    with parent_fd(p,False) as (d,n):
        fd=os.open(n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)
        try:os.write(fd,b);os.fsync(fd);os.fsync(d)
        finally:os.close(fd)
def store_artifact(root:Path,rel:str,b:bytes,opened=None)->None:
    """Publish through the same opened parent used to admit the download."""
    if not rel or rel.startswith("/") or any(bad(x) for x in rel.split("/")):raise ValidationError("unsafe artifact path")
    if opened is None:
        with parent_fd(root/rel) as destination:store_artifact(root,rel,b,destination)
        return
    d,n=opened
    with parent_fd(root/rel,False) as (current,_):
        before,after=os.fstat(d),os.fstat(current)
        if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise ReceiptError("artifact parent replaced")
    fd=os.open(n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=d)
    try:
        with os.fdopen(fd,"wb") as stream:stream.write(b);stream.flush();os.fsync(stream.fileno())
        os.fsync(d)
    except BaseException:
        os.unlink(n,dir_fd=d);raise
def collect(c:Client,p:Path,j:str,out:Path,log:int,per:int,total:int,count:int,d:float)->dict[str,Any]:
    if any(not positive(v) for v in (log,per,total,count)):raise ValidationError("collection bounds must be positive")
    x=load_receipt(p);bind(x,c.controller,j)
    if x["phase"]!="terminal" or x.get("build_number") is None:raise ReceiptError("collection needs terminal exact build")
    if c.end is None:c.end=time.monotonic()+finite(d,"deadline")
    base=build(c.controller,j,x["build_number"]);man=[];failed=False
    def checkpoint():
        x["collection"]={"status":"partial","artifacts":man};save_receipt(p,x)
    def get(label,u,cap,rel):
        nonlocal failed
        c.last_bytes=0
        try:
            # Admit local paths before any GET. No artifact URL is interpreted
            # before this validation, including percent-encoded traversal.
            if not rel or rel.startswith("/") or any(bad(z) for z in rel.split("/")):raise ValidationError("unsafe artifact path")
            with parent_fd(out/rel) as (fd,name):
                try:os.stat(name,dir_fd=fd,follow_symlinks=False)
                except FileNotFoundError:pass
                else:raise ValidationError("artifact collision")
                status,_,body=c.request("GET",u,limit=cap)
                if status!=200:raise TransportError("download HTTP error")
                store_artifact(out,rel,body,(fd,name))
            man.append({"path":label,"status":"downloaded","bytes":c.last_bytes})
        except (JenkinsError,OSError,TimeoutError):
            failed=True
            man.append({"path":label,"status":"error","bytes":c.last_bytes,"reason":"limit_or_transfer_or_path_error"})
        checkpoint()
        return c.last_bytes
    checkpoint()
    try:
        meta=c.api_get(base+"/api/json")
        result=observed_build(c,j,x["build_number"],meta)
        if result!=x["jenkins_result"]:raise TransportError("terminal observation changed")
        get("console.log",base+"/consoleText",log,"console.log")
        arts=meta.get("artifacts")
        if not isinstance(arts,list):
            man.append({"path":"artifacts","status":"error","bytes":0,"reason":"malformed_metadata"})
            raise TransportError("malformed artifact metadata")
        used=0
        # API cap bounds the input list; output manifest stays bounded even
        # for an arbitrarily large operator-supplied count.
        admitted=min(count,1024)
        for idx,a in enumerate(arts):
            if idx>=admitted:
                failed=True;man.append({"path":"artifacts","status":"skipped","bytes":0,"reason":"count_limit","omitted":len(arts)-idx});break
            rel=a.get("relativePath") if isinstance(a,dict) else None
            if not isinstance(rel,str):
                failed=True;man.append({"path":"unknown","status":"error","bytes":0,"reason":"malformed_metadata"});continue
            if used>=total:
                failed=True;man.append({"path":"artifacts","status":"skipped","bytes":0,"reason":"aggregate_limit","omitted":len(arts)-idx});break
            used+=get(rel,base+"/artifact/"+"/".join(quote(z,safe="") for z in rel.split("/")),min(per,total-used),rel)
        x["collection"]={"status":"partial" if failed else "complete","artifacts":man};save_receipt(p,x);return x
    except (JenkinsError,OSError,TimeoutError):checkpoint();raise
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--controller",required=True);p.add_argument("--job",required=True);p.add_argument("--receipt",type=Path,required=True);p.add_argument("--username-env",default="JENKINS_USERNAME");p.add_argument("--token-env",default="JENKINS_API_TOKEN");p.add_argument("--allow-http",action="store_true");p.add_argument("--timeout",type=float,default=15);p.add_argument("--deadline",type=float,default=900);p.add_argument("--poll",type=float,default=2);p.add_argument("--api-bytes",type=int,default=1_000_000);s=p.add_subparsers(dest="command",required=True);z=s.add_parser("submit");z.add_argument("--parameter",action="append",default=[]);s.add_parser("wait");s.add_parser("cancel");z=s.add_parser("reconcile");z.add_argument("--build-number",type=int,required=True);s.add_parser("show");z=s.add_parser("collect");z.add_argument("--output",type=Path,required=True);z.add_argument("--log-bytes",type=int,default=1_000_000);z.add_argument("--artifact-bytes",type=int,default=10_000_000);z.add_argument("--artifact-total-bytes",type=int,default=50_000_000);z.add_argument("--artifact-count",type=int,default=32);a=p.parse_args(argv)
    try:
        finite(a.timeout,"timeout");finite(a.deadline,"deadline");finite(a.poll,"poll")
        if a.api_bytes<=0:raise ValidationError("api-bytes must be positive")
        c=Client(Controller(validate_controller(a.controller,a.allow_http)),os.environ.get(a.username_env),os.environ.get(a.token_env),a.timeout,a.api_bytes)
        with operation_lock(a.receipt,c,a.deadline,offline=a.command=="show"):
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
        print(json.dumps(x if a.command=="show" else {"request_id":x["request_id"],"phase":x["phase"],"jenkins_result":x.get("jenkins_result"),"collection":x["collection"]["status"]}))
        return 0 if (a.command in {"submit","show","reconcile"} or (x.get("jenkins_result")=="SUCCESS" and (a.command in {"wait","cancel"} or x["collection"]["status"]=="complete"))) else 2
    except (JenkinsError,ValueError,OverflowError,TimeoutError,OSError,TypeError,RecursionError) as e:
        message=str(e) if isinstance(e,JenkinsError) else "local input/I/O/deadline failure; inspect receipt with show"
        print("jenkins-jobs: "+message[:240],file=sys.stderr);return 3
if __name__=="__main__":raise SystemExit(main())
