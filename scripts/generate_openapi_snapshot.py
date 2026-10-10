"""Check the API summary without writing; --write atomically accepts reviewed drift."""
from __future__ import annotations

import json
import sys

def _summarize(schema: dict) -> dict:
    """Reduce the schema to only the surface area we want to pin.

    Full schemas include FastAPI-generated component refs that churn on every
    Pydantic upgrade — too noisy. We pin paths + methods + parameter names +
    response codes. That's the contract the TS client cares about.
    """
    paths: dict = {}
    for path, methods in sorted(schema.get("paths", {}).items()):
        path_summary: dict = {}
        for method, op in sorted(methods.items()):
            if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
                continue
            params = sorted(
                [p["name"], p.get("in")]
                for p in op.get("parameters", [])
            )
            responses = sorted(op.get("responses", {}).keys())
            path_summary[method.upper()] = {
                "params": params,
                "responses": responses,
            }
            if path == '/api/v1/orgs/{slug}/workflows/activations' and method.upper() == 'POST':
                body = op['requestBody']['content']['application/json']['schema']
                path_summary['POST']['input_discriminators'] = [branch['properties']['inputs']['items']['discriminator'] for branch in body['anyOf']]
        if path_summary:
            paths[path] = path_summary
    return {"paths": paths}

def main(argv: list[str] | None = None) -> int:
    import argparse
    import difflib
    import os
    import tempfile
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Compare without writing (default)')
    mode.add_argument('--write', action='store_true', help='Atomically replace the reviewed snapshot')
    args = parser.parse_args(argv)
    # Operator execution only: importing the pure summarizer starts no runtime.
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.config import Settings

    target = Path(__file__).resolve().parents[1] / 'tests/contract/openapi.json'
    try:
        payload = (json.dumps(_summarize(create_app(DaemonState.idle(Settings())).openapi()),
                              indent=2, sort_keys=True) + '\n').encode()
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('snapshot target must be a regular file')
        if args.write:
            fd, temporary = tempfile.mkstemp(prefix='.openapi-', suffix='.json', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if target.exists():
                    os.chmod(temporary, target.stat().st_mode & 0o777)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            print('OpenAPI snapshot written')
            return 0
        stored = target.read_bytes() if target.is_file() else b''
        if stored == payload:
            print('OpenAPI snapshot matches')
            return 0
        print('OpenAPI snapshot differs; review before --write')
        delta = difflib.unified_diff(stored.decode('utf-8', errors='replace').splitlines(),
                                     payload.decode().splitlines(), fromfile='stored', tofile='current')
        for line in list(delta)[:100]:
            print(line)
        return 1
    except (OSError, ValueError) as error:
        print(f'OpenAPI snapshot refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    # Direct operator invocation finds this checkout without altering installed
    # or frozen consumers. Imported summarizer needs no path mutation.
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
