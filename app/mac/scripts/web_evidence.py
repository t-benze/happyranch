#!/usr/bin/env python3
"""Private generated-web custody. Unsigned observations, never release approval.

Both publish_build and verify_web re-open the same original inputs and validate
relations. Python alone owns stat integers. JS sees opaque subject tokens only.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
from dataclasses import asdict
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import tarfile
import unicodedata

A = runpy.run_path(str(Path(__file__).with_name('asset_evidence.py')))
MiB = 1024**2
CAP = 16*MiB
TOOL = 32*MiB
MAX_ROWS = 32768
MAX_OBSERVATIONS = 262144
BUILD_INPUT = 96*MiB
BUILD_ENVELOPE = 112*MiB
BUILD_ADDITION = 16*MiB
BUILD_NODES = 12582912
BODY_WORK = 1024*MiB
RESOURCE_PROFILE = 'web-private-plain@2'
COMPLETION_LEAF = 'web-completion.jsonl'


def web_bounded(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        with A['budget_scope']('web-evidence-v1'):
            return function(*args, **kwargs)
    return call

EXTENSIONS = {'.html','.js','.jsx','.mjs','.cjs','.ts','.tsx','.mts','.cts','.vue','.svelte','.astro','.md','.mdx','.json','.svg'}
EXCLUSIONS = ('web/vendor/','web/node_modules/','web/dist/')
POLICY = 'pinned-web-content@1'
VITE_VERSION = '5.4.21+hr.css-relation.1'
ARCHIVES = {'vite':'5.4.21','rollup':'4.60.3','@vitejs/plugin-react':'4.7.0','@tailwindcss/vite':'4.3.0','@tailwindcss/node':'4.3.0','@tailwindcss/oxide':'4.3.0','tailwindcss':'4.3.0','lightningcss':'1.32.0','esbuild':'0.21.5','@esbuild/darwin-arm64':'0.21.5','@rollup/rollup-darwin-arm64':'4.60.3','@tailwindcss/oxide-darwin-arm64':'4.3.0','lightningcss-darwin-arm64':'1.32.0','postcss':'8.5.14'}
KINDS = {'esbuild-attempt':256*1024,'esbuild-preparation':256*1024,'attempt':256*1024,'context':256*1024,'css-inputs':8*MiB,'build':BUILD_ENVELOPE,'dist':8*MiB,'notices':8*MiB,'failure':256*1024}
BIND_KEYS = {'candidate','roots','run_id','venue','asset_context_sha256','asset_source_sha256','toolchains_sha256','originals'}

class WebError(ValueError):
    pass

def need(value, code, subject=''):
    if not value:
        raise WebError('WEB_'+code+(':'+str(subject) if subject else ''))

def keys(value, expected):
    need(type(value) is dict and set(value)==set(expected),'CLOSED_SCHEMA',str(expected))

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value, cap=CAP, *, node_cap=500000, size_only=False):
    # Stream bounded canonical pieces, preserving exact descriptor integers.
    output=bytearray();nodes=0;size=0
    def append(piece):
        nonlocal size
        raw=piece.encode('utf-8','strict')
        size+=len(raw);need(size<=cap,'RECORD_CAP')
        if not size_only:output.extend(raw)
    def integer_text(number):
        negative=number<0;number=abs(number);parts=[]
        while number:
            number,part=divmod(number,10**1000);parts.append(str(part))
        result='0' if not parts else parts[-1]+''.join(part.zfill(1000) for part in reversed(parts[:-1]))
        return ('-' if negative else '')+result
    def emit(item,depth=0):
        nonlocal nodes
        nodes+=1;need(nodes<=node_cap and depth<=32,'JSON_COMPLEXITY_CAP')
        if item is None:append('null')
        elif type(item) is bool:append('true' if item else 'false')
        elif type(item) is int:append(integer_text(item))
        elif isinstance(item,str):
            # Escape in bounded pieces; never form an unbounded escaped string.
            append('"')
            for offset in range(0,len(item),8192):
                append(json.dumps(item[offset:offset+8192],ensure_ascii=False,allow_nan=False)[1:-1])
            append('"')
        elif isinstance(item,list):
            need(depth<32,'JSON_COMPLEXITY_CAP');append('[')
            for i,child in enumerate(item):
                if i:append(',')
                emit(child,depth+1)
            append(']')
        elif isinstance(item,dict):
            need(depth<32 and all(isinstance(key,str) for key in item),'JSON_KEY_INVALID');append('{')
            for i,key in enumerate(sorted(item)):
                if i:append(',')
                emit(key,depth+1);append(':');emit(item[key],depth+1)
            append('}')
        else:raise WebError('WEB_JSON_SCALAR')
    emit(value)
    return size if size_only else bytes(output)

def parse(raw, cap=CAP, *, build=False, charged=False):
    if not charged:A['_budget'].get().charge_json(len(raw))
    need(len(raw)<=cap,'JSON_CAP')
    text=raw.decode('utf-8','strict')
    depth=nodes=0;quoted=escaped=scalar=False
    for c in text:
        if quoted:
            if escaped:escaped=False
            elif c=='\\':escaped=True
            elif c=='"':quoted=False
        elif c=='"':quoted=True;nodes+=1;scalar=False
        elif c in '[{':depth+=1;nodes+=1;scalar=False
        elif c in ']}':depth-=1;scalar=False
        elif c in ' \n\r\t,:':scalar=False
        elif not scalar:nodes+=1;scalar=True
        need(depth<=32 and nodes<=(BUILD_NODES if build else 500000),'JSON_COMPLEXITY_CAP')
    def pairs(rows):
        out={}
        for k,v in rows:
            need(k not in out,'JSON_DUPLICATE_KEY',k);out[k]=v
        return out
    def integer(text):
        # Bounded exact conversion, independent of interpreter digit guard.
        negative=text.startswith('-');digits=text[1:] if negative else text
        number=0
        for offset in range(0,len(digits),1000):
            part=digits[offset:offset+1000];number=number*10**len(part)+int(part)
        return -number if negative else number
    def refuse(_):raise WebError('WEB_JSON_NUMBER_INVALID')
    value=json.loads(text,object_pairs_hook=pairs,parse_int=integer,parse_float=refuse,parse_constant=refuse)
    def visit(v):
        if isinstance(v,str):v.encode('utf-8','strict')
        elif isinstance(v,dict):
            for k,c in v.items():visit(k);visit(c)
        elif isinstance(v,list):
            for c in v:visit(c)
        else:need(v is None or type(v) in (bool,int),'JSON_SCALAR')
    visit(value);return value

def digest_shape(value):
    A['hex_value'](value);return value

def js_size(value, cap=CAP):
    need(type(value) is int and 0<=value<=min(cap,2**53-1),'JS_NUMBER_DOMAIN');return value

def path_name(value):
    need(isinstance(value,str) and not value.startswith('/') and '\\' not in value,'PATH_INVALID')
    parts=value.split('/')
    need(len(value.encode())<=4096 and len(parts)<=16 and all(p not in ('','.','..') and len(p.encode())<=255 for p in parts),'PATH_INVALID',value)
    need(not any(ord(c)<32 or ord(c)==127 for c in value),'PATH_INVALID',value)
    return value


def is_absent(root, name):
    try:
        with A['parent_at'](root,name) as (parent,leaf):
            os.stat(leaf,dir_fd=parent,follow_symlinks=False)
        return False
    except FileNotFoundError:
        return True

def decode(raw):
    return raw.decode('utf-8','replace')

def descriptor_token(row):
    A['subject_shape'](row['descriptor'])
    return {'schema':'web-subject-token@1','subject_id':row['subject_id'],'descriptor_sha256':sha(canonical(row['descriptor'])),'content':{'size':row['descriptor']['size'],'sha256':row['descriptor']['sha256']}}


class SnapshotDiagnostics:
    """Optional content-free writes to borrowed fd3; never protocol/acceptance."""
    def __init__(self,call,allowance):
        self.disabled=True;self.used=0;self.records=0
        # Invalid/missing private metadata disables diagnostics, not the helper.
        if not isinstance(call,str) or not re.fullmatch(r'[1-9][0-9]{0,4}',call):return
        if not isinstance(allowance,str) or not re.fullmatch(r'[1-9][0-9]{0,3}',allowance):return
        self.call=int(call);self.allowance=int(allowance)
        if self.call>65536 or self.allowance>3072:return
        try:os.fstat(3)
        except (OSError,ValueError):return
        self.disabled=False

    def mark(self,phase,event):
        if self.disabled:return
        try:
            if phase not in ('bindings','assets','toolchain','source','stdin','mutable','context','create','reply') or event not in ('start','end'):
                self.disabled=True;return
            raw=f'[packaging-snapshot] call={self.call} phase={phase} event={event}\n'.encode('ascii')
            if self.records>=24 or len(raw)>128 or self.used+len(raw)>self.allowance:
                self.disabled=True;return
            # Charge attempted bytes before the single write. No retry or close
            # of borrowed fd3, including on short or failed delivery.
            self.used+=len(raw);self.records+=1
            if os.write(3,raw)!=len(raw):self.disabled=True
        except (OSError,ValueError):self.disabled=True


class CompleteBuildDiagnostics:
    """One optional terminal fd3 line, inside the original command budget."""
    def __init__(self,call,allowance):
        self.disabled=True;self.phase='admission';self.operation='caller';self.ordinal=0;self.size=0
        if not isinstance(call,str) or not re.fullmatch(r'[1-9][0-9]{0,4}',call):return
        if not isinstance(allowance,str) or not re.fullmatch(r'[1-9][0-9]{0,3}',allowance):return
        self.call=int(call);self.allowance=int(allowance)
        if self.call>65536 or self.allowance>1024:return
        try:
            os.fstat(3)
            self.budget=A['_budget'].get()
        except Exception:return
        self.disabled=False

    def position(self,operation,ordinal=0,size=0):
        self.operation=operation;self.ordinal=ordinal;self.size=size

    def enter_phase(self,phase):
        self.phase=phase;self.position('validation')

    def finish(self,event):
        if self.disabled:return
        self.disabled=True # At most one attempt, including short/failed output.
        try:
            phases=('admission','completion-body','prefix','prefix-context','checked-build-record','validation-context','validation','completion-write','reply')
            operations=('caller','bindings','asset-context','asset-source','toolchain-read','source-git','helper-read','validation','raw-read','subject-import','subject-import-read','subject-import-validation','selection-lookup','selection-json','lock-json','source-list','native-member-read','load-seal-read','load-seal-json','load-event-read','load-event-json','completion-write','token-json')
            if self.phase not in phases or self.operation not in operations+tuple(prefix+kind for prefix in ('record-read-','record-json-') for kind in KINDS) or event not in ('failed','success'):return
            # Read existing views only. No charge, scope exit, reset or receipt.
            asset=getattr(self.budget,'_asset',None)
            counters=(self.budget.regular,self.budget.json,asset.regular if asset is not None else -1,asset.json if asset is not None else -1)
            if any(type(n) is not int or not -1<=n<=2**53-1 for n in counters):return
            if any(type(n) is not int or not 0<=n<=2**53-1 for n in (self.ordinal,self.size)):return
            raw=(f'[packaging-complete] call={self.call} grant={self.allowance} event={event} phase={self.phase} operation={self.operation} subject_ordinal={self.ordinal} subject_size={self.size} regular={counters[0]} json={counters[1]} asset_regular={counters[2]} asset_json={counters[3]}\n').encode('ascii')
            if len(raw)>min(self.allowance,1024):return
            os.write(3,raw) # Attempt consumes the grant; never retry or close fd3.
        except Exception:pass # Diagnostic failure must preserve the original result.


class Session:
    def __init__(self,binding,diagnostics=None,terminal=None):
        self.terminal=terminal
        self.diagnostics=diagnostics
        if diagnostics:diagnostics.mark('bindings','start')
        keys(binding,BIND_KEYS);self.binding=binding
        keys(binding['candidate'],A['Candidate'].__dataclass_fields__)
        self.candidate=A['Candidate'](**binding['candidate'])
        keys(binding['roots'],A['Paths'].__dataclass_fields__)
        self.paths=A['Paths'](**{k:Path(v) for k,v in binding['roots'].items()})
        self.run=binding['run_id'];A['run_value'](self.run)
        need(binding['venue'] in ('native','portable_web'),'VENUE')
        self.originals=A['absolute'](binding['originals'])
        need(not any(self.originals.is_relative_to(r) or r.is_relative_to(self.originals) for r in (self.paths.repo,self.paths.output)),'ORIGINAL_ROOT_OVERLAP')
        if terminal:terminal.position('bindings')
        self.dirs,self.owner,self.preflight=A['bindings'](self.candidate,self.paths,self.run)
        self.binding_sha=sha(canonical(binding));self.subjects=[];self.subject_index={}
        if diagnostics:diagnostics.mark('bindings','end')
        if diagnostics:diagnostics.mark('assets','start')
        if binding['venue']=='native':
            need(sys.platform=='darwin' and __import__('platform').machine()=='arm64','NATIVE_PLATFORM_REQUIRED')
            for f in ('asset_context_sha256','asset_source_sha256','toolchains_sha256'):digest_shape(binding[f])
            with A['_budget'].get().asset_lane():
                if terminal:terminal.position('asset-context')
                A['check_context'](self.candidate,self.paths,self.run,binding['asset_context_sha256'])
                if terminal:terminal.position('asset-source')
                A['checked_source'](self.candidate,self.paths,self.run,binding['asset_context_sha256'],binding['asset_source_sha256'])
        else:
            need(binding['asset_context_sha256'] is None and binding['asset_source_sha256'] is None,'PORTABLE_NATIVE_PREFIX_FORBIDDEN')
            digest_shape(binding['toolchains_sha256'])
        if diagnostics:diagnostics.mark('assets','end')
        if diagnostics:diagnostics.mark('toolchain','start')
        if terminal:terminal.position('toolchain-read')
        with A['root_for'](self.paths.evidence_dir) as root:
            row=A['read_subject'](root,'toolchains.txt',A['TOOLCHAINS'])
        need(row['sha256']==binding['toolchains_sha256'],'TOOLCHAINS_MISMATCH')
        if diagnostics:diagnostics.mark('toolchain','end')
        if diagnostics:diagnostics.mark('source','start')
        if terminal:terminal.position('source-git')
        need(A['git'](self.paths,'rev-parse',self.candidate.source_sha+'^{tree}',cap=128).decode().strip()==self.candidate.source_tree,'SOURCE_TREE_MISMATCH')
        # The invoked implementation must be the pinned exported implementation,
        # even when the native wrapper calls its adjacent repository copy.
        if terminal:terminal.position('helper-read')
        with A['root_for'](Path(__file__).parent) as root:
            actual=A['read_subject'](root,'web_evidence.py')
        with A['root_for'](self.paths.exported_source) as root:
            exported=A['read_subject'](root,'app/mac/scripts/web_evidence.py')
            A['verify_git_subject'](self.candidate,self.paths,root,'app/mac/scripts/web_evidence.py',exported)
        need(actual['sha256']==exported['sha256'] and actual['size']==exported['size'],'HELPER_SOURCE_MISMATCH')
        if diagnostics:diagnostics.mark('source','end')

    def envelope(self,kind,payload,upstream):
        return {'schema':'web-evidence@1','kind':kind,'candidate':asdict(self.candidate),'run_id':self.run,'roots':A['paths_dict'](self.paths),'owner':self.owner,'preflight':self.preflight,'asset_context_sha256':self.binding['asset_context_sha256'],'toolchains_sha256':self.binding['toolchains_sha256'],'upstream':upstream,'payload':payload}

    def write(self,kind,payload,upstream=None):
        value=self.envelope(kind,payload,upstream or {})
        raw=canonical(value,KINDS[kind]-1,node_cap=BUILD_NODES if kind=='build' else 500000)+b'\n'
        if kind=='build':self.check_build_size(raw,payload)
        # Structural construction plus bounded emission replaces only the
        # producer self-parse. Independent consumers still parse every load.
        with A['root_for'](self.paths.evidence_dir) as root:
            row=A['create_once'](root,'web-'+kind+'.json',raw,A['ReadDomain'](KINDS[kind],True))
        return {'schema':'web-'+kind+'-token@1','record_sha256':row['sha256'],'context_binding_sha256':self.binding_sha}

    def check_build_size(self,raw,payload):
        need(type(payload) is dict,'CLOSED_SCHEMA','build payload')
        base={k:v for k,v in payload.items() if k not in ('subjects','source_tool_associations')}
        original=canonical(base,BUILD_INPUT,node_cap=BUILD_NODES,size_only=True)
        need(len(raw)-original<=BUILD_ADDITION,'BUILD_ADDITION_CAP')

    def load(self,kind,expected):
        if self.terminal:self.terminal.position('record-read-'+kind)
        digest_shape(expected)
        with A['root_for'](self.paths.evidence_dir) as root:
            row,raw=A['read_bytes'](root,'web-'+kind+'.json',A['ReadDomain'](KINDS[kind],True))
        if self.terminal:self.terminal.position('record-json-'+kind)
        A['_budget'].get().charge(len(raw),json_bytes=True)
        need(row['sha256']==expected,'RECORD_DIGEST_MISMATCH',kind)
        value=parse(raw,KINDS[kind],build=kind=='build',charged=True);keys(value,self.envelope(kind,None,{}))
        need({k:v for k,v in value.items() if k not in ('payload','upstream')}=={k:v for k,v in self.envelope(kind,None,{}).items() if k not in ('payload','upstream')},'CALLER_BINDING_MISMATCH',kind)
        if kind=='build':self.check_build_size(raw,value['payload'])
        if self.terminal:self.terminal.position('validation')
        return value

    def raw(self,root,name,cap=CAP):
        if self.terminal:self.terminal.position('subject-import-read' if self.terminal.ordinal else 'raw-read',self.terminal.ordinal,self.terminal.size)
        path_name(name)
        with A['root_for'](root) as handle:
            result=A['read_bytes'](handle,name,A['ReadDomain'](cap))
        if self.terminal:self.terminal.position('subject-import-validation' if self.terminal.ordinal else 'validation',self.terminal.ordinal,self.terminal.size)
        return result

    def intern_subject(self,root,name,cap=CAP):
        descriptor,raw=self.raw(root,name,cap)
        root_name='source' if root==self.paths.exported_source else 'evidence' if root==self.paths.evidence_dir else 'originals' if root==self.originals else None
        need(root_name,'SUBJECT_ROOT')
        index=(root_name,name)
        if index in self.subject_index:
            old=self.subjects[self.subject_index[index]]
            need(old['descriptor']==descriptor,'SUBJECT_DESCRIPTOR_MISMATCH',name)
            return descriptor_token(old),raw
        need(len(self.subjects)<MAX_ROWS,'SUBJECT_CAP')
        row={'subject_id':'s'+str(len(self.subjects)+1),'root':root_name,'descriptor':descriptor,'descriptor_sha256':sha(canonical(descriptor))}
        self.subject_index[index]=len(self.subjects);self.subjects.append(row)
        return descriptor_token(row),raw

    def import_subjects(self,rows):
        need(type(rows) is list and len(rows)<=MAX_ROWS,'SUBJECT_CAP')
        for ordinal,row in enumerate(rows,1):
            if self.terminal:
                size=row['descriptor'].get('size',0) if type(row) is dict and type(row.get('descriptor')) is dict else 0
                self.terminal.position('subject-import',ordinal,size)
            keys(row,{'subject_id','root','descriptor','descriptor_sha256'})
            need(row['subject_id']=='s'+str(ordinal),'SUBJECT_ID_INVALID')
            need(sha(canonical(row['descriptor']))==row['descriptor_sha256'],'SUBJECT_DESCRIPTOR_MISMATCH')
            token=descriptor_token(row)
            root={'source':self.paths.exported_source,'evidence':self.paths.evidence_dir,'originals':self.originals}.get(row['root'])
            need(root is not None,'SUBJECT_ROOT')
            actual,_=self.raw(root,row['descriptor']['relative'],TOOL)
            need(actual==row['descriptor'],'SUBJECT_DESCRIPTOR_MISMATCH',row['descriptor']['relative'])
            index=(row['root'],row['descriptor']['relative']);need(index not in self.subject_index,'SUBJECT_DUPLICATE')
            self.subject_index[index]=len(self.subjects);self.subjects.append(row)
            need(token['content']['size']<=TOOL,'SUBJECT_CAP')
            if self.terminal:self.terminal.position('validation')

    def resolve_subject_token(self,token):
        keys(token,{'schema','subject_id','descriptor_sha256','content'});keys(token['content'],{'size','sha256'});js_size(token['content']['size'],TOOL);digest_shape(token['content']['sha256']);digest_shape(token['descriptor_sha256'])
        need(token['schema']=='web-subject-token@1' and re.fullmatch(r's[1-9][0-9]{0,4}',token['subject_id']),'SUBJECT_TOKEN_INVALID')
        n=int(token['subject_id'][1:]);need(n<=len(self.subjects),'SUBJECT_TOKEN_UNKNOWN')
        row=self.subjects[n-1];need(descriptor_token(row)==token,'SUBJECT_TOKEN_MISMATCH')
        root={'source':self.paths.exported_source,'evidence':self.paths.evidence_dir,'originals':self.originals}[row['root']]
        actual,raw=self.raw(root,row['descriptor']['relative'],TOOL)
        need(actual==row['descriptor'],'SUBJECT_DESCRIPTOR_MISMATCH',row['descriptor']['relative'])
        return raw

    def source_list(self):
        if self.terminal:self.terminal.position('source-list')
        raw=A['git'](self.paths,'ls-tree','-rz',self.candidate.source_sha,'--','web',cap=8*MiB)
        if self.terminal:self.terminal.position('validation')
        need(not raw or raw.endswith(b'\0'),'SOURCE_LIST_TRUNCATED')
        result=[]
        for line in raw.split(b'\0')[:-1]:
            meta,name=line.split(b'\t',1);name=name.decode('utf-8');mode,kind,oid=meta.decode().split(' ')
            if name.startswith(EXCLUSIONS) or Path(name).suffix not in EXTENSIONS:continue
            need(mode in ('100644','100755') and kind=='blob','CONTENT_SOURCE_MODE',name)
            path_name(name);need(len(result)<MAX_ROWS,'CONTENT_INPUT_CAP');result.append((name,oid))
        result.sort(key=lambda row:row[0].encode())
        need(len(result)<=MAX_ROWS,'CONTENT_INPUT_CAP')
        # Complete selected namespace walk; no silently admitted export extras.
        selected=[]
        def walk(root,prefix):
            root.verify();before=sorted(os.listdir(root.fd))
            for entry in before:
                name=prefix+entry
                if (name+'/').startswith(EXCLUSIONS):continue
                info=os.stat(entry,dir_fd=root.fd,follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    with A['root_for'](self.paths.exported_source/name) as child:walk(child,name+'/')
                elif Path(name).suffix in EXTENSIONS:
                    need(stat.S_ISREG(info.st_mode),'CONTENT_SOURCE_MODE',name);path_name(name);need(len(selected)<MAX_ROWS,'CONTENT_INPUT_CAP');selected.append(name)
            need(sorted(os.listdir(root.fd))==before,'CONTENT_NAMESPACE_CHANGED')
        with A['root_for'](self.paths.exported_source/'web') as root:walk(root,'web/')
        need(set(selected)=={n for n,_ in result},'CSS_INPUT_SET_MISMATCH')
        return result

    def prepare_css_inputs(self):
        entries=[]
        for name,oid in self.source_list():
            token,raw=self.intern_subject(self.paths.exported_source,name)
            need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==oid,'SOURCE_BLOB_MISMATCH',name)
            entries.append({'source_token':token,'relative_path':name,'extension':Path(name).suffix[1:],'decoded_sha256':sha(decode(raw).encode())})
        return {'policy':POLICY,'source_tree':self.candidate.source_tree,'entries':entries}

    def lock(self):
        _,raw=self.intern_subject(self.paths.exported_source,'web/package-lock.json')
        if self.terminal:self.terminal.position('lock-json')
        result=parse(raw,8*MiB)
        if self.terminal:self.terminal.position('validation')
        return result

    def check_originals(self):
        lock=self.lock()['packages'];result=[]
        with A['root_for'](self.originals) as root:
            names=sorted(os.listdir(root.fd))
        need(names==sorted(n.replace('/','_')+'.tgz' for n in ARCHIVES),'ORIGINAL_INVENTORY')
        for package,version in ARCHIVES.items():
            token,raw=self.intern_subject(self.originals,package.replace('/','_')+'.tgz')
            entry=lock['node_modules/'+package]
            if package=='vite':
                _,metadata=self.intern_subject(self.paths.exported_source,'web/vendor/vite/UPSTREAM.json');metadata=parse(metadata)
                need(sha(raw)==metadata['original_sha256'],'ORIGINAL_INTEGRITY',package)
            else:
                need(entry['version']==version and entry['integrity']=='sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode(),'ORIGINAL_INTEGRITY',package)
            members={};expanded=0
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
                for item in archive:
                    need(len(members)<4096 and item.isfile() and item.name.startswith('package/'),'ORIGINAL_MEMBER_KIND',item.name)
                    path_name(item.name);need(item.name not in members and item.size<=TOOL,'ORIGINAL_MEMBER_INVALID',item.name)
                    expanded+=item.size;need(expanded<=128*MiB,'ORIGINAL_EXPANDED_CAP')
                    body=archive.extractfile(item).read(item.size+1);A['_budget'].get().charge(len(body));need(len(body)==item.size,'ORIGINAL_MEMBER_LENGTH')
                    members[item.name]={'size':len(body),'sha256':sha(body),'mode':item.mode}
            result.append({'package':package,'version':version,'archive':token,'members':members})
        return result

    def check_installed(self,originals):
        lock=self.lock()['packages'];tool=[]
        # All regular implementation and notice members, excluding documentation,
        # are compared directly. Vite's ONLY two changed members use repack hashes.
        for original in originals:
            package=original['package'];base='web/node_modules/'+package
            if '/darwin-arm64' in package or package.endswith('-darwin-arm64'):
                if self.binding['venue']=='portable_web':continue
            expected=original['members']
            if package=='vite':
                _,raw=self.raw(self.paths.exported_source,'web/vendor/vite/UPSTREAM.json');manifest=parse(raw)
                expected={r['path']:{k:r[k] for k in ('size','sha256','mode')} for r in manifest['members']}
                _,archive=self.raw(self.paths.exported_source,'web/vendor/vite/vite-'+VITE_VERSION+'.tgz')
                need(sha(archive)==manifest['archive_sha256'] and lock['node_modules/vite']['integrity']==manifest['integrity'],'VITE_REPACK_MISMATCH')
            for name,member in expected.items():
                relative=name[len('package/'):]
                if relative.endswith(('.md','.map')) and not Path(relative).name.lower().startswith(('license','notice')):continue
                if package=='esbuild' and relative=='bin/esbuild':continue # Optimized alias authenticated separately.
                token,_=self.intern_subject(self.paths.exported_source,base+'/'+relative,TOOL)
                need(token['content']=={k:member[k] for k in ('size','sha256')},'INSTALLED_ORIGINAL_MISMATCH',base+'/'+relative)
                tool.append({'package':package,'member':name,'token':token})
        for package,version in [('vite',VITE_VERSION),('rollup','4.60.3'),('esbuild','0.21.5'),('@tailwindcss/node','4.3.0'),('@tailwindcss/oxide','4.3.0'),('tailwindcss','4.3.0'),('lightningcss','1.32.0'),('postcss','8.5.14')]:
            _,raw=self.raw(self.paths.exported_source,'web/node_modules/'+package+'/package.json');need(parse(raw)['version']==version,'INSTALLED_VERSION',package)
        return tool

    def verify_esbuild_preparation(self,expected):
        record=self.load('esbuild-preparation',expected);payload=record['payload']
        keys(payload,{'selection','node','route','member','mode','pre_state','subjects','post_tokens','action'})
        route=payload['route'];keys(route,{'importer_lock_key','esbuild_lock_key','platform_lock_key','selected_relative','alias_relative'})
        need(route['importer_lock_key']=='node_modules/vite' and route['esbuild_lock_key']=='node_modules/esbuild' and route['alias_relative']=='web/node_modules/esbuild/bin/esbuild','ESBUILD_ROUTE')
        platform_name='@esbuild/darwin-arm64' if self.binding['venue']=='native' else '@esbuild/linux-x64'
        need(route['platform_lock_key']=='node_modules/'+platform_name and route['selected_relative']=='web/node_modules/'+platform_name+'/bin/esbuild','ESBUILD_ROUTE')
        pre=payload['pre_state'];keys(pre,{'selected','alias'})
        a,p=pre['alias'],pre['selected'];same=(a['dev'],a['ino'])==(p['dev'],p['ino'])
        need((same and a['nlink']==p['nlink']==2 and payload['action']=='dealias_known_optimized_alias') or (not same and a['nlink']==p['nlink']==1 and payload['action']=='already_independent'),'ESBUILD_ALIAS_LAYOUT_INVALID')
        for descriptor in (a,p):
            need(descriptor['sha256']==payload['member']['sha256'] and descriptor['size']==payload['member']['size'] and stat.S_IMODE(descriptor['mode'])==0o755,'ESBUILD_MEMBER_MISMATCH')
        self.import_subjects(payload['subjects'][:2])
        for token in payload['post_tokens'].values():self.resolve_subject_token(token)
        need(len(payload['subjects'])>=2 and payload['subjects'][0]['descriptor']['relative']==route['selected_relative'] and payload['subjects'][1]['descriptor']['relative']==route['alias_relative'],'ESBUILD_SUBJECTS')
        a,p=[r['descriptor'] for r in payload['subjects'][:2]]
        need((a['dev'],a['ino'])!=(p['dev'],p['ino']) and all(stat.S_IMODE(x['mode'])==0o755 and x['sha256']==payload['member']['sha256'] and x['size']==payload['member']['size'] for x in (a,p)),'ESBUILD_ALIAS_LAYOUT_INVALID')
        need(native_selection(self)==payload['selection'],'TOOL_SELECTION_CHANGED')
        need(self.subjects==payload['subjects'],'TOOL_SELECTION_SUBJECTS')
        check_selection_observation(payload['selection'],payload['node'],loaded=False)
        return payload


# Closed profiles derived from the retained, original-authenticated loaders.
# The hashes deliberately refuse a changed loader rather than interpreting an
# unreviewed resolver. No native module is loaded by the preparation probe.
NATIVE_LOADERS = {
    '@tailwindcss/oxide': ('index.js','d6e520baf1db574827bd2ea2f1d7585eea4d4c0934a55e01febea5ffd6e3f218'),
    'rollup': ('dist/native.js','93c3da450b25ba485bb3de20dded405aa9c105951e8d31964eeb49633c2b16ea'),
    'lightningcss': ('node/index.js','8b7f10ff14d65a382bd7041a747df4a0d4b7c4e22b2842bcaca2588cd86bb045'),
}
TOOL_OVERRIDES = ('NODE_OPTIONS','NODE_PATH','ESBUILD_BINARY_PATH','TAILWIND_OXIDE_PATH','NAPI_RS_NATIVE_LIBRARY_PATH','NAPI_RS_FORCE_WASI','NAPI_RS_ENFORCE_VERSION_CHECK','LIGHTNINGCSS_FORCE_WASM')


def selection_environment() -> None:
    for key in TOOL_OVERRIDES:
        need(not os.environ.get(key),'TOOL_OVERRIDE',key)


TOOL_IMPORTS = (
    ('web/scripts/packaging-tailwind.ts','tailwindcss','web/node_modules/tailwindcss/dist/lib.js'),
    ('web/scripts/packaging-tailwind.ts','@tailwindcss/oxide','web/node_modules/@tailwindcss/oxide/index.js'),
    ('web/scripts/packaging-tailwind.ts','@tailwindcss/node','web/node_modules/@tailwindcss/node/dist/index.js'),
    ('web/node_modules/@tailwindcss/node/dist/index.mjs','lightningcss','web/node_modules/lightningcss/node/index.js'),
    ('web/node_modules/vite/dist/node/chunks/dep-BK3b2jBa.js','rollup','web/node_modules/rollup/dist/rollup.js'),
)


def selection_lookups(s: Session,importers: list[str]) -> list[dict]:
    # resolve.paths performs no module evaluation or package-metadata read.
    # Use the actual pinned runtime's ancestor AND global lookup locations.
    script="""const {createRequire}=require('node:module');
if(process.versions.node.split('.')[0]!=='24')throw Error('Node24 required');
console.log(JSON.stringify(JSON.parse(process.argv[1]).map(importer=>({importer,paths:createRequire(importer).resolve.paths('hr-selection-probe')}))));"""
    if s.terminal:s.terminal.position('selection-lookup')
    result=A['process_capture'](['node','-e',script,canonical(importers,16384).decode()],s.paths.exported_source,16384,16384)
    A['_budget'].get().charge(len(result['stdout'])+len(result['stderr']))
    need(result['process']['exit']==0 and result['process']['quiescent'] and not result['process']['timed_out'] and not result['process']['overflow'],'TOOL_NODE_LOOKUPS')
    if s.terminal:s.terminal.position('selection-json')
    rows=parse(result['stdout'],16384)
    if s.terminal:s.terminal.position('validation')
    need(type(rows) is list and len(rows)==len(importers),'TOOL_NODE_LOOKUPS')
    for importer,row in zip(importers,rows):
        keys(row,{'importer','paths'})
        need(row['importer']==importer and type(row['paths']) is list and 0<len(row['paths'])<=128,'TOOL_NODE_LOOKUPS')
        for name in row['paths']:
            need(type(name) is str and Path(name).is_absolute() and os.path.normpath(name)==name and len(name.encode())<=4096,'TOOL_NODE_LOOKUPS')
    return rows


def native_selection(s: Session) -> dict:
    """Guarded filesystem admission, also run by each reopened reader.

    This establishes a local selected-input relation, not same-UID race safety
    or proof that arbitrary native code executed. Native loading is separately
    observed from the real build process's CJS cache.
    """
    selection_environment()
    native=s.binding['venue']=='native'
    platform_name,arch=('darwin','arm64') if native else ('linux','x64')
    suffix='darwin-arm64' if native else 'linux-x64-gnu'
    root=s.paths.exported_source;lock=s.lock()['packages'];routes=[];edges=[];unresolved=[];edge_excluded=[]
    importers=list(dict.fromkeys(['web/node_modules/'+package+'/'+member for package,(member,_) in NATIVE_LOADERS.items()]+[row[0] for row in TOOL_IMPORTS]))
    lookups=selection_lookups(s,[str(root/name) for name in importers])
    searches={row['importer']:row['paths'] for row in lookups}
    def absent(name: str) -> None:
        candidate=Path(name)
        if candidate.is_relative_to(root):
            with A['root_for'](root) as handle:
                need(is_absent(handle,str(candidate.relative_to(root))),'TOOL_ROUTE_ALTERNATIVE',name)
        else:
            try:
                with A['root_for'](candidate.parent,owned=False) as handle:
                    need(is_absent(handle,candidate.name),'TOOL_ROUTE_ALTERNATIVE',name)
            except FileNotFoundError:
                pass
    def exclude(stem: Path,paths: list[str],*,literal: bool=True,extensions: bool=True) -> None:
        # Node LOAD_AS_FILE appends extensions even to a .node/.cjs request.
        # Any existing literal stem (including a directory) refuses: never
        # interpret arbitrary alternative package.json/main/index contents.
        for suffix in ([''] if literal else [])+(['.js','.json','.node'] if extensions else []):
            name=str(stem)+suffix;absent(name);paths.append(name)
    def edge(importer: str,request: str,expected: str) -> None:
        # Earlier stems can be files OR directories. At the nominated package
        # stem, exclude sibling file routes but retain the authenticated dir.
        target=root/'web/node_modules'/request
        for directory in searches[str(root/importer)]:
            stem=Path(directory)/request
            exclude(stem,edge_excluded,literal=stem!=target)
            if stem==target:break
        else:need(False,'TOOL_NODE_LOOKUPS',request)
        edges.append({'importer':str(root/importer),'request':request,'selected':str(root/expected)})
    for package,(member,digest) in NATIVE_LOADERS.items():
        importer='web/node_modules/'+package+'/'+member
        loader,raw=s.intern_subject(root,importer,TOOL)
        need(loader['content']['sha256']==digest,'TOOL_LOADER_MISMATCH',importer)
        if package=='@tailwindcss/oxide':
            start=raw.index(b"} else if (process.platform === 'darwin') {") if native else raw.index(b"} else if (process.platform === 'linux') {")
            block=raw[start:raw.index(b"} else if (process.platform === 'freebsd') {",start)] if native else raw[start:]
            selected_package='@tailwindcss/oxide-'+suffix
            selected_name='tailwindcss-oxide.'+suffix+'.node'
            # Derive the ordered native candidates from this authenticated
            # branch; retain their order, including both universal choices.
            if native:
                # Universal attempts precede the architecture branch. The x64
                # branch is not an earlier arm64 candidate.
                universal=block[:block.index(b"if (process.arch === 'x64')")]
                arm64=block[block.index(b"} else if (process.arch === 'arm64') {"):]
                block=universal+arm64
            requests=re.findall(rb"(?:return|=) require\('([^']+)'\)",block)
            chosen=requests.index(selected_package.encode())
            earlier=[x.decode() for x in requests[:chosen] if not x.endswith(b'/package.json')]
            if not native:
                # The musl branch is not selected by the admitted glibc Node.
                earlier=['./'+selected_name]
            excluded=earlier+['./tailwindcss-oxide.wasi.cjs','@tailwindcss/oxide-wasm32-wasi']
        elif package=='rollup':
            need(b'const localName = `./rollup.${packageBase}.node`;' in raw and b'existsSync(path.join(__dirname, localName)) ? localName : `@rollup/rollup-${packageBase}`' in raw,'TOOL_LOADER_ROUTE')
            selected_package='@rollup/rollup-'+suffix;selected_name='rollup.'+suffix+'.node';excluded=['./'+selected_name]
        else:
            need(b"require(`lightningcss-${parts.join('-')}`)" in raw and b"require(`../lightningcss.${parts.join('-')}.node`)" in raw,'TOOL_LOADER_ROUTE')
            selected_package='lightningcss-'+suffix;selected_name='lightningcss.'+suffix+'.node';excluded=['../'+selected_name]
        excluded_paths=[]
        for request in excluded:
            if request.startswith('.'):
                stem=Path(os.path.normpath(str((root/importer).parent/request)))
                # Rollup requires the local request ONLY if existsSync on its
                # exact stem succeeds. Sibling extensions alone are unreachable.
                exclude(stem,excluded_paths,extensions=package!='rollup')
            else:
                for directory in searches[str(root/importer)]:
                    exclude(Path(directory)/request,excluded_paths)
            if package!='rollup':unresolved.append({'importer':str(root/importer),'request':request})
        selected='web/node_modules/'+selected_package+'/'+selected_name
        token,_=s.intern_subject(root,selected,TOOL)
        _,metadata=s.intern_subject(root,'web/node_modules/'+selected_package+'/package.json')
        if s.terminal:s.terminal.position('selection-json')
        metadata=parse(metadata);entry=lock['node_modules/'+selected_package]
        if s.terminal:s.terminal.position('validation')
        need(metadata['name']==selected_package and metadata['version']==entry['version'] and metadata['main']==('./' if package=='rollup' else '')+selected_name,'TOOL_PLATFORM_MEMBER',selected_package)
        original=None
        if native:
            archive_token,archive_raw=s.intern_subject(s.originals,selected_package.replace('/','_')+'.tgz')
            need(entry['integrity']=='sha512-'+base64.b64encode(hashlib.sha512(archive_raw).digest()).decode(),'TOOL_PLATFORM_ORIGINAL')
            with tarfile.open(fileobj=io.BytesIO(archive_raw),mode='r:gz') as archive:
                item=archive.getmember('package/'+selected_name)
                need(item.isfile() and item.size<=TOOL,'TOOL_PLATFORM_MEMBER')
                if s.terminal:s.terminal.position('native-member-read')
                body=archive.extractfile(item).read(TOOL+1);A['_budget'].get().charge(len(body))
                if s.terminal:s.terminal.position('validation')
            need(token['content']=={'size':len(body),'sha256':sha(body)},'TOOL_SELECTED_MEMBER_MISMATCH',selected)
            original={'archive':archive_token,'member':'package/'+selected_name,'content':token['content']}
        edge(importer,selected_package,selected)
        routes.append({'package':package,'importer':importer,'loader':loader,'platform_package':selected_package,'lock_integrity':entry['integrity'],'selected':selected,'member':token,'original':original,'excluded':excluded_paths})
    # Match real upstream consumers, not only nominated platform packages.
    for importer,request,expected in TOOL_IMPORTS:
        s.intern_subject(root,importer,TOOL);s.intern_subject(root,expected,TOOL);edge(importer,request,expected)
    return {'platform':platform_name,'arch':arch,'routes':routes,'edges':edges,'venue':s.binding['venue'],'lookups':lookups,'edge_excluded':edge_excluded,'unresolved':unresolved}


def selection_projection(plan: dict) -> dict:
    return {**{k:plan[k] for k in ('platform','arch','edges','unresolved')},'lookup_sha256':sha(canonical(plan['lookups'],16384))}


def check_selection_observation(plan: dict,observation: dict,*,loaded: bool) -> None:
    keys(observation,{'platform','arch','edges','unresolved','lookup_sha256','execPath','node','glibc','loaded'})
    need({k:observation[k] for k in ('platform','arch','edges','unresolved','lookup_sha256')}==selection_projection(plan),'TOOL_NODE_SELECTION')
    need(type(observation['node']) is str and observation['node'].split('.')[0]=='24' and isinstance(observation['execPath'],str) and Path(observation['execPath']).is_absolute(),'TOOL_NODE_RUNTIME')
    need(plan['platform']!='linux' or bool(observation['glibc']),'TOOL_LIBC')
    # Loaded paths are supplied by the actual Node build process, never by the
    # non-loading preflight. Exactly one admitted native member per loader.
    expected=[edge['selected'] for edge in plan['edges'][:3]] if loaded else []
    need(observation['loaded']==expected,'TOOL_NATIVE_CACHE')


def probe_selection(s: Session,plan: dict) -> dict:
    script="""const {createRequire}=require('node:module');
const {createHash}=require('node:crypto');
const p=JSON.parse(process.argv[1]);
const edges=p.edges.map(e=>({...e,selected:createRequire(e.importer).resolve(e.request)}));
const unresolved=p.unresolved.map(e=>{try{createRequire(e.importer).resolve(e.request);}catch(error){if(error.code==='MODULE_NOT_FOUND')return e;throw error;}throw Error('Unexpected excluded resolution');});
const lookups=[...new Set(edges.map(e=>e.importer))].map(importer=>({importer,paths:createRequire(importer).resolve.paths('hr-selection-probe')}));
const lookup_sha256=createHash('sha256').update(JSON.stringify(lookups)).digest('hex');
console.log(JSON.stringify({platform:process.platform,arch:process.arch,execPath:process.execPath,node:process.versions.node,glibc:process.report.getReport().header.glibcVersionRuntime??null,edges,unresolved,lookup_sha256,loaded:[]}));"""
    result=A['process_capture'](['node','-e',script,canonical(selection_projection(plan),16384).decode()],s.paths.exported_source,16384,16384)
    A['_budget'].get().charge(len(result['stdout'])+len(result['stderr']))
    need(result['process']['exit']==0 and result['process']['quiescent'] and not result['process']['timed_out'] and not result['process']['overflow'],'TOOL_NODE_PROBE')
    observation=parse(result['stdout'],16384);check_selection_observation(plan,observation,loaded=False)
    return observation


def prepare_esbuild_alias(session):
    s=session
    selection_environment()
    selection=native_selection(s)
    node=probe_selection(s,selection)
    platform_name='@esbuild/darwin-arm64' if s.binding['venue']=='native' else '@esbuild/linux-x64'
    need(s.binding['venue']=='native' or (sys.platform=='linux' and __import__('platform').machine()=='x86_64'),'PORTABLE_PLATFORM')
    base='web/node_modules/'
    for package in ('vite','esbuild',platform_name):
        _,raw=s.raw(s.paths.exported_source,base+package+'/package.json')
        need(parse(raw)['version']==(VITE_VERSION if package=='vite' else '0.21.5'),'ESBUILD_ROUTE',package)
    lock=s.lock()['packages'];member_name='package/bin/esbuild'
    with A['root_for'](s.paths.exported_source) as root:
        for name in ('web/package-lock.json','web/vendor/vite/UPSTREAM.json'):
            row=A['read_subject'](root,name)
            A['verify_git_subject'](s.candidate,s.paths,root,name,row)
        need(is_absent(root,base+'vite/node_modules/esbuild'),'ESBUILD_IMPORT_ROUTE')
        with A['parent_at'](root,base+'.bin/esbuild') as (parent,leaf):
            if not is_absent(root,base+'.bin/esbuild'):
                link=os.stat(leaf,dir_fd=parent,follow_symlinks=False)
                need(stat.S_ISLNK(link.st_mode) and os.readlink(leaf,dir_fd=parent)=='../esbuild/bin/esbuild','ESBUILD_BIN_LINK')
    _,parent_archive=s.raw(s.originals,'esbuild.tgz')
    need(lock['node_modules/esbuild']['integrity']=='sha512-'+base64.b64encode(hashlib.sha512(parent_archive).digest()).decode(),'ESBUILD_PARENT_ORIGINAL')
    with tarfile.open(fileobj=io.BytesIO(parent_archive),mode='r:gz') as archive:
        for member_name_checked in ('lib/main.js','install.js','package.json'):
            member=archive.getmember('package/'+member_name_checked)
            need(member.isfile() and member.size<=CAP,'ESBUILD_PARENT_MEMBER')
            body=archive.extractfile(member).read(CAP+1)
            _,installed=s.raw(s.paths.exported_source,base+'esbuild/'+member_name_checked)
            need(body==installed,'ESBUILD_PARENT_MEMBER_MISMATCH',member_name_checked)
    _,manifest_raw=s.raw(s.paths.exported_source,'web/vendor/vite/UPSTREAM.json')
    vite_manifest=parse(manifest_raw)
    expected_chunk=next(row for row in vite_manifest['members'] if row['path']=='package/dist/node/chunks/dep-BK3b2jBa.js')
    _,vite_chunk=s.raw(s.paths.exported_source,base+'vite/dist/node/chunks/dep-BK3b2jBa.js')
    need(sha(vite_chunk)==expected_chunk['sha256'],'ESBUILD_IMPORTER_MISMATCH')
    if s.binding['venue']=='native':
        _,raw=s.raw(s.originals,'@esbuild_darwin-arm64.tgz')
        need(lock['node_modules/'+platform_name]['integrity']=='sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode(),'ESBUILD_ORIGINAL')
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            member=archive.getmember(member_name);need(member.isfile() and member.size<=TOOL and member.mode==0o755,'ESBUILD_MEMBER')
            body=archive.extractfile(member).read(TOOL+1)
        expected={'archive_sha256':sha(raw),'lock_integrity':lock['node_modules/'+platform_name]['integrity'],'member_name':member_name,'size':len(body),'sha256':sha(body),'mode':0o755}
        need(expected['size']==9859426 and expected['sha256']=='b81b084018b48549fced4d72ac6a9d820aec907aff4ec546aef4a8703fee8dcc','ESBUILD_MEMBER')
    else:
        # Portable observations authenticate the locked Linux member separately;
        # never label this as the admitted Darwin/native preparation.
        name='web/node_modules/'+platform_name+'/bin/esbuild'
        with A['root_for'](s.paths.exported_source) as root:
            with A['parent_at'](root,name) as (parent,leaf):
                fd=os.open(leaf,A['FLAGS'],dir_fd=parent)
                try:
                    info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_size<=TOOL,'ESBUILD_MEMBER');body=os.read(fd,TOOL+1)
                finally:os.close(fd)
        expected={'archive_sha256':None,'lock_integrity':lock['node_modules/'+platform_name]['integrity'],'member_name':member_name,'size':len(body),'sha256':sha(body),'mode':0o755}
    selected=base+platform_name+'/bin/esbuild';alias=base+'esbuild/bin/esbuild'
    route={'importer_lock_key':'node_modules/vite','esbuild_lock_key':'node_modules/esbuild','platform_lock_key':'node_modules/'+platform_name,'selected_relative':selected,'alias_relative':alias}
    with A['root_for'](s.paths.exported_source) as root, contextlib.ExitStack() as stack:
        for disallowed in (base+'esbuild/bin-esbuild',base+'esbuild/lib/downloaded-'+platform_name.replace('/','-')+'-esbuild'):
            need(is_absent(root,disallowed),'ESBUILD_UNSUPPORTED_LAYOUT',disallowed)
        opened=[];pre={}
        for key,name in [('selected',selected),('alias',alias)]:
            parent,leaf=stack.enter_context(A['parent_at'](root,name));fd=os.open(leaf,A['FLAGS'],dir_fd=parent);stack.callback(os.close,fd)
            info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o755 and info.st_size==expected['size'],'ESBUILD_MEMBER',name)
            body=os.read(fd,TOOL+1);A['_budget'].get().charge(len(body));need(sha(body)==expected['sha256'],'ESBUILD_MEMBER_MISMATCH',name)
            need(A['identity'](info)==A['identity'](os.fstat(fd))==A['identity'](os.stat(leaf,dir_fd=parent,follow_symlinks=False)),'ESBUILD_UNSTABLE')
            pre[key]=A['subject_row'](name,info,sha(body));opened.append((parent,leaf,fd,info,body))
        p,a=opened;same=(p[3].st_dev,p[3].st_ino)==(a[3].st_dev,a[3].st_ino)
        need((same and p[3].st_nlink==a[3].st_nlink==2) or (not same and p[3].st_nlink==a[3].st_nlink==1),'ESBUILD_ALIAS_LAYOUT_INVALID')
        s.write('esbuild-attempt',{'route':route,'member':expected,'pre_state':pre})
        action='already_independent'
        if same:
            temporary='.happyranch-esbuild-copy'
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700,dir_fd=a[0])
            try:
                offset=0
                while offset<len(p[4]):
                    n=os.write(fd,p[4][offset:offset+65536]);need(n>0,'ESBUILD_COPY_SHORT');offset+=n;A['_budget'].get().charge(n)
                os.fchmod(fd,0o755);os.fsync(fd);new=os.fstat(fd)
                need(new.st_size==expected['size'] and new.st_nlink==1 and new.st_ino!=p[3].st_ino,'ESBUILD_COPY_INVALID')
            finally:os.close(fd)
            for parent,leaf,fd,info,_ in opened:
                need(A['identity'](os.fstat(fd))==A['identity'](info)==A['identity'](os.stat(leaf,dir_fd=parent,follow_symlinks=False)),'ESBUILD_UNSTABLE')
            with A['subject_fd'](root,base+'esbuild/bin/'+temporary,A['ReadDomain'](TOOL)) as (fd,info):
                need(sha(os.read(fd,TOOL+1))==expected['sha256'],'ESBUILD_COPY_INVALID')
            os.rename(temporary,a[1],src_dir_fd=a[0],dst_dir_fd=a[0]);action='dealias_known_optimized_alias'
    s.subjects=[];s.subject_index={};post={}
    for key,name in [('selected',selected),('alias',alias)]:
        post[key],_=s.intern_subject(s.paths.exported_source,name,TOOL)
        need(post[key]['content']=={k:expected[k] for k in ('size','sha256')},'ESBUILD_POST_MISMATCH')
    selection=native_selection(s)
    return s.write('esbuild-preparation',{'selection':selection,'node':node,'route':route,'member':expected,'mode':0o755,'pre_state':pre,'subjects':s.subjects,'post_tokens':post,'action':action})


def begin_web(s,preparation,completion_leaf=COMPLETION_LEAF):
    need(completion_leaf==COMPLETION_LEAF,'COMPLETION_LEAF_INVALID')
    with A['root_for'](s.paths.evidence_dir) as root:
        need(is_absent(root,COMPLETION_LEAF) and is_absent(root,'web-failure.json'),'COMPLETION_TARGET_EXISTS')
    s.write('attempt',{'binding_sha256':s.binding_sha,'completion_leaf':completion_leaf})
    prepared=s.verify_esbuild_preparation(preparation)
    originals=s.check_originals();tools=s.check_installed(originals)
    source=[]
    for name in ['web/vite.config.ts','web/tsconfig.json','web/scripts/packaging-custody.ts','web/scripts/packaging-tailwind.ts','app/mac/scripts/web_evidence.py','web/vendor/vite/css-relation.patch','web/vendor/vite/UPSTREAM.json']:
        token,raw=s.intern_subject(s.paths.exported_source,name);source.append({'path':name,'token':token})
        with A['root_for'](s.paths.exported_source) as root:
            A['verify_git_subject'](s.candidate,s.paths,root,name,s.subjects[int(token['subject_id'][1:])-1]['descriptor'])
    listing=A['git'](s.paths,'ls-tree','-rz',s.candidate.source_sha,'--','web',cap=8*MiB)
    for line in listing.split(b'\0')[:-1]:
        meta,name=line.split(b'\t',1);name=name.decode('utf-8')
        if name.startswith(EXCLUSIONS) or not name.endswith('.css'):continue
        need(meta.split(b' ')[0] in (b'100644',b'100755'),'CSS_SOURCE_MODE',name)
        s.intern_subject(s.paths.exported_source,name)
    s.intern_subject(s.paths.exported_source,'web/node_modules/highlight.js/styles/github-dark-dimmed.css')
    context_subjects=list(s.subjects)
    plan=s.prepare_css_inputs();plan_token=s.write('css-inputs',{'plan':plan,'subjects':s.subjects},{'preparation':preparation})
    token=s.write('context',{'selection':prepared['selection'],'node':prepared['node'],'binding_sha256':s.binding_sha,'completion_leaf':completion_leaf,'preparation':preparation,'css_inputs':plan_token['record_sha256'],'sources':source,'tools':tools,'originals':originals,'subjects':context_subjects},{'preparation':preparation,'asset_source':s.binding['asset_source_sha256']})
    return token


def load_context(s,context,preparation):
    s.subjects=[];s.subject_index={}
    prepared=s.verify_esbuild_preparation(preparation)
    # Registry is inherited once, with identical prefix IDs.
    s.subjects=[];s.subject_index={}
    record=s.load('context',context);p=record['payload']
    keys(p,{'selection','node','binding_sha256','completion_leaf','preparation','css_inputs','sources','tools','originals','subjects'})
    need(p['selection']==prepared['selection'] and p['node']==prepared['node'],'TOOL_SELECTION_CHANGED')
    need(p['binding_sha256']==s.binding_sha and p['completion_leaf']==COMPLETION_LEAF and p['preparation']==preparation,'CALLER_BINDING_MISMATCH')
    s.import_subjects(p['subjects'])
    plan=s.load('css-inputs',p['css_inputs'])['payload']
    need(plan['subjects'][:len(s.subjects)]==s.subjects,'SUBJECT_PREFIX_MISMATCH')
    s.subjects=[];s.subject_index={};s.import_subjects(plan['subjects'])
    return p


# Helper-owned lifecycle receipts, separate from mutable CSS operations.
LOAD_DIR = 'web-css-loads'
LOAD_SEAL = LOAD_DIR+'/seal.json'
LOAD_EVENT = 256*1024
LOAD_BYTES = 8*MiB


def mutable_web(s: Session) -> None:
    with A['root_for'](s.paths.evidence_dir) as root:
        need(is_absent(root,'web-failure.json'),'FAILED_BUILD')
        need(is_absent(root,LOAD_SEAL) and is_absent(root,'web-build.json') and is_absent(root,COMPLETION_LEAF),'CSS_LOADS_SEALED')


def load_inventory(root: object, *, create: bool=False) -> tuple[list[str],bool]:
    # Stop while iterating, before materializing an unbounded directory listing.
    names=[];sealed=False
    try:
        with A['parent_at'](root,LOAD_DIR+'/.inventory',create=create) as (parent,_):
            with os.scandir(parent) as entries:
                for entry in entries:
                    if entry.name=='seal.json':
                        need(not sealed,'CSS_LOAD_INVENTORY');sealed=True;continue
                    need(len(names)<MAX_ROWS,'CSS_LOAD_EVENT_CAP')
                    need(re.fullmatch(r'[0-9]{6}\.json',entry.name),'CSS_LOAD_INVENTORY',entry.name)
                    names.append(entry.name)
            names.sort()
            need(names==[f'{i:06d}.json' for i in range(1,len(names)+1)],'CSS_LOAD_SEQUENCE')
    except FileNotFoundError:
        need(not create,'CSS_LOAD_INVENTORY')
    return names,sealed


def load_event_token(descriptor: dict,index: int) -> dict:
    return {'schema':'web-css-load-token@1','index':index,'sha256':descriptor['sha256']}


def read_load_events(s: Session,root: object,context: str,preparation: str,names: list[str]) -> tuple[list, list, int, int]:
    events=[];inventory=[];total=0;name_bytes=0
    for index,leaf in enumerate(names,1):
        if s.terminal:s.terminal.position('load-event-read')
        descriptor,raw=A['read_bytes'](root,LOAD_DIR+'/'+leaf,A['ReadDomain'](LOAD_EVENT,True))
        total+=len(raw);need(total<=LOAD_BYTES,'CSS_LOAD_BYTES_CAP')
        if s.terminal:s.terminal.position('load-event-json')
        event=parse(raw,LOAD_EVENT)
        if s.terminal:s.terminal.position('validation')
        keys(event,s.envelope('css-load',None,{}))
        expected=s.envelope('css-load',None,{'context':context,'preparation':preparation})
        need({k:v for k,v in event.items() if k!='payload'}=={k:v for k,v in expected.items() if k!='payload'},'CSS_LOAD_BINDING')
        payload=event['payload'];keys(payload,{'kind','index','invocation','parent_outer_call','data','transport'})
        need(type(payload['index']) is int and payload['index']==index,'CSS_LOAD_SEQUENCE')
        need(isinstance(payload['invocation'],str) and re.fullmatch(r'k[1-9][0-9]{0,4}',payload['invocation']) and isinstance(payload['parent_outer_call'],str) and re.fullmatch(r'o[1-9][0-9]{0,5}',payload['parent_outer_call']),'CSS_COMPILE_LIFECYCLE')
        name_bytes=metadata_limits(event,initial_names=name_bytes)
        events.append((payload,load_event_token(descriptor,index)))
        inventory.append(descriptor)
    return events,inventory,total,name_bytes


def load_lifecycles(events: list, *, complete: bool=False) -> dict:
    compiles={};parents=set()
    for event,token in events:
        need(isinstance(event['invocation'],str) and re.fullmatch(r'k[1-9][0-9]{0,4}',event['invocation']) and isinstance(event['parent_outer_call'],str) and re.fullmatch(r'o[1-9][0-9]{0,5}',event['parent_outer_call']),'CSS_COMPILE_LIFECYCLE')
        invocation=event['invocation'];parent=event['parent_outer_call'];d=event['data'];kind=event['kind']
        keys(event['transport'],{'request_bytes','reply_bytes'})
        js_size(event['transport']['request_bytes'],4096);js_size(event['transport']['reply_bytes'],32*MiB if kind=='stylesheet_read' else 4096)
        if kind=='compile_begin':
            keys(d,{'module','input','options'})
            need(invocation=='k'+str(len(compiles)+1) and invocation not in compiles and parent not in parents,'CSS_COMPILE_LIFECYCLE')
            need(type(d['module']) is str and type(d['input']) is dict,'CSS_COMPILE_LIFECYCLE')
            keys(d['input'],{'size','sha256'});js_size(d['input']['size']);digest_shape(d['input']['sha256'])
            need(d['options']=={'base':str(Path(d['module'].split('?')[0]).parent),'from':d['module'],'polyfills':3,'sourcemap':False},'CSS_COMPILE_OPTIONS')
            parents.add(parent);compiles[invocation]={'begin':d,'parent':parent,'reads':[],'tokens':[],'ended':False}
        else:
            state=compiles.get(invocation)
            need(state and state['parent']==parent and not state['ended'],'CSS_COMPILE_LIFECYCLE')
            if kind=='stylesheet_read':
                keys(d,{'ordinal','id','base','relative','descriptor','raw_sha256','decoded_sha256','decoded_size'})
                need(type(d['ordinal']) is int and d['ordinal']==len(state['reads'])+1,'CSS_LOAD_ORDINAL')
                need(type(d['id']) is str and type(d['base']) is str,'CSS_COMPILE_LIFECYCLE')
                path_name(d['relative']);A['subject_shape'](d['descriptor']);digest_shape(d['raw_sha256']);digest_shape(d['decoded_sha256']);js_size(d['decoded_size'])
                need(d['descriptor']['relative']==d['relative'] and d['descriptor']['sha256']==d['raw_sha256'],'CSS_COMPILE_INPUT_MISSING')
                state['reads'].append(d);state['tokens'].append(token)
            elif kind=='compile_end':
                keys(d,{'input','read_tokens'})
                need(d['input']==state['begin']['input'] and d['read_tokens']==state['tokens'],'CSS_COMPILE_INPUT_MISSING')
                state['ended']=True
            else:need(False,'CSS_LOAD_KIND')
    if complete:need(all(state['ended'] for state in compiles.values()),'CSS_COMPILE_LIFECYCLE')
    return compiles


def append_load_event(s: Session,context: str,preparation: str,index: int,kind: str,invocation: str,parent: str,data: dict,request: dict,reply: dict | None=None) -> dict:
    mutable_web(s)
    with A['root_for'](s.paths.evidence_dir) as root:
        names,sealed=load_inventory(root,create=True)
        need(not sealed and type(index) is int and index==len(names)+1 and index<=MAX_ROWS,'CSS_LOAD_SEQUENCE')
        events,_,total,name_bytes=read_load_events(s,root,context,preparation,names)
        placeholder={'schema':'web-css-load-token@1','index':index,'sha256':'0'*64}
        response=placeholder if reply is None else {**reply,'read_token':placeholder}
        transport={'request_bytes':getattr(s,'request_bytes',len(canonical(request,4096))),'reply_bytes':len(canonical(response,32*MiB if kind=='stylesheet_read' else 4096))+1}
        need(transport['request_bytes']==len(canonical(request,4096)),'CSS_LOAD_TRANSPORT')
        payload={'kind':kind,'index':index,'invocation':invocation,'parent_outer_call':parent,'data':data,'transport':transport}
        value=s.envelope('css-load',payload,{'context':context,'preparation':preparation})
        metadata_limits(value,initial_names=name_bytes)
        raw=canonical(value,LOAD_EVENT-1)+b'\n'
        need(total+len(raw)<=LOAD_BYTES,'CSS_LOAD_BYTES_CAP')
        provisional={'schema':'web-css-load-token@1','index':index,'sha256':sha(raw)}
        # Validate the prospective transition before any exclusive write.
        load_lifecycles([*events,(payload,provisional)])
        A['_budget'].get().charge(len(raw),json_bytes=True)
        descriptor=A['create_once'](root,LOAD_DIR+'/'+f'{index:06d}.json',raw,A['ReadDomain'](LOAD_EVENT,True))
        after,after_sealed=load_inventory(root)
        need(after==[*names,f'{index:06d}.json'] and not after_sealed,'CSS_LOAD_SEQUENCE')
    return load_event_token(descriptor,index)


def compile_event(s: Session,context: str,preparation: str,index: int,kind: str,request: dict) -> dict:
    load_context(s,context,preparation)
    if kind=='compile_begin':
        keys(request,{'invocation','parent_outer_call','module','input','options'})
        data={k:request[k] for k in ('module','input','options')}
    else:
        keys(request,{'invocation','parent_outer_call','read_tokens'})
        with A['root_for'](s.paths.evidence_dir) as root:
            names,_=load_inventory(root)
            events,_,_,_=read_load_events(s,root,context,preparation,names)
        state=load_lifecycles(events).get(request['invocation'])
        need(state and not state['ended'] and state['parent']==request['parent_outer_call'],'CSS_COMPILE_LIFECYCLE')
        data={'input':state['begin']['input'],'read_tokens':request['read_tokens']}
    return append_load_event(s,context,preparation,index,kind,request['invocation'],request['parent_outer_call'],data,request)


def seal_loads(s: Session,context: str,preparation: str) -> None:
    # The complete directory, never a producer list, supplies the expected set.
    with A['root_for'](s.paths.evidence_dir) as root:
        need(is_absent(root,'web-failure.json'),'FAILED_BUILD')
        sealed=not is_absent(root,LOAD_SEAL)
    if sealed:
        # Inspect retained seal corruption honestly, but never republish it.
        checked_loads(s,context,preparation)
        need(False,'CSS_LOADS_SEALED')
    mutable_web(s)
    s.load('context',context)
    with A['root_for'](s.paths.evidence_dir) as root:
        names,sealed=load_inventory(root,create=True);need(not sealed,'CSS_LOADS_SEALED')
        events,inventory,total,name_bytes=read_load_events(s,root,context,preparation,names)
        load_lifecycles(events,complete=True)
        payload={'inventory':inventory,'event_bytes':total}
        value=s.envelope('css-load-seal',payload,{'context':context,'preparation':preparation})
        metadata_limits(value,initial_names=name_bytes)
        raw=canonical(value,LOAD_BYTES-1,node_cap=BUILD_NODES)+b'\n'
        A['_budget'].get().charge(len(raw),json_bytes=True)
        A['create_once'](root,LOAD_SEAL,raw,A['ReadDomain'](LOAD_BYTES,True))


def checked_loads(s: Session,context: str,preparation: str) -> dict:
    with A['root_for'](s.paths.evidence_dir) as root:
        need(is_absent(root,'web-failure.json'),'FAILED_BUILD')
        names,sealed=load_inventory(root);need(sealed,'CSS_LOAD_SEAL_MISSING')
        if s.terminal:s.terminal.position('load-seal-read')
        _,raw=A['read_bytes'](root,LOAD_SEAL,A['ReadDomain'](LOAD_BYTES,True))
        if s.terminal:s.terminal.position('load-seal-json')
        seal=parse(raw,LOAD_BYTES,build=True);keys(seal,s.envelope('css-load-seal',None,{}))
        if s.terminal:s.terminal.position('validation')
        expected=s.envelope('css-load-seal',None,{'context':context,'preparation':preparation})
        need({k:v for k,v in seal.items() if k!='payload'}=={k:v for k,v in expected.items() if k!='payload'},'CSS_LOAD_BINDING')
        keys(seal['payload'],{'inventory','event_bytes'})
        events,inventory,total,name_bytes=read_load_events(s,root,context,preparation,names)
        need(seal['payload']=={'inventory':inventory,'event_bytes':total},'CSS_LOAD_INVENTORY')
        # Verify the terminal file through the ordinary descriptor guard as well.
        s.load_names=metadata_limits(seal,initial_names=name_bytes)
        s.load_transport=sum(event['transport']['request_bytes']+event['transport']['reply_bytes'] for event,_ in events)
        js_size(s.load_transport,BODY_WORK)
        compiles=load_lifecycles(events,complete=True)
        for state in compiles.values():
            for read in state['reads']:
                descriptor,body=s.raw(s.paths.exported_source,read['relative'])
                decoded=decode(body).encode()
                need(descriptor==read['descriptor'] and sha(body)==read['raw_sha256'] and sha(decoded)==read['decoded_sha256'] and len(decoded)==read['decoded_size'],'CSS_COMPILE_INPUT_MISSING')
    return compiles


def css_read(s,context,preparation,request,index=None):
    mutable_web(s)
    p=load_context(s,context,preparation)
    site=request['site']
    keys(request,{'site','id','base','invocation','parent_outer_call','ordinal'} if site=='stylesheet' else {'site','id','base'})
    site=request['site'];need(site in ('content','stylesheet','source'),'READ_SITE')
    if site=='content':
        plan=s.load('css-inputs',p['css_inputs'])['payload']['plan']
        entries=[]
        for row in plan['entries']:
            raw=s.resolve_subject_token(row['source_token']);entries.append({**row,'content':decode(raw)})
        return {'schema':'web-css-content@1','entries':entries,'selection':selection_projection(p['selection'])}
    name=request['id'];base=request['base']
    if site=='stylesheet':
        if name=='tailwindcss':relative='web/node_modules/tailwindcss/index.css'
        elif name=='highlight.js/styles/github-dark-dimmed.css':relative='web/node_modules/'+name
        else:
            need(name.startswith('./') or name.startswith('../'),'CSS_IMPORT_UNSUPPORTED',name)
            base_path=Path(base);need(base_path.is_absolute() and base_path.is_relative_to(s.paths.exported_source/'web'),'CSS_IMPORT_BASE')
            # Lexical normalization only; guarded open below refuses actual links.
            relative=os.path.relpath(os.path.normpath(str(base_path/name)),s.paths.exported_source)
            path_name(relative);need(relative.startswith('web/') and relative.endswith('.css'),'CSS_IMPORT_UNSUPPORTED',name)
            if '/node_modules/' in relative:need(relative.startswith('web/node_modules/tailwindcss/'),'CSS_IMPORT_OWNER')
    else:
        absolute=Path(name.split('?',1)[0]);need(absolute.is_absolute() and absolute.is_relative_to(s.paths.exported_source/'web'),'SOURCE_OUTSIDE_ROOTS')
        relative=absolute.relative_to(s.paths.exported_source).as_posix()
    token,raw=s.intern_subject(s.paths.exported_source,relative)
    # Extra imported subjects are sealed by actual path/token in build, with
    # independent readers obtaining descriptors directly; never JS numbers.
    result={'schema':'web-css-read@1','path':str(s.paths.exported_source/relative),'relative':relative,'base':str((s.paths.exported_source/relative).parent),'content':decode(raw),'content_sha256':sha(decode(raw).encode()),'raw_sha256':sha(raw),'token':token}
    if site=='stylesheet':
        descriptor=s.subjects[int(token['subject_id'][1:])-1]['descriptor']
        data={'ordinal':request['ordinal'],'id':name,'base':base,'relative':relative,'descriptor':descriptor,'raw_sha256':sha(raw),'decoded_sha256':result['content_sha256'],'decoded_size':len(result['content'].encode())}
        result['read_token']=append_load_event(s,context,preparation,index,'stylesheet_read',request['invocation'],request['parent_outer_call'],data,request,result)
    return result


def snapshot(s,context,preparation,index,raw):
    if s.diagnostics:s.diagnostics.mark('mutable','start')
    mutable_web(s)
    if s.diagnostics:s.diagnostics.mark('mutable','end')
    # Direct context token authenticates this write, without rereading every
    # registry subject for each intermediate. Final publisher reopens all.
    if s.diagnostics:s.diagnostics.mark('context','start')
    context_record=s.load('context',context)
    need(context_record['payload']['preparation']==preparation,'PREPARATION_BINDING')
    if s.diagnostics:s.diagnostics.mark('context','end')
    js_size(index,MAX_ROWS);need(index>0 and len(raw)<=CAP,'BLOB_CAP')
    name='css/'+f'{index:06d}'+'.bin'
    if s.diagnostics:s.diagnostics.mark('create','start')
    with A['root_for'](s.paths.evidence_dir) as root:
        row=A['create_once'](root,name,raw,A['ReadDomain'](CAP))
    if s.diagnostics:s.diagnostics.mark('create','end')
    return {'path':name,'size':row['size'],'sha256':row['sha256'],'descriptor_sha256':sha(canonical(row))}


def blob(s,row):
    keys(row,{'path','size','sha256','descriptor_sha256'});digest_shape(row['descriptor_sha256']);js_size(row['size']);digest_shape(row['sha256'])
    need(re.fullmatch(r'css/[0-9]{6}\.bin',row['path']) and 1<=int(row['path'][4:10])<=MAX_ROWS,'BLOB_PATH')
    token,raw=s.intern_subject(s.paths.evidence_dir,row['path']);descriptor=s.subjects[int(token['subject_id'][1:])-1]['descriptor']
    need(sha(canonical(descriptor))==row['descriptor_sha256'],'SUBJECT_DESCRIPTOR_MISMATCH',row['path'])
    need(descriptor['size']==row['size'] and descriptor['sha256']==row['sha256'],'BLOB_MISMATCH',row['path'])
    return raw


def validate_css(s,operations,plan,observations,loads):
    need(len(operations)<=MAX_ROWS,'OPERATION_CAP');states={};styles={};assets={};finalized={};minifies={};emits=[];chunks={};urls={}
    declared=[op.get('data',{}).get('invocation') for op in operations if op.get('stage')=='compile']
    need(all(isinstance(key,str) for key in declared) and len(set(declared))==len(declared) and set(declared)==set(loads),'CSS_COMPILE_INPUT_MISSING')
    consumed=set();emission_consumed=set()
    def b(row):return decode(blob(s,row))
    for ordinal,op in enumerate(operations,1):
        keys(op,{'schema','id','parent_outer_call','stage','data','status','error'})
        need(op['schema']=='css-operation@1' and op['id']=='c'+str(ordinal) and op['status']=='returned' and op['error'] is None,'CSS_OPERATION_INVALID')
        parent=op['parent_outer_call'];need(parent in observations and observations[parent]['kind']=='hook_call','CSS_CALL_PARENT')
        stage=op['stage'];d=op['data']
        if stage=='compile':
            keys(d,{'module','input','options','invocation','imports','features','root','sources'})
            need(d['module'] not in states,'CSS_STATE_CHAIN_INVALID')
            need(d['root'] is None and d['sources']==[],'CSS_UNSUPPORTED_SOURCE_DIRECTIVE')
            need(d['options']=={'polyfills':3,'sourcemap':False,'base':str(Path(d['module'].split('?')[0]).parent),'from':d['module']},'CSS_COMPILE_OPTIONS')
            state=loads.get(d['invocation'])
            need(state and d['invocation'] not in consumed and state['ended'] and state['parent']==parent,'CSS_COMPILE_INPUT_MISSING')
            raw_input=blob(s,d['input'])
            need(state['begin']=={'module':d['module'],'input':{'size':len(raw_input),'sha256':sha(raw_input)},'options':d['options']},'CSS_COMPILE_INPUT_MISSING')
            need(type(d['imports']) is list and [r.get('read_token') for r in d['imports']]==state['tokens'],'CSS_COMPILE_INPUT_MISSING')
            consumed.add(d['invocation'])
            b(d['input']);states[d['module']]={'phase':'compile','features':d['features'],'candidates':[],'entries':[],'output':None}
            for imp,read in zip(d['imports'],state['reads']):
                keys(imp,{'id','base','relative','content','raw_sha256','decoded_sha256','read_token'})
                need(all(imp[k]==read[k] for k in ('id','base','relative','raw_sha256','decoded_sha256')),'CSS_COMPILE_INPUT_MISSING')
                descriptor,raw=s.raw(s.paths.exported_source,imp['relative']);need(descriptor['sha256']==imp['raw_sha256'] and sha(decode(raw).encode())==imp['decoded_sha256'] and b(imp['content'])==decode(raw),'CSS_COMPILE_INPUT_MISSING')
                validate_import_urls(decode(raw))
        elif stage=='content_scan':
            keys(d,{'module','batch','inputs','returned'})
            state=states.get(d['module']);need(state and state['phase'] in ('compile','scan'),'CSS_STATE_CHAIN_INVALID')
            need(d['batch']==len(state.get('batches',[])),'CSS_SCAN_INPUT_INVALID');state.setdefault('batches',[]).append(d['batch']);state['phase']='scan'
            inputs=parse(blob(s,d['inputs']));returned=parse(blob(s,d['returned']))
            need(type(inputs) is list and type(returned) is list and all(type(x) is str for x in returned),'CSS_SCAN_INPUT_INVALID')
            offset=len(state['entries'])
            for index,item in enumerate(inputs):
                keys(item,{'relative_path','content','extension'});need(offset+index<len(plan['entries']),'CSS_INPUT_SET_MISMATCH')
                expected=plan['entries'][offset+index]
                need(item['relative_path']==expected['relative_path'] and item['extension']==expected['extension'],'CSS_SCAN_INPUT_INVALID')
                raw=s.resolve_subject_token(expected['source_token']);need(item['content']==decode(raw),'CSS_SCAN_INPUT_INVALID');state['entries'].append(item['relative_path'])
            need(len(returned)<=MAX_ROWS,'CANDIDATE_CAP')
            for candidate in returned:
                if candidate not in state['candidates']:
                    need(len(state['candidates'])<MAX_ROWS,'CANDIDATE_CAP');state['candidates'].append(candidate)
        elif stage=='candidate_build':
            keys(d,{'module','candidates','output','utilities'})
            state=states.get(d['module']);need(state and state['phase'] in ('compile','scan'),'CSS_STATE_CHAIN_INVALID')
            need(type(d['utilities']) is bool and d['utilities']==bool(state['features'] & 16),'CSS_FEATURES')
            need(parse(blob(s,d['candidates']))==state['candidates'],'CSS_STATE_CHAIN_INVALID')
            if d['utilities']:need(state['entries']==[r['relative_path'] for r in plan['entries']],'CSS_INPUT_SET_MISMATCH')
            else:need(not state['entries'] and not state['candidates'],'CSS_SCAN_INPUT_INVALID')
            state['phase']='built';state['output']=b(d['output'])
        elif stage=='optimize':
            keys(d,{'module','input','options','output','map'})
            state=states.get(d['module']);need(state and state['phase']=='built' and b(d['input'])==state['output'],'CSS_OPTIMIZE_RELATION_INVALID')
            need(d['map'] is None and d['options']=={'file':d['module'],'minify':True,'map':None},'CSS_OPTIMIZE_RELATION_INVALID')
            state['phase']='optimized';state['output']=b(d['output'])
        elif stage=='vite_css':
            keys(d,{'id','input','output','urls','modules','deps','map'})
            state=states.get(d['id']);need(state and state['phase']=='optimized' and b(d['input'])==state['output'],'CSS_OPTIMIZE_RELATION_INVALID')
            need(d['modules'] is None,'CSS_MODULES_UNSUPPORTED');state['phase']='vite';state['output']=b(d['output'])
            for url in d['urls']:
                keys(url,{'url','importer','decoded','result'});need(type(url['result']) is str,'CSS_URL_RELATION_INVALID')
                need(url['result']!=url['url'] or url['url'].startswith(('/','data:','http:','https:','#')),'CSS_URL_UNRESOLVED')
        elif stage=='style_store':
            keys(d,{'id','css'});state=states.get(d['id']);need(state and state['phase']=='vite' and b(d['css'])==state['output'],'CSS_STYLE_STATE_INVALID');styles[d['id']]=b(d['css']);state['phase']='stored'
        elif stage=='css_chunk':
            keys(d,{'chunk','ids','styles','css','format','split','js'})
            need(d['format']=='es' and d['split'] is True and d['ids']==[x['id'] for x in d['styles']],'CSS_CHUNK_PROFILE')
            joined=''
            for value in d['styles']:
                keys(value,{'id','present','transform_only','css'});need(value['present']==(value['id'] in styles),'CSS_STYLE_STATE_INVALID')
                if value['present']:
                    need(b(value['css'])==styles[value['id']],'CSS_STYLE_STATE_INVALID')
                    if not value['transform_only']:joined+=styles[value['id']]
            # Vite awaits its emission queues between chunk capture and URL /
            # finalization. Other render calls may interleave during that wait;
            # only the recorded enclosing call owns this CSS chain.
            need(joined==b(d['css']),'CSS_CONCAT_MISMATCH');chunks[parent]=joined;urls.pop(parent,None)
        elif stage=='css_url':
            keys(d,{'pass','input','output','replacements','cssAssetName','base','assetsDir'})
            value=b(d['input']);need(parent in chunks and value==urls.get(parent,chunks[parent]),'CSS_URL_RELATION_INVALID')
            # Offsets are JS UTF16 code units. Reconstruct exact literal replaces.
            raw=value.encode('utf-16-le');cursor=0;pieces=[]
            for r in d['replacements']:
                offset=js_size(r['offset'],CAP)*2;matched=r['matched'].encode('utf-16-le')
                need(offset>=cursor and raw[offset:offset+len(matched)]==matched,'CSS_URL_RELATION_INVALID')
                if d['pass']=='asset':need(r['fileHash'] in {o['reference_id'] for o in observations.values() if o['kind']=='plugin_emission'},'CSS_URL_RELATION_INVALID')
                pieces.extend([raw[cursor:offset],r['replacement'].encode('utf-16-le')]);cursor=offset+len(matched)
            pieces.append(raw[cursor:]);need(b''.join(pieces).decode('utf-16-le')==b(d['output']),'CSS_URL_RELATION_INVALID');urls[parent]=b(d['output'])
        elif stage=='css_minify':
            keys(d,{'input','options','code','inlined','output'})
            need(d['inlined'] is False and d['options']=={'loader':'css','target':['es2020','edge88','firefox78','chrome87','safari14'],'charset':'utf8','logLevel':None,'logLimit':None,'logOverride':None,'legalComments':None,'minify':True} and b(d['code'])==b(d['output']),'CSS_MINIFY_INPUT_MISMATCH');minifies.setdefault(parent,[]).append(d)
        elif stage=='css_finalize':
            keys(d,{'input','hoist','hoisted','minify','minified','marker','output'})
            need(parent in chunks and b(d['input'])==urls.get(parent,chunks[parent]),'CSS_FINALIZE_CHAIN_INVALID')
            if not d['hoist']:need(b(d['input'])==b(d['hoisted']),'CSS_FINALIZE_CHAIN_INVALID')
            # Each finalize consumes its own actual minifier once. A later
            # finalize cannot reuse a prior result, even when the bytes match.
            own_minifies=minifies.pop(parent,[])
            if d['minify']:
                need(len(own_minifies)==1 and b(own_minifies[0]['input'])==b(d['hoisted']) and b(own_minifies[0]['output'])==b(d['minified']),'CSS_MINIFY_INPUT_MISMATCH')
            else:need(not own_minifies and b(d['hoisted'])==b(d['minified']),'CSS_MINIFY_INPUT_MISMATCH')
            need(d['marker']=='/*$vite$:1*/' and b(d['output'])==b(d['minified'])+d['marker'],'CSS_FINALIZE_CHAIN_INVALID');finalized[parent]=b(d['output'])
        elif stage=='css_emit':
            keys(d,{'source','referenceId','filename','cssAssetName','originalFileName'})
            need(b(d['source'])==finalized.get(parent),'CSS_EMISSION_RELATION_MISSING')
            match=[o for o in observations.values() if o['kind']=='plugin_emission' and o['call_id']==parent and o['reference_id']==d['referenceId'] and o['source']=={'size':len(b(d['source']).encode()),'sha256':sha(b(d['source']).encode())}]
            need(len(match)==1,'CSS_EMISSION_RELATION_MISSING')
            need(match[0]['id'] not in emission_consumed and d['filename']==match[0]['resolved_file_name'],'CSS_EMISSION_RELATION_MISSING')
            emission_consumed.add(match[0]['id']);emits.append(d)
        elif stage=='css_asset_read':
            keys(d,{'id','file','content'});absolute=Path(d['file']);need(absolute.is_relative_to(s.paths.exported_source/'web'),'CSS_ASSET_ROOT')
            descriptor,raw=s.raw(s.paths.exported_source,absolute.relative_to(s.paths.exported_source).as_posix());need(raw==blob(s,d['content']),'CSS_URL_RELATION_INVALID')
            asset=assets.setdefault(d['id'],{'pending':[],'url':None})
            asset['pending'].append(parent)
        elif stage=='css_asset_store':
            keys(d,{'id','url'});asset=assets.get(d['id'])
            need(asset is not None and parent in asset['pending'] and type(d['url']) is str,'CSS_URL_RELATION_INVALID')
            asset['pending'].remove(parent);asset['url']=d['url']
        elif stage=='css_asset_hit':
            keys(d,{'id','url'});asset=assets.get(d['id'])
            need(asset is not None and asset['url'] is not None and asset['url']==d['url'],'CSS_URL_RELATION_INVALID')
        else:need(False,'CSS_STAGE_INVALID',stage)
    need(consumed==set(loads),'CSS_COMPILE_INPUT_MISSING')
    need(not any(minifies.values()),'CSS_MINIFY_INPUT_MISMATCH')
    need(all(not asset['pending'] for asset in assets.values()),'CSS_URL_RELATION_INVALID')
    need(states and all(st['phase']=='stored' for st in states.values()) and emits,'CSS_COMPLETE_RELATION_MISSING')
    return {'modules':list(states),'emissions':[x['referenceId'] for x in emits]}


def validate_import_urls(css):
    """Bounded lexical guard. No rewrites; comments/ordinary strings skipped."""
    need(len(css.encode())<=CAP,'CSS_IMPORT_CAP');i=0;n=len(css)
    while i<n:
        if css.startswith('/*',i):
            end=css.find('*/',i+2);need(end>=0,'CSS_URL_SYNTAX');i=end+2;continue
        c=css[i]
        if c in "\"'":
            quote=c;i+=1
            while i<n and css[i]!=quote:
                if css[i]=='\\':i+=1
                i+=1
            need(i<n,'CSS_URL_SYNTAX');i+=1;continue
        if c=='\\':need(False,'CSS_URL_ESCAPE')
        if c.isascii() and (c.isalpha() or c in '-_'):
            start=i
            while i<n and css[i].isascii() and (css[i].isalnum() or css[i] in '-_'):i+=1
            ident=css[start:i].lower();need(i==n or css[i]!='\\','CSS_URL_ESCAPE')
            j=i
            while j<n and css[j].isspace():j+=1
            if j<n and css[j]=='(':
                need(ident not in ('image-set','-webkit-image-set'),'CSS_IMAGE_SET_UNSUPPORTED')
                if ident=='url':
                    k=j+1
                    while k<n and css[k].isspace():k+=1
                    quote=css[k] if k<n and css[k] in "\"'" else None
                    if quote:k+=1
                    start=k
                    while k<n and (css[k]!=quote if quote else css[k]!=')'):
                        need(css[k]!='\\','CSS_URL_ESCAPE');k+=1
                    need(k<n,'CSS_URL_SYNTAX');url=css[start:k].strip()
                    if quote:
                        k+=1
                        while k<n and css[k].isspace():k+=1
                    need(k<n and css[k]==')','CSS_URL_SYNTAX')
                    need(url.startswith(('/','#','data:','https:','http:','//')),'CSS_RELATIVE_URL_UNSUPPORTED',url)
                    i=k+1
            continue
        i+=1


def validate_build(s,payload,context,preparation,*,stored_required=False):
    stored={k:payload[k] for k in ('subjects','source_tool_associations') if k in payload}
    need(not stored_required or set(stored)=={'subjects','source_tool_associations'},'BUILD_REGISTRY_MISSING')
    payload={k:v for k,v in payload.items() if k not in stored}
    if s.terminal:s.terminal.enter_phase('validation-context')
    p=load_context(s,context,preparation)
    if s.terminal:s.terminal.enter_phase('validation')
    loads=checked_loads(s,context,preparation)
    keys(payload,{'resource_profile','complete','profile','observations','css_operations','modules','final_bundle','counts'})
    need(payload['resource_profile']==RESOURCE_PROFILE,'RESOURCE_PROFILE')
    need(payload['complete'] is True,'CALL_INCOMPLETE');profile=payload['profile']
    keys(profile,{'command','mode','isProduction','isWorker','root','base','appType','input','output','build','css','esbuild','define','env','config_dependencies','plugins','tool_selection'})
    check_selection_observation(p['selection'],profile['tool_selection'],loaded=True)
    need(all(profile['tool_selection'][key]==p['node'][key] for key in ('execPath','node','glibc')),'TOOL_NODE_RUNTIME')
    need(profile['command']=='build' and profile['mode']=='production' and profile['isProduction'] is True and profile['isWorker'] is False and profile['root']==str(s.paths.exported_source/'web') and profile['base']=='/' and profile['appType']=='spa','PROFILE_UNSUPPORTED')
    need(profile['build']['sourcemap'] is True and profile['build']['cssCodeSplit'] is True and profile['build']['cssMinify'] is True and profile['build']['minify']=='esbuild' and profile['build']['ssr'] is False and profile['build']['watch'] is None and profile['build']['lib'] is False,'PROFILE_UNSUPPORTED')
    need(profile['css']['devSourcemap'] is False and profile['css']['postcss']=={'plugins':[]},'PROFILE_UNSUPPORTED')
    keys(profile['build'],{'outDir','assetsDir','assetsInlineLimit','cssCodeSplit','sourcemap','minify','cssMinify','target','cssTarget','write','copyPublicDir','lib','ssr','watch','modulePreload','commonjsOptions'})
    need(profile['build']['outDir']=='dist' and profile['build']['assetsDir']=='assets' and profile['build']['assetsInlineLimit']==4096 and profile['build']['write'] is True and profile['build']['copyPublicDir'] is True,'PROFILE_UNSUPPORTED')
    keys(profile['css'],{'devSourcemap','postcss','transformer'});need(profile['css']['transformer'] is None,'PROFILE_UNSUPPORTED')
    need(profile['define']=={},'PROFILE_DEFINE_UNSUPPORTED')
    expected_env={'BASE_URL':'/','MODE':'production','DEV':'false','PROD':'true'}
    need(profile['env']=={k:{'size':len(v.encode()),'sha256':sha(v.encode())} for k,v in expected_env.items()},'PROFILE_ENV_UNSUPPORTED')
    need(profile['esbuild']=={'jsx':'automatic','jsxDev':False,'jsxImportSource':None},'PROFILE_ESBUILD_UNSUPPORTED')
    targets=['es2020','edge88','firefox78','chrome87','safari14']
    need(profile['build']['target']==targets==profile['build']['cssTarget'] and profile['build']['modulePreload']=={'polyfill':True},'PROFILE_TARGET_UNSUPPORTED')
    need(profile['build']['commonjsOptions']=={'include':[{'pattern':'node_modules','flags':''}],'extensions':['.js','.cjs']},'PROFILE_COMMONJS_UNSUPPORTED')
    need(profile['input']==str(s.paths.exported_source/'web/index.html') and profile['output']=={'addons':[],'dir':'dist','format':'es'},'PROFILE_OUTPUT_UNSUPPORTED')
    need(profile['config_dependencies']==[str(s.paths.exported_source/name) for name in ('web/scripts/packaging-custody.ts','web/scripts/packaging-tailwind.ts','web/vite.config.ts')],'PROFILE_CONFIG_DEPENDENCIES')
    expected_names=['commonjs--resolver','vite:build-metadata','vite:watch-package-data','vite:pre-alias','alias','i18n-browser-evidence-instrumentation','vite:react-babel','vite:react-refresh','happyranch:packaging-tailwind','vite:modulepreload-polyfill','vite:resolve','vite:html-inline-proxy','vite:css','vite:esbuild','vite:json','vite:wasm-helper','vite:worker','vite:asset','happyranch:html-pre','happyranch:html-post','vite:wasm-fallback','vite:define','vite:css-post','vite:build-html','vite:worker-import-meta-url','vite:asset-import-meta-url','vite:force-systemjs-wrap-complete','commonjs','vite:data-uri','vite:dynamic-import-vars','vite:import-glob','happyranch:packaging-custody','vite:build-import-analysis','vite:esbuild-transpile','vite:terser','vite:reporter','vite:load-fallback']
    need([x['name'] for x in profile['plugins']]==expected_names,'PLUGIN_INVENTORY_MISMATCH')
    for ordinal,item in enumerate(profile['plugins']):
        keys(item,{'ordinal','name','hooks'});need(type(item['ordinal']) is int and item['ordinal']==ordinal,'PLUGIN_ORDINAL')
        if item['name']=='vite:react-babel':need(not item['hooks'],'REACT_BABEL_PROFILE')
    keys(payload['counts'],{'observations','operations','processed_bytes','transport_bytes'})
    js_size(payload['counts']['transport_bytes'],BODY_WORK)
    need(payload['counts']['transport_bytes']==s.load_transport,'CSS_LOAD_TRANSPORT')
    js_size(payload['counts']['processed_bytes'],BODY_WORK)
    need(type(payload['counts']['observations']) is int and payload['counts']['observations']==len(payload['observations']) and type(payload['counts']['operations']) is int and payload['counts']['operations']==len(payload['css_operations']),'COUNTS_MISMATCH')
    observations=payload['observations'];need(type(observations) is list and len(observations)<=MAX_OBSERVATIONS,'OBSERVATION_CAP')
    need(len(profile['plugins'])<=256,'PLUGIN_CAP')
    ids={};calls={};refs={};snapshots={};delta=None;render={};render_inputs={};write=None;generated=None
    hooks={'load','transform','moduleParsed','resolveId','renderStart','renderChunk','generateBundle','writeBundle','buildEnd','closeBundle','banner','footer','intro','outro'}
    entry_keys={'path','preliminary_path','type','bytes','module_ids','sourcemap_path'}
    def entries(rows):
        need(type(rows) is list and len(rows)<=4096,'OUTPUT_CAP');names=set()
        for row in rows:
            keys(row,entry_keys);path_name(row['path']);need(row['path'] not in names,'OUTPUT_DUPLICATE');names.add(row['path'])
            keys(row['bytes'],{'size','sha256'});js_size(row['bytes']['size']);digest_shape(row['bytes']['sha256'])
            need(row['type'] in ('chunk','asset','map'),'OUTPUT_KIND')
            if row['type']!='chunk':need(row['preliminary_path'] is None and row['sourcemap_path'] is None and row['module_ids']==[],'CORE_MAP_SHAPE')
        return rows
    for ordinal,o in enumerate(observations,1):
        need(o.get('schema')=='web-observation@1' and o.get('id')=='o'+str(ordinal),'OBSERVATION_ID');kind=o.get('kind');ids[o['id']]=o
        if kind=='hook_call':
            keys(o,{'schema','kind','id','parent_id','plugin_ordinal','hook','subject_token','chunk_id','input','result','status','watch_ids','emission_ids'})
            need(type(o['plugin_ordinal']) is int and 0<=o['plugin_ordinal']<len(profile['plugins']) and o['hook'] in hooks,'PRODUCER_INVALID')
            need(o['status'] in ('returned','no_result'),'CALL_INCOMPLETE')
            if o['parent_id'] is not None:need(o['parent_id'] in calls,'CALL_PARENT_INVALID')
            for value in (o['input'],o['result']):
                if value is not None:keys(value,{'size','sha256'});js_size(value['size']);digest_shape(value['sha256'])
            calls[o['id']]=o
            if o['hook']=='renderChunk':
                chain=render.setdefault(o['chunk_id'],[])
                if not chain:need(o['chunk_id'] in render_inputs and render_inputs[o['chunk_id']]==o['input'],'CORE_RENDER_INPUT_MISMATCH')
                if chain and chain[-1]['result'] is not None:need(o['input']==chain[-1]['result'],'RENDER_CHAIN_INVALID')
                chain.append(o)
            if o['subject_token'] is not None:s.resolve_subject_token(o['subject_token'])
        elif kind=='plugin_emission':
            keys(o,{'schema','kind','id','call_id','action','reference_id','emitted_type','declared_file_name','names','original_names','source','resolved_file_name'})
            need(o['call_id'] in calls and o['id'] in calls[o['call_id']]['emission_ids'],'CALL_PARENT_INVALID')
            need(o['action'] in ('emit','set_source') and isinstance(o['reference_id'],str) and o['reference_id'],'EMISSION_INVALID')
            if o['action']=='set_source':need(o['reference_id'] in refs,'EMIT_BEFORE_SET')
            refs[o['reference_id']]=o
        elif kind=='core_snapshot':
            keys(o,{'schema','kind','id','phase','chunk_id','predecessor_ids','plugin_ordinal','internal_parent_id','internal_reference_id','entries'})
            need(o['plugin_ordinal'] is None and o['internal_parent_id'] is None and o['internal_reference_id'] is None,'CORE_REFERENCE_INVALID');entries(o['entries'])
            if o['phase']=='generate_input':need(generated is None,'CORE_PHASE_DUPLICATE');generated=o['entries'];delta=o['id']
            elif o['phase']=='write_input':need(write is None,'CORE_PHASE_DUPLICATE');write=o['entries']
            else:
                need(o['phase']=='render_input' and len(o['entries'])==1 and o['chunk_id'] not in render_inputs,'CORE_PHASE_INVALID');render_inputs[o['chunk_id']]=o['entries'][0]['bytes']
            snapshots[o['id']]=o['entries']
        elif kind=='bundle_delta':
            keys(o,{'schema','kind','id','call_id','before_snapshot_id','after_entries'})
            need(o['call_id'] in calls and calls[o['call_id']]['hook']=='generateBundle' and o['before_snapshot_id']==delta,'BUNDLE_CHAIN_INVALID');entries(o['after_entries']);delta=o['id'];snapshots[delta]=o['after_entries']
        elif kind=='html_snapshot':
            keys(o,{'schema','kind','id','stage','document_id','input','result'});need(o['stage'] in ('pre','post') and o['input']==o['result'],'HTML_SNAPSHOT_INVALID')
        else:need(False,'OBSERVATION_KIND_INVALID',kind)
    need(generated is not None and write is not None and delta is not None,'GENERATED_RELATION_MISSING')
    need(write==snapshots[delta],'FINAL_WRITE_MISMATCH')
    need(entries(payload['final_bundle'])==write,'FINAL_BUNDLE_MISMATCH')
    for row in write:
        if row['type']=='chunk':
            need(row['preliminary_path'] in render and row['module_ids']==next(o['entries'][0]['module_ids'] for o in observations if o['kind']=='core_snapshot' and o['phase']=='render_input' and o['chunk_id']==row['preliminary_path']),'CORE_CHUNK_MAPPING')
            if row['sourcemap_path'] is not None:need(any(r['path']==row['sourcemap_path'] and r['type']=='map' for r in write),'CORE_MAP_PAIR_MISSING')
    plan_record=s.load('css-inputs',p['css_inputs']);plan=plan_record['payload']['plan']
    # Derive complete list and actual bytes independently on every reader.
    expected=s.prepare_css_inputs();need(plan==expected,'CSS_INPUT_SET_MISMATCH')
    result=validate_css(s,payload['css_operations'],plan,ids,loads)
    for operation in payload['css_operations']:
        if operation['stage']=='compile':
            parent=ids[operation['parent_outer_call']];data=operation['data'];raw=blob(s,data['input'])
            need(parent['hook']=='transform' and profile['plugins'][parent['plugin_ordinal']]['name']=='happyranch:packaging-tailwind' and parent['input']=={'size':len(raw),'sha256':sha(raw)},'CSS_COMPILE_INPUT_MISSING')
    for operation in payload['css_operations']:
        if operation['stage']!='css_emit':continue
        data=operation['data'];emitted=blob(s,data['source'])
        references=[o for o in observations if o['kind']=='plugin_emission' and o['reference_id']==data['referenceId']]
        need(len(references)==1,'CSS_EMISSION_RELATION_MISSING')
        filename=references[0]['resolved_file_name'];before=next((r for r in generated if r['path']==filename),None);after=next((r for r in write if r['path']==filename),None)
        need(before is not None and after is not None and before['bytes']=={'size':len(emitted),'sha256':sha(emitted)},'CSS_EMISSION_RELATION_MISSING')
        final=re.sub(rb'/\*\$vite\$:\d+\*/',b'',emitted,count=1)
        need(after['bytes']=={'size':len(final),'sha256':sha(final)},'CSS_FINALIZE_CHAIN_INVALID')
        need(any(o['kind']=='bundle_delta' and payload['profile']['plugins'][calls[o['call_id']]['plugin_ordinal']]['name']=='vite:css-post' and any(r['path']==filename and r['bytes']==after['bytes'] for r in o['after_entries']) for o in observations),'CSS_FINALIZE_CHAIN_INVALID')
    need(payload['modules'] and len(payload['modules'])<=MAX_ROWS,'MODULE_CAP')
    memberships=0;module_ids=set();all_members=set()
    for module in payload['modules']:
        keys(module,{'id','code','calls','source'})
        need(module['calls'] and all(c in calls for c in module['calls']),'GENERATED_RELATION_MISSING')
        need(module['id'] not in module_ids,'MODULE_DUPLICATE');module_ids.add(module['id'])
        memberships+=len(module['calls']);need(memberships<=MAX_OBSERVATIONS,'CALL_MEMBERSHIP_CAP')
        need(len(set(module['calls']))==len(module['calls']) and not all_members.intersection(module['calls']),'CALL_MEMBERSHIP_DUPLICATE');all_members.update(module['calls'])
        need(module['calls']==sorted(module['calls'],key=lambda v:int(v[1:])),'CALL_ORDER')
        current=None;observed_final=False
        if module['source'] is not None:
            path=Path(module['source']);need(path.is_relative_to(s.paths.exported_source/'web'),'MODULE_SOURCE_ROOT')
            token,raw=s.intern_subject(s.paths.exported_source,path.relative_to(s.paths.exported_source).as_posix())
            text=decode(raw).encode();current={'size':len(text),'sha256':sha(text)}
        for call_id in module['calls']:
            call=calls[call_id]
            if call['hook']=='load' and call['result'] is not None:current=call['result']
            elif call['hook']=='transform':
                need(current is not None,'GENERATED_RELATION_MISSING',module['id'])
                need(call['input']==current,'MODULE_TRANSFORM_CHAIN',module['id'])
                if call['result'] is not None:current=call['result']
            elif call['hook']=='moduleParsed':
                need(call['input']==current==module['code'],'MODULE_FINAL_CHAIN',module['id'])
                if profile['plugins'][call['plugin_ordinal']]['name']=='happyranch:packaging-custody':
                    need(not observed_final,'MODULE_FINAL_CHAIN',module['id']);observed_final=True
        need(observed_final and current==module['code'],'MODULE_FINAL_CHAIN',module['id'])
    for state in loads.values():
        need(any(module['id']==state['begin']['module'] and state['parent'] in module['calls'] for module in payload['modules']),'CSS_COMPILE_INPUT_MISSING')
    shipped={name for row in write if row['type']=='chunk' for name in row['module_ids']}
    need(shipped<={row['id'] for row in payload['modules']},'GENERATED_RELATION_MISSING')
    html=[o for o in observations if o['kind']=='html_snapshot' and o['stage']=='pre']
    need(len(html)==1,'HTML_SOURCE_MISSING')
    _,raw=s.raw(s.paths.exported_source,'web/index.html');text=decode(raw).encode()
    need(html[0]['input']=={'size':len(text),'sha256':sha(text)},'HTML_SOURCE_MISMATCH')
    output_html=next((row for row in write if row['path']=='index.html'),None)
    need(output_html is not None and any(o['kind']=='plugin_emission' and o['declared_file_name']=='index.html' and o['source']==output_html['bytes'] and payload['profile']['plugins'][calls[o['call_id']]['plugin_ordinal']]['name']=='vite:build-html' for o in observations),'HTML_EMISSION_MISSING')
    associations=source_associations(s,p,payload)
    if stored:
        need(set(stored)=={'subjects','source_tool_associations'} and stored['subjects']==s.subjects and stored['source_tool_associations']==associations,'SOURCE_ASSOCIATION_MISMATCH')
    need(sum(len(r['outer_ids']) for r in associations)<=MAX_OBSERVATIONS,'ASSOCIATION_MEMBERSHIP_CAP')
    need(producer_work(payload)==payload['counts']['processed_bytes'],'BODY_WORK_MISMATCH')
    metadata_limits(payload,initial_names=s.load_names)
    s.associations=associations
    return result


def source_associations(s,context,payload):
    tools=context['tools'];sources={r['path']:r['token'] for r in context['sources']};rows=[]
    def member(package,name):
        matches=[r['token'] for r in tools if r['package']==package and r['member']=='package/'+name]
        need(len(matches)==1,'GENERATOR_IMPLEMENTATION_MISSING',package+'/'+name);return matches[0]
    vite=member('vite','dist/node/chunks/dep-BK3b2jBa.js');rollup=member('rollup','dist/es/shared/node-entry.js')
    for plugin in payload['profile']['plugins']:
        outer=[o['id'] for o in payload['observations'] if o['kind']=='hook_call' and o['plugin_ordinal']==plugin['ordinal']]
        if not outer:continue
        name=plugin['name'];tool_tokens=[]
        if name in ('happyranch:html-pre','happyranch:html-post','happyranch:packaging-custody'):implementation=sources['web/scripts/packaging-custody.ts'];site=['web/scripts/packaging-custody.ts',name]
        elif name=='i18n-browser-evidence-instrumentation':implementation=sources['web/vite.config.ts'];site=['web/vite.config.ts','i18nEvidenceInstrumentation']
        elif name=='happyranch:packaging-tailwind':
            implementation=sources['web/scripts/packaging-tailwind.ts'];site=['web/scripts/packaging-tailwind.ts','compile/scanFiles/build/optimize']
            tool_tokens=[member('tailwindcss','dist/lib.mjs'),member('@tailwindcss/node','dist/index.mjs'),member('@tailwindcss/oxide','index.js'),member('lightningcss','node/index.js')]
        elif name in ('vite:react-babel','vite:react-refresh'):implementation=member('@vitejs/plugin-react','dist/index.js');site=['@vitejs/plugin-react/dist/index.js',name]
        else:
            implementation=vite;site=['vite/dist/node/chunks/dep-BK3b2jBa.js',name]
            if name in ('vite:esbuild','vite:define','vite:esbuild-transpile','vite:css-post'):tool_tokens=[member('esbuild','lib/main.js')]
        rows.append({'kind':'source_tool_association','outer_ids':outer,'implementation_token':implementation,'tool_tokens':tool_tokens,'source_site':site,'relation':'source_derived'})
    core=[o['id'] for o in payload['observations'] if o['kind']=='core_snapshot']
    rows.append({'kind':'source_tool_association','outer_ids':core,'implementation_token':rollup,'tool_tokens':[],'source_site':['rollup/dist/es/shared/node-entry.js','renderChunks/addChunksToBundle/finaliseAssets/writeOutputFile'],'relation':'source_derived'})
    return rows


def producer_work(payload):
    total=js_size(payload['counts']['transport_bytes'],BODY_WORK);seen=set()
    def digests(value):
        nonlocal total
        if isinstance(value,dict):
            if set(value)=={'size','sha256'}:
                js_size(value['size']);digest_shape(value['sha256']);total+=value['size']
                need(total<=BODY_WORK,'AGGREGATE_BODY_CAP')
            else:
                for child in value.values():digests(child)
        elif isinstance(value,list):
            for child in value:digests(child)
    digests(payload['observations']);digests(payload['final_bundle']);digests(payload['profile']['env'])
    def snapshots(value):
        nonlocal total
        if isinstance(value,dict):
            if set(value)=={'path','size','sha256','descriptor_sha256'}:
                need(value['path'] not in seen,'SNAPSHOT_REFERENCE_DUPLICATE')
                seen.add(value['path']);js_size(value['size']);total+=value['size']
                need(total<=BODY_WORK and len(seen)<=MAX_ROWS,'AGGREGATE_BODY_CAP')
            else:
                for child in value.values():snapshots(child)
        elif isinstance(value,list):
            for child in value:snapshots(child)
    snapshots(payload['css_operations'])
    for operation in payload['css_operations']:
        if operation['stage']=='compile':
            total+=operation['data']['input']['size']
            need(total<=BODY_WORK,'AGGREGATE_BODY_CAP')
    return total


def metadata_limits(payload, *, initial_names=0):
    # Occurrences of logical names, including repeated names, never a set of paths.
    names=initial_names
    need(names<=8*MiB,'AGGREGATE_NAME_CAP')
    fields={'importer','selected','execPath','loaded','request','path','preliminary_path','sourcemap_path','chunk_id','document_id','reference_id',
            'declared_file_name','resolved_file_name','names','original_names','watch_ids',
            'module_ids','id','source','module','file','base','from','relative','relative_path',
            'filename','cssAssetName','originalFileName','source_site','config_dependencies',
            'input','root','dir','outDir','assetsDir','name'}
    def visit(value,field='',depth=0):
        nonlocal names
        need(depth<=32,'JSON_COMPLEXITY_CAP')
        if isinstance(value,str):
            raw=value.encode('utf-8','strict');need(len(raw)<=4096,'METADATA_STRING_CAP')
            if field in fields:
                names+=len(raw);need(names<=8*MiB,'AGGREGATE_NAME_CAP')
        elif isinstance(value,dict):
            for key,child in value.items():visit(child,key,depth+1)
        elif isinstance(value,list):
            need(len(value)<=MAX_OBSERVATIONS,'METADATA_LIST_CAP')
            for child in value:visit(child,field,depth+1)
    visit(payload)
    return names


def build_token(s,value):
    keys(value,{'schema','record_sha256','context_binding_sha256'})
    need(value['schema']=='web-build-token@1' and value['context_binding_sha256']==s.binding_sha,'COMPLETION_TOKEN_INVALID')
    digest_shape(value['record_sha256']);digest_shape(value['context_binding_sha256'])
    return value


def completion_body(s,context,preparation,token,leaf):
    need(leaf==COMPLETION_LEAF,'COMPLETION_LEAF_INVALID')
    digest_shape(context);digest_shape(preparation);build_token(s,token)
    return {'schema':'web-completion@1','context_binding_sha256':s.binding_sha,
            'context_sha256':context,'preparation_sha256':preparation,
            'completion_leaf':leaf,'build_token':token}


def completion_prefix(s,context,preparation,leaf):
    need(leaf==COMPLETION_LEAF,'COMPLETION_LEAF_INVALID')
    attempt=s.load('attempt',s.raw(s.paths.evidence_dir,'web-attempt.json',KINDS['attempt'])[0]['sha256'])
    need(attempt['payload']=={'binding_sha256':s.binding_sha,'completion_leaf':leaf},'COMPLETION_ATTEMPT_MISMATCH')
    if s.terminal:s.terminal.enter_phase('prefix-context')
    p=load_context(s,context,preparation)
    if s.terminal:s.terminal.enter_phase('prefix')
    need(p['completion_leaf']==leaf,'COMPLETION_LEAF_INVALID')
    with A['root_for'](s.paths.evidence_dir) as root:
        need(is_absent(root,'web-failure.json'),'FAILED_BUILD')


@contextlib.contextmanager
def completion_context(manager):
    """Preserve primary I/O errors across held-directory cleanup as well."""
    resource=manager.__enter__()
    try:
        yield resource
    except BaseException as primary:
        try:manager.__exit__(type(primary),primary,primary.__traceback__)
        except BaseException as secondary:
            primary.add_note('secondary completion directory/close: '+str(secondary)[:4096])
        raise
    else:
        manager.__exit__(None,None,None)


def completion_write(root,leaf,raw):
    # Preserve an original write/fsync failure if close fails as well.
    with completion_context(A['parent_at'](root,leaf)) as (parent,name):
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
        primary=None
        try:
            view=memoryview(raw)
            while view:
                n=os.write(fd,view[:65536]);need(n>0,'COMPLETION_WRITE_INCOMPLETE');view=view[n:]
            os.fsync(fd)
        except BaseException as exc:
            primary=exc;raise
        finally:
            try:os.close(fd)
            except OSError as exc:
                if primary is None:raise
                primary.add_note('secondary completion close: '+str(exc)[:4096])
    row,actual=A['read_bytes'](root,leaf,A['ReadDomain'](4096,True))
    need(actual==raw and row['sha256']==sha(raw),'COMPLETION_READBACK_MISMATCH')


def complete_build(s,context,preparation,token,leaf=COMPLETION_LEAF):
    if s.terminal:s.terminal.enter_phase('completion-body')
    body=completion_body(s,context,preparation,token,leaf)
    if s.terminal:s.terminal.enter_phase('prefix')
    completion_prefix(s,context,preparation,leaf)
    checked_build(s,context,preparation,token['record_sha256'])
    if s.terminal:s.terminal.enter_phase('completion-write');s.terminal.position('completion-write')
    raw=canonical(body,4095)+b'\n';need(len(raw)==555,'COMPLETION_TOKEN_INVALID')
    with completion_context(A['root_for'](s.paths.evidence_dir)) as root:
        completion_write(root,leaf,raw)
    # main emits these SAME canonical bytes only after the guarded readback.
    return body


def accept_build(s,context,preparation,leaf=COMPLETION_LEAF):
    need(leaf==COMPLETION_LEAF,'COMPLETION_LEAF_INVALID')
    descriptor,raw=s.raw(s.paths.evidence_dir,leaf,4096)
    value=parse(raw,4096)
    keys(value,{'schema','context_binding_sha256','context_sha256','preparation_sha256','completion_leaf','build_token'})
    expected=completion_body(s,context,preparation,value['build_token'],leaf)
    need(value==expected and raw==canonical(expected,4095)+b'\n','COMPLETION_TOKEN_INVALID')
    completion_prefix(s,context,preparation,leaf)
    checked_build(s,context,preparation,value['build_token']['record_sha256'])
    after,reopened=s.raw(s.paths.evidence_dir,leaf,4096)
    need(after==descriptor and reopened==raw,'COMPLETION_UNSTABLE')
    return value['build_token']


def publish_build(s,context,preparation,payload):
    seal_loads(s,context,preparation)
    result=validate_build(s,payload,context,preparation)
    return s.write('build',{**payload,'subjects':s.subjects,'source_tool_associations':s.associations},{'context':context,'preparation':preparation,'css_relation':result})


def checked_build(s,context,preparation,build):
    if s.terminal:s.terminal.enter_phase('checked-build-record')
    record=s.load('build',build)
    if s.terminal:s.terminal.enter_phase('validation')
    result=validate_build(s,record['payload'],context,preparation,stored_required=True)
    need(record['upstream']=={'context':context,'preparation':preparation,'css_relation':result},'BUILD_UPSTREAM_MISMATCH')
    return record


def seal_dist(s,context,preparation,build,asset_dist=None):
    record=checked_build(s,context,preparation,build)
    generated=record['payload']['final_bundle']
    with A['root_for'](s.paths.exported_source/'web/public') as root:public=A['census'](root)
    with A['root_for'](s.paths.dist) as root:dist=A['census'](root)
    def alias(name):return unicodedata.normalize('NFC',name).casefold()
    for g in generated:
        for p in public['files']:
            a,b=alias(g['path']),alias(p['relative']);need(not(a==b or a.startswith(b+'/') or b.startswith(a+'/')),'PUBLIC_OUTPUT_COLLISION',g['path'])
    expected={r['path']:r['bytes'] for r in generated}
    expected.update({r['relative']:{k:r[k] for k in ('size','sha256')} for r in public['files']})
    need(expected=={r['relative']:{k:r[k] for k in ('size','sha256')} for r in dist['files']},'DIST_CENSUS_MISMATCH')
    if s.binding['venue']=='native':
        need(asset_dist is not None,'ASSET_DIST_TOKEN_REQUIRED')
        with A['_budget'].get().asset_lane():
            A['check_dist_before_copy'](s.candidate,s.paths,s.run,s.binding['asset_context_sha256'],asset_dist)
    return s.write('dist',{'census':dist,'public':public,'generated':generated},{'context':context,'preparation':preparation,'build':build,'asset_dist':asset_dist})


def retain_web_notices(s,context,preparation,build,dist,asset_app,components):
    p=load_context(s,context,preparation);record=checked_build(s,context,preparation,build)
    d=s.load('dist',dist);need(d['upstream']['build']==build,'DIST_BINDING')
    if s.binding['venue']=='native':
        with A['_budget'].get().asset_lane():
            A['load_record']('app',s.candidate,s.paths,s.run,asset_app,s.binding['asset_context_sha256'])
    rows=[];refused=[];retained=[]
    owners={'web/node_modules/'+r['package']:r['package'] for r in p['originals'] if 'darwin-arm64' not in r['package']}
    owners['web/node_modules/highlight.js']='highlight.js'
    # Ordinary participating owners use the innermost installed manifest; no
    # parent climbing and no source-map-only provenance assumption.
    for module in record['payload']['modules']:
        path=module['source']
        if path and '/node_modules/' in path:
            prefix,relative=path.rsplit('/node_modules/',1);parts=relative.split('/');name='/'.join(parts[:2]) if parts[0].startswith('@') else parts[0];base=Path(prefix+'/node_modules/'+name).relative_to(s.paths.exported_source).as_posix();owners[base]=name
    need(len(owners)<=1024,'OWNER_CAP')
    for base,owner in sorted(owners.items()):
        owner_id=sha(base.encode());notices=[];problem=None
        try:
            token,raw=s.intern_subject(s.paths.exported_source,base+'/package.json',256*1024);manifest=parse(raw)
            need(manifest.get('name')==owner and type(manifest.get('version')) is str and bool(manifest['version']),'OWNER_METADATA_CONFLICT',base)
            with A['root_for'](s.paths.exported_source/base) as root:
                names=[n for n in sorted(os.listdir(root.fd)) if re.match(r'(?i)^(licen[cs]e|notice|copying)(?:[.\-_].*)?$',n)]
            need(len(names)<=32,'OWNER_ORIGINAL_CAP',owner)
            notice_sources=[base+'/'+name for name in names]
            if not names and owner.startswith('@radix-ui/'):
                # Reuse only the existing exact fallback, after guarding its
                # implementation, identity map, inputs and metadata ourselves.
                helper='packaging/notice-data/retained_notices.py'
                helper_token,_=s.intern_subject(s.paths.exported_source,helper)
                with A['root_for'](s.paths.exported_source) as root:
                    A['verify_git_subject'](s.candidate,s.paths,root,helper,s.subjects[int(helper_token['subject_id'][1:])-1]['descriptor'])
                s.intern_subject(s.paths.exported_source,'packaging/notice-data/identity-map.json',256*1024)
                inputs=[]
                for module in record['payload']['modules']:
                    path=module['source']
                    if path and Path(path).is_relative_to(s.paths.exported_source/base):
                        relative=Path(path).relative_to(s.paths.exported_source).as_posix()
                        source_token,_=s.intern_subject(s.paths.exported_source,relative)
                        inputs.append({'input':path,'source_sha256':source_token['content']['sha256'],'metadata_chain':[{'path':str(s.paths.exported_source/base/'package.json'),'sha256':token['content']['sha256']}]})
                helpers=runpy.run_path(str(s.paths.exported_source/helper))
                recovered=helpers['radix_original'](s.paths.exported_source/'packaging/notice-data',owner,manifest['version'],s.paths.exported_source/base,inputs)
                if recovered is not None:
                    original_path,_=recovered
                    need(original_path.is_relative_to(s.paths.exported_source/'packaging/notice-data'),'OWNER_FALLBACK_ROOT')
                    notice_sources=[original_path.relative_to(s.paths.exported_source).as_posix()]
            need(notice_sources,'OWNER_ORIGINAL_MISSING',owner)
            pending=[]
            for source in notice_sources:
                original,raw=s.intern_subject(s.paths.exported_source,source,256*1024);need(raw and decode(raw).encode()==raw,'OWNER_ORIGINAL_UTF8',owner)
                target='Contents/Resources/licenses/web-generated/'+owner_id+'/'+Path(source).name
                with A['root_for'](s.paths.app) as root:
                    if not is_absent(root,target):
                        existing=A['read_subject'](root,target,A['NOTICE']);need(existing['sha256']==sha(raw) and existing['size']==len(raw),'OWNER_TARGET_CONFLICT',owner)
                pending.append((original,source,target))
            # Prevalidate ALL sources/targets before creating any for this owner.
            for original,source,target in pending:
                with A['root_for'](s.paths.exported_source) as source_root,A['root_for'](s.paths.app) as target_root:
                    A['copy_once'](source_root,source,target_root,target,A['NOTICE'],allow_identical_existing=True)
                    retained_row=A['read_subject'](target_root,target,A['NOTICE'])
                notices.append({'destination':target,'sha256':retained_row['sha256']});retained.append({'owner':owner,'original':original,'target':retained_row})
        except (OSError,ValueError,KeyError) as exc:problem=str(exc);refused.append({'owner':base,'reason':problem})
        rows.append({'component':owner,'scope':'web-generated','disposition':'LICENSE_UNRESOLVED' if problem else 'RETAINED_TEXT','reason':problem or 'candidate_notice_retention; applicability unestablished','inclusion':[{'package_root':base}],'notices':notices if not problem else []})
    token=s.write('notices',{'prefix':[sha(canonical(r)) for r in components],'appended':rows,'retained':retained,'refused':refused,'subjects':s.subjects},{'context':context,'preparation':preparation,'build':build,'dist':dist,'asset_app':asset_app})
    return rows,token


def verify_web(s,context,preparation,build,dist,notices=None,asset_app=None,mapping=None):
    record=checked_build(s,context,preparation,build)
    d=s.load('dist',dist)
    need(d['upstream']['context']==context and d['upstream']['preparation']==preparation and d['upstream']['build']==build,'DIST_BINDING')
    with A['root_for'](s.paths.dist) as root:need(A['census'](root)==d['payload']['census'],'DIST_CENSUS_MISMATCH')
    with A['root_for'](s.paths.exported_source/'web/public') as root:need(A['census'](root)==d['payload']['public'],'PUBLIC_CENSUS_MISMATCH')
    need(d['payload']['generated']==record['payload']['final_bundle'],'DIST_GENERATED_BINDING')
    public=d['payload']['public']['files'];generated=d['payload']['generated']
    def alias(value):return unicodedata.normalize('NFC',value).casefold()
    for g in generated:
        for p in public:
            a,b=alias(g['path']),alias(p['relative']);need(not(a==b or a.startswith(b+'/') or b.startswith(a+'/')),'PUBLIC_OUTPUT_COLLISION',g['path'])
    expected={r['path']:r['bytes'] for r in generated};expected.update({r['relative']:{k:r[k] for k in ('size','sha256')} for r in public})
    need(expected=={r['relative']:{k:r[k] for k in ('size','sha256')} for r in d['payload']['census']['files']},'DIST_CENSUS_MISMATCH')
    if notices:
        n=s.load('notices',notices);p=n['payload'];keys(p,{'prefix','appended','retained','refused','subjects'})
        need(n['upstream']=={'context':context,'preparation':preparation,'build':build,'dist':dist,'asset_app':asset_app},'NOTICES_BINDING')
        need(mapping is not None and mapping['components'][-len(p['appended']):]==p['appended'] and [sha(canonical(r)) for r in mapping['components'][:-len(p['appended'])]]==p['prefix'],'MAPPING_PREFIX_CHANGED')
        need(p['subjects'][:len(s.subjects)]==s.subjects,'SUBJECT_PREFIX_MISMATCH')
        s.subjects=[];s.subject_index={};s.import_subjects(p['subjects'])
        for row in p['retained']:
            s.resolve_subject_token(row['original'])
            with A['root_for'](s.paths.app) as root:need(A['read_subject'](root,row['target']['relative'],A['NOTICE'])==row['target'],'NOTICE_TARGET_MISMATCH')
        with A['root_for'](s.paths.app/A['APP_WEB']) as root:app=A['census'](root)
        expected={r['relative']:{k:r[k] for k in ('size','sha256')} for r in d['payload']['census']['files'] if not r['relative'].endswith('.map')}
        need(expected=={r['relative']:{k:r[k] for k in ('size','sha256')} for r in app['files']},'APP_CENSUS_MISMATCH')
    return {'web_observation':'VALIDATED_LOCAL_RELATIONS','venue':s.binding['venue'],'available':False,'approved_url':None}


def add_arguments(parser):
    for name in ('web-caller','web-context-sha256','web-preparation-sha256','web-build-sha256','web-dist-sha256','web-notices-sha256','web-tool-originals'):
        parser.add_argument('--'+name,required=True,action=A['Once'])

@web_bounded
def verify_arguments(args):
    s=Session(parse(args.web_caller.encode(),256*1024))
    need(str(s.originals)==args.web_tool_originals,'ORIGINALS_CALLER_MISMATCH')
    with A['root_for'](args.mapping.parent) as root:_,raw=A['read_bytes'](root,args.mapping.name,A['ReadDomain'](8*MiB))
    return verify_web(s,args.web_context_sha256,args.web_preparation_sha256,args.web_build_sha256,args.web_dist_sha256,args.web_notices_sha256,args.asset_app_sha256,parse(raw,8*MiB))


@web_bounded
def main():
    p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('stage',choices=['prepare','begin','compile-begin','compile-end','read-input','snapshot','publish-build','complete-build','accept-build','seal-dist','notices','verify','fail','token-field'])
    p.add_argument('--caller');p.add_argument('--context');p.add_argument('--preparation');p.add_argument('--build');p.add_argument('--dist');p.add_argument('--notices');p.add_argument('--asset-dist');p.add_argument('--asset-app');p.add_argument('--index',type=int);p.add_argument('--token');p.add_argument('--field');p.add_argument('--original-exit',type=int);p.add_argument('--completion-leaf',default=COMPLETION_LEAF)
    p.add_argument('--snapshot-diagnostic-call',nargs='?');p.add_argument('--snapshot-diagnostic-bytes',nargs='?')
    p.add_argument('--complete-diagnostic-call',nargs='?');p.add_argument('--complete-diagnostic-bytes',nargs='?')
    a=p.parse_args()
    if a.stage=='token-field':
        token=parse(a.token.encode(),4096);value=token[a.field];print(json.dumps(value,separators=(',',':')) if isinstance(value,(dict,list)) else value);return
    diagnostics=SnapshotDiagnostics(a.snapshot_diagnostic_call,a.snapshot_diagnostic_bytes) if a.stage=='snapshot' else None
    terminal=CompleteBuildDiagnostics(a.complete_diagnostic_call,a.complete_diagnostic_bytes) if a.stage=='complete-build' else None
    try:
        s=Session(parse(a.caller.encode(),256*1024),diagnostics,terminal)
        try:
            result=dispatch(s,a)
        except (OSError,ValueError,KeyError,tarfile.TarError) as primary:
            if a.stage in ('compile-begin','compile-end','read-input','snapshot','publish-build'):
                try:s.write('failure',{'original_exit':1,'stage':a.stage})
                except (OSError,ValueError,KeyError,tarfile.TarError) as secondary:primary.add_note('secondary failure receipt: '+str(secondary)[:4096])
            raise
    except BaseException:
        if terminal:terminal.finish('failed')
        raise
    if terminal:terminal.finish('success')
    return result


def dispatch(s: Session,a: argparse.Namespace) -> None:
    def request() -> dict:
        raw=sys.stdin.buffer.read(4097);s.request_bytes=len(raw)
        return parse(raw,4096)
    def snapshot_request():
        if s.diagnostics:s.diagnostics.mark('stdin','start')
        raw=sys.stdin.buffer.read(CAP+1)
        if s.diagnostics:s.diagnostics.mark('stdin','end')
        return raw
    if a.stage=='prepare':result=prepare_esbuild_alias(s)
    elif a.stage=='begin':result=begin_web(s,a.preparation,a.completion_leaf)
    elif a.stage in ('compile-begin','compile-end'):
        result=compile_event(s,a.context,a.preparation,a.index,a.stage.replace('-','_'),request())
    elif a.stage=='read-input':result=css_read(s,a.context,a.preparation,request(),a.index)
    elif a.stage=='snapshot':result=snapshot(s,a.context,a.preparation,a.index,snapshot_request())
    elif a.stage=='publish-build':result=publish_build(s,a.context,a.preparation,parse(sys.stdin.buffer.read(BUILD_INPUT+1),BUILD_INPUT,build=True))
    elif a.stage=='complete-build':
        if s.terminal:s.terminal.enter_phase('completion-body');s.terminal.position('token-json')
        result=complete_build(s,a.context,a.preparation,parse(sys.stdin.buffer.read(4097),4096),a.completion_leaf)
    elif a.stage=='accept-build':result=accept_build(s,a.context,a.preparation,a.completion_leaf)
    elif a.stage=='seal-dist':result=seal_dist(s,a.context,a.preparation,a.build,a.asset_dist)
    elif a.stage=='notices':
        mapping=parse(sys.stdin.buffer.read(8*MiB+1),8*MiB);rows,token=retain_web_notices(s,a.context,a.preparation,a.build,a.dist,a.asset_app,mapping['components']);result={'rows':rows,'token':token}
    elif a.stage=='verify':result=verify_web(s,a.context,a.preparation,a.build,a.dist,a.notices,a.asset_app,parse(sys.stdin.buffer.read(8*MiB+1),8*MiB) if a.notices else None)
    else:result=s.write('failure',{'original_exit':a.original_exit,'stage':os.environ.get('STAGE','web')})
    if s.diagnostics:s.diagnostics.mark('reply','start')
    if s.terminal:s.terminal.enter_phase('reply')
    sys.stdout.buffer.write(canonical(result,32*MiB if a.stage=='read-input' else CAP)+b'\n')
    sys.stdout.buffer.flush()
    if s.diagnostics:s.diagnostics.mark('reply','end')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,tarfile.TarError) as exc:
        print(str(exc),file=sys.stderr)
        for note in getattr(exc,'__notes__',())[:8]:print(note[:4096],file=sys.stderr)
        raise SystemExit(1)
