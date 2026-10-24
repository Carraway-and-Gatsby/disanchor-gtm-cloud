#!/usr/bin/env python3
"""Create a reproducible source-only Cloud ZIP, excluding runtime state/secrets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True


_spec = importlib.util.spec_from_file_location("cloud_preflight", Path(__file__).with_name("cloud-preflight.py"))
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)


def build_bundle(root, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.suffix != ".zip":
        raise checks.ValidationError("bundle_output_must_end_in_zip")
    checks.preflight(root)
    entries = checks.source_entries(root)
    manifest = {"format": "disanchor-source-bundle-v1", "runtime_state_included": False,
                "credentials_verified": False, "files": [
                    {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in sorted(entries.items())]}
    entries["bundle-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".cloud-bundle-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, data)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {"status": "source_bundle_created", "output": str(output), "source_files": len(manifest["files"]),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "credentials_verified": False, "cloud_deployed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("reports/disanchor-cloud.zip"))
    args = parser.parse_args(argv)
    try:
        result = build_bundle(args.root, args.output)
    except (checks.ValidationError, OSError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error) if isinstance(error, checks.ValidationError) else "bundle_write_failed"}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
