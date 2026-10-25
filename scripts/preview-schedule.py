#!/usr/bin/env python3
"""Print explicit account/UTC/ET/PT/KST slots; no state, credentials or API calls."""
import argparse
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtm.config import Config, timestamp, slots

parser = argparse.ArgumentParser()
parser.add_argument('--config', default='config.toml')
parser.add_argument('--start', required=True, help='Intended start with explicit UTC offset')
args = parser.parse_args()
c = Config.load(args.config)
result = []
for slot in slots(c, timestamp(args.start)):
    when = timestamp(slot['scheduled_at'])
    result.append({**slot, 'account': getattr(c.accounts, slot['platform'] + '_account'),
                   **{name: when.astimezone(ZoneInfo(zone)).isoformat() for name, zone in (
                       ('ET', 'America/New_York'), ('PT', 'America/Los_Angeles'), ('KST', 'Asia/Seoul'))}})
print(json.dumps({'mode': c.schedule.mode, 'preview_only': True, 'scheduled_posts_created': 0,
                  'auto_publish': c.publishing.auto_publish, 'budget_usd': c.budget.target_total_usd,
                  'slots': result}, ensure_ascii=False, indent=2))
