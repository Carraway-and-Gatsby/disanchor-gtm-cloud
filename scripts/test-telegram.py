#!/usr/bin/env python3
"""Run an explicitly bounded local Telegram-only test, without a Cloud instance."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtm.telegram_test import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(json.dumps({'status': 'interrupted_listener_stopped', 'social_posts_sent': 0}))
        raise SystemExit(130)
    except Exception:
        # Bot credentials appear in transport URLs. Never emit tracebacks or raw errors.
        print(json.dumps({'status': 'local_telegram_test_failed_details_omitted', 'social_posts_sent': 0}))
        raise SystemExit(1)
