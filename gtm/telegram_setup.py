"""Interactive private credential setup; never activates the worker or sends messages."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
import warnings

from .environment import ALLOWED, read_private_environment
from .telegram import TelegramError, _http, _positive_id

FIELDS = ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_USER_ID')
TOKEN = re.compile(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}\Z')


class SetupAPI:
    """Only read methods, fixed official origin, bounded transport, no retries."""
    def __init__(self, token, transport=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('invalid_telegram_credential')
        self.token, self.transport = token, transport or _http
        self.calls = 0

    def call(self, method):
        if method not in {'getMe', 'getWebhookInfo', 'getUpdates'} or self.calls >= 5:
            raise ValueError('setup_method_or_call_limit')
        # Omit offset and allowed_updates: do not acknowledge queued messages or
        # change the bot's update subscription while pairing.
        payload = {'limit': 100, 'timeout': 0} if method == 'getUpdates' else {}
        self.calls += 1
        try:
            response = self.transport('POST', f'https://api.telegram.org/bot{self.token}/{method}',
                                      {'Content-Type': 'application/json'}, json.dumps(payload).encode())
            body = response.get('body')
            if response.get('status_code') != 200 or not isinstance(body, dict) or body.get('ok') is not True:
                raise TelegramError('telegram_rejected')
            return body['result']
        except Exception:
            # Requests include the credential in the URL; never propagate errors.
            raise TelegramError('telegram_rejected') from None

    def inspect(self):
        me, webhook = self.call('getMe'), self.call('getWebhookInfo')
        if (not isinstance(me, dict) or me.get('is_bot') is not True
                or not isinstance(me.get('username'), str)
                or not re.fullmatch(r'[A-Za-z0-9_]{5,64}', me['username'])
                or not isinstance(webhook, dict) or not isinstance(webhook.get('url'), str)):
            raise ValueError('invalid_setup_response')
        if webhook['url']:
            raise ValueError('existing_webhook_not_changed')
        return me['username']


def paired_owner(updates, challenge):
    if not isinstance(updates, list) or len(updates) > 100:
        raise ValueError('invalid_pairing_response')
    owners = set()
    for update in updates:
        message = update.get('message') if isinstance(update, dict) else None
        if not isinstance(message, dict) or message.get('text') != '/start ' + challenge:
            continue
        sender, chat = message.get('from'), message.get('chat')
        if (not isinstance(sender, dict) or sender.get('is_bot') is not False
                or not isinstance(chat, dict) or chat.get('type') != 'private'
                or any(k in message for k in ('forward_origin', 'forward_from', 'forward_from_chat', 'sender_chat'))):
            continue
        try:
            user_id, chat_id = _positive_id(sender.get('id')), _positive_id(chat.get('id'))
        except ValueError:
            continue
        if user_id == chat_id:
            owners.add(str(user_id))
    if len(owners) > 1:
        raise ValueError('ambiguous_pairing_owner')
    return next(iter(owners), None)


def existing_values(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('private_path_symlink')
    return read_private_environment(path) if path.exists() else {}


def save_credentials(path, values, expected):
    path = Path(path)
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700)
    info = parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('private_directory_requires_700')
    if set(values) != set(FIELDS) or not TOKEN.fullmatch(values[FIELDS[0]]):
        raise ValueError('invalid_setup_credentials')
    if _positive_id(values[FIELDS[1]]) != _positive_id(values[FIELDS[2]]):
        raise ValueError('private_owner_mismatch')
    if existing_values(path) != expected:
        raise ValueError('private_file_changed_retry')
    if set(expected) - ALLOWED:
        raise ValueError('invalid_existing_fields')
    merged = {**expected, **values}
    fd, temporary = tempfile.mkstemp(prefix='.telegram-', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write('# Private runtime credentials; never commit or shell-source.\n')
            for name, value in merged.items():
                handle.write(name + '=' + value + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None, *, root=None):
    parser = argparse.ArgumentParser(description='Private Telegram setup; publishing remains OFF')
    parser.add_argument('mode', choices=('configure', 'check'))
    args = parser.parse_args(argv)
    root = Path(root or Path(__file__).resolve().parents[1])
    path = root / 'state/runtime.env'
    from .config import Config
    cfg = Config.load(root / 'config.toml')
    if (cfg.publishing.auto_publish or not cfg.publishing.dry_run or cfg.publishing.account_remaining_quota != 0
            or cfg.routing.enabled or cfg.model.enabled or cfg.telegram.enabled
            or cfg.budget.target_total_usd != 20 or cfg.platforms.enabled != ['x', 'threads']):
        raise ValueError('setup_requires_locked_defaults')
    prior = existing_values(path)
    if args.mode == 'check':
        if not all(prior.get(k) for k in FIELDS):
            print(json.dumps({'status': 'credentials_pending', 'credential_values_shown': False,
                              'publishing': 'OFF', 'budget_usd': 20}))
            return 2
        if _positive_id(prior[FIELDS[1]]) != _positive_id(prior[FIELDS[2]]):
            raise ValueError('private_owner_mismatch')
        username = SetupAPI(prior[FIELDS[0]]).inspect()
        print(json.dumps({'status': 'bot_identity_verified_not_delivery_tested', 'bot_username': username,
                          'webhook_present': False, 'private_owner_configured': True,
                          'publishing': 'OFF', 'budget_usd': 20, 'telegram_enabled': False,
                          'network_calls': 2, 'messages_sent': 0}))
        return 0
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('interactive_terminal_required')
    if input('다른 프로그램에서 쓰지 않는 전용 봇인가요? yes 입력: ').strip() != 'yes':
        raise ValueError('dedicated_bot_confirmation_required')
    if any(prior.get(k) for k in FIELDS):
        if input('기존 Telegram 설정을 교체하려면 replace 입력: ').strip() != 'replace':
            raise ValueError('existing_telegram_settings_preserved')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        token = getpass.getpass('BotFather 토큰 (입력 내용은 보이지 않습니다): ')
    api = SetupAPI(token)
    username = api.inspect()
    challenge = 'gtm_' + secrets.token_urlsafe(24)
    expires = time.monotonic() + 600
    print(f'휴대폰 Telegram에서 @{username} 개인 대화를 여세요. 다음 문구를 직접 보내세요:')
    print('/start ' + challenge)
    print('이 문구는 본인 확인용입니다. 다른 사람에게 공유하지 마세요. 봇의 자동 응답은 아직 없습니다.')
    owner = None
    for _ in range(3):
        input('전송한 뒤 Enter (최대 3회 확인, 10분 안에 완료): ')
        if time.monotonic() > expires:
            raise ValueError('pairing_expired_retry')
        owner = paired_owner(api.call('getUpdates'), challenge)
        if owner:
            break
        print('일치하는 개인 메시지가 없습니다. 문구와 봇을 확인하세요.')
    if not owner:
        raise ValueError('pairing_message_missing')
    if input('방금 본인 Telegram 개인 대화에서 직접 보냈다면 yes 입력: ').strip() != 'yes':
        raise ValueError('owner_confirmation_required')
    save_credentials(path, dict(zip(FIELDS, (token, owner, owner))), prior)
    print('비공개 state/runtime.env에 저장했습니다(600). 기존 다른 키의 값은 보존했습니다.')
    print('Cloud·자동 조회·메시지 전송·게시·모델을 활성화하지 않았습니다. 이 채팅에는 완료 여부만 알려주세요.')
    return 0
