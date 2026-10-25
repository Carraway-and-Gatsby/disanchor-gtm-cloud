#!/usr/bin/env python3
"""Run from a private terminal; never pass tokens as arguments."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtm.telegram_setup import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        # No traceback can expose a token-bearing URL or private environment.
        print('Telegram 준비를 중단했습니다. 토큰·ID·provider 오류 원문은 출력하지 않습니다. '
              '전용 봇, 개인 메시지, webhook 충돌, 파일 권한을 확인하세요.', file=sys.stderr)
        raise SystemExit(2)
