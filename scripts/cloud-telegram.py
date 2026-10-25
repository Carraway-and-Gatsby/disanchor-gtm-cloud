#!/usr/bin/env python3
"""Restricted staging receiver; token values never appear in diagnostics."""
import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--deadline', required=True, help='Fixed UTC deadline, at most 24h from first run')
args = parser.parse_args()
sys.path.insert(0, str(args.root.resolve()))

if __name__ == '__main__':
    try:
        from gtm.cloud_telegram import run
        result = run(args.root, args.deadline)
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print(json.dumps({'status': 'cloud_telegram_failed_details_omitted', 'api_publishing': 'OFF'}))
        raise SystemExit(1)
