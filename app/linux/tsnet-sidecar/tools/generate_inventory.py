#!/usr/bin/env python3
"""Generate and verify the exact Linux build-module inventory and notices."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "third_party"

def go(*args: str) -> str:
    env = os.environ.copy()
    env.update({"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0"})
    return subprocess.run([env.get("GO", "go"), *args], cwd=ROOT, env=env, text=True, check=True, stdout=subprocess.PIPE).stdout

def objects(raw: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder(); result=[]; pos=0
    while pos < len(raw):
        while pos < len(raw) and raw[pos].isspace(): pos += 1
        if pos == len(raw): break
        value, pos = decoder.raw_decode(raw, pos); result.append(value)
    return result

def license_file(module_dir: Path) -> Path:
    candidates = sorted(p for p in module_dir.iterdir() if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING")))
    if not candidates: raise SystemExit(f"uncertain license: {module_dir.name}")
    return candidates[0]

def spdx(text: str) -> str:
    lower=text.lower()
    if "redistribution and use in source and binary forms" in lower and "neither the name" in lower: return "BSD-3-Clause"
    if "redistribution and use in source and binary forms" in lower: return "BSD-2-Clause"
    if "permission is hereby granted, free of charge" in lower: return "MIT"
    if "apache license" in lower and "version 2.0" in lower: return "Apache-2.0"
    if "isc license" in lower or ("permission to use, copy, modify" in lower and "the software is provided \"as is\"" in lower): return "ISC"
    if "mozilla public license version 2.0" in lower: return "MPL-2.0"
    raise SystemExit("uncertain license text: " + hashlib.sha256(text.encode()).hexdigest())

def main() -> None:
    packages=objects(go("list", "-deps", "-json", "."))
    modules: dict[tuple[str,str], dict[str, object]]={}
    for pkg in packages:
        mod=pkg.get("Module")
        if not isinstance(mod,dict) or mod.get("Main"): continue
        if isinstance(mod.get("Replace"),dict): mod=mod["Replace"]
        key=(str(mod["Path"]),str(mod["Version"]))
        modules[key]={"module":key[0],"version":key[1],"sum":str(mod.get("Sum", "")),"dir":str(mod["Dir"])}
    graph=set(line.split()[1] for line in go("mod","graph").splitlines() if len(line.split())==2)
    records=[]; texts: dict[str,dict[str,object]]={}
    sums={line.split()[0]+"@"+line.split()[1]:line.split()[2] for line in (ROOT/"go.sum").read_text().splitlines() if len(line.split())==3 and not line.split()[1].endswith("/go.mod")}
    for key in sorted(modules):
        item=modules[key]; coordinate=f"{key[0]}@{key[1]}"
        if coordinate not in graph: raise SystemExit(f"build module absent from graph: {coordinate}")
        if not item["sum"] or sums.get(coordinate) != item["sum"]: raise SystemExit(f"checksum mismatch: {coordinate}")
        path=license_file(Path(str(item.pop("dir")))); text="\n".join(line.rstrip() for line in path.read_text(errors="strict").splitlines()).rstrip()+"\n"
        ident=hashlib.sha256(text.encode()).hexdigest(); kind=spdx(text)
        item.update({"source":f"https://{key[0]}","spdx":kind,"license_sha256":ident,"relationship":"statically-linked-linux-build-input"})
        records.append(item); texts.setdefault(ident,{"spdx":kind,"text":text,"modules":[]})["modules"].append(coordinate)
    payload={"schema_version":1,"artifact":{"goos":"linux","goarch":"amd64","cgo_enabled":False,"package":"happyranch/linux-tsnet-sidecar"},"generator":"tools/generate_inventory.py","modules":records}
    OUT.mkdir(exist_ok=True)
    (OUT/"dependency-inventory.json").write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    notice=["# HappyRanch Linux embedded-tailnet sidecar — third-party notices input", "", "Generated from checksum-pinned modules that enter the GOOS=linux/GOARCH=amd64/CGO_ENABLED=0 artifact.", "Factual dependency identification only. No copyright-holder or contributor name implies endorsement or promotion.", "A later binary distribution must ship this generated notice content; N1 is build/test only and makes no distribution claim.", ""]
    for ident,group in sorted(texts.items()):
        notice += ["---", "", "Modules:"]+[f"- {m}" for m in group["modules"]]+["",f"SPDX: {group['spdx']}",f"License-SHA256: {ident}","","```text",str(group["text"]).rstrip(),"```",""]
    (OUT/"THIRD_PARTY_NOTICES.md").write_text("\n".join(notice))
    print(f"verified {len(records)} build modules, {len(packages)} packages, {len(graph)} graph edges")

if __name__ == "__main__": main()
