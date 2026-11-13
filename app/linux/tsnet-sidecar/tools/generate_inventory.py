#!/usr/bin/env python3
"""Generate and verify the exact platform build-module inventory and notices."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"linux-amd64": ("linux", "amd64", "Linux"), "darwin-arm64": ("darwin", "arm64", "Darwin") }

def go(goos: str, goarch: str, *args: str) -> str:
    env = os.environ.copy()
    env.update({"GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0"})
    env["GOFLAGS"] = env.get("GOFLAGS", "") + " -mod=readonly"
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, default="linux-amd64")
    parser.add_argument("--check", action="store_true", help="verify committed bytes without writing")
    args = parser.parse_args()
    goos, goarch, platform = TARGETS[args.target]
    out = ROOT / "third_party"
    if args.target != "linux-amd64":
        out = out / args.target
    packages=objects(go(goos, goarch, "list", "-deps", "-json", "."))
    modules: dict[tuple[str,str], dict[str, object]]={}
    for pkg in packages:
        mod=pkg.get("Module")
        if not isinstance(mod,dict) or mod.get("Main"): continue
        if isinstance(mod.get("Replace"),dict): mod=mod["Replace"]
        key=(str(mod["Path"]),str(mod["Version"]))
        modules[key]={"module":key[0],"version":key[1],"sum":str(mod.get("Sum", "")),"dir":str(mod["Dir"])}
    graph=set(line.split()[1] for line in go(goos,goarch,"mod","graph").splitlines() if len(line.split())==2)
    records=[]; texts: dict[str,dict[str,object]]={}
    sums={line.split()[0]+"@"+line.split()[1]:line.split()[2] for line in (ROOT/"go.sum").read_text().splitlines() if len(line.split())==3 and not line.split()[1].endswith("/go.mod")}
    for key in sorted(modules):
        item=modules[key]; coordinate=f"{key[0]}@{key[1]}"
        if coordinate not in graph: raise SystemExit(f"build module absent from graph: {coordinate}")
        if not item["sum"] or sums.get(coordinate) != item["sum"]: raise SystemExit(f"checksum mismatch: {coordinate}")
        path=license_file(Path(str(item.pop("dir")))); text="\n".join(line.rstrip() for line in path.read_text(errors="strict").splitlines()).rstrip()+"\n"
        ident=hashlib.sha256(text.encode()).hexdigest(); kind=spdx(text)
        item.update({"source":f"https://{key[0]}","spdx":kind,"license_sha256":ident,"relationship":f"statically-linked-{goos}-build-input"})
        records.append(item); texts.setdefault(ident,{"spdx":kind,"text":text,"modules":[]})["modules"].append(coordinate)
    payload={"schema_version":1,"artifact":{"goos":goos,"goarch":goarch,"cgo_enabled":False,"package":"happyranch/linux-tsnet-sidecar"},"generator":"tools/generate_inventory.py","modules":records}
    inventory = json.dumps(payload,indent=2,sort_keys=True)+"\n"
    notice=[f"# HappyRanch {platform} embedded-tailnet sidecar — third-party notices input", "", f"Generated from checksum-pinned modules that enter the GOOS={goos}/GOARCH={goarch}/CGO_ENABLED=0 artifact.", "Factual dependency identification only. No copyright-holder or contributor name implies endorsement or promotion.", "A later binary distribution must ship this generated notice content; N1 is build/test only and makes no distribution claim.", ""]
    for ident,group in sorted(texts.items()):
        notice += ["---", "", "Modules:"]+[f"- {m}" for m in group["modules"]]+["",f"SPDX: {group['spdx']}",f"License-SHA256: {ident}","","```text",str(group["text"]).rstrip(),"```",""]
    files = {"dependency-inventory.json": inventory, "THIRD_PARTY_NOTICES.md": "\n".join(notice)}
    if args.check:
        for name, content in files.items():
            path = out / name
            if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
                raise SystemExit(f"inventory/notice drift: {path.relative_to(ROOT)}")
    else:
        out.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (out / name).write_text(content, encoding="utf-8")
    print(f"verified {len(records)} build modules, {len(packages)} packages, {len(graph)} graph edges")

if __name__ == "__main__": main()
