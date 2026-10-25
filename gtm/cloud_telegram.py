"""Bounded owner-only Cloud staging queries. Never runs the Hermes agent."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import stat
import time

from .config import Config, UTC, iso, timestamp
from .engine import Engine
from .environment import read_private_environment
from .safety import publication_lock
from .store import Store, process_lock
from .telegram import TelegramClient, TelegramMonitor, _http
from .telegram_setup import SetupAPI
from .telegram_test import locked_defaults

COMMANDS = ('/status', '/mcp', '/excluded', '/sentiment')
PREFIX = 'Cloud staging · 실제 저장 기록 · API 게시 OFF · LinkedIn 차단 · 예산 $20\n'


class QueryMonitor(TelegramMonitor):
    def _notifications(self, now, daily=False):
        # A query service has no unsolicited daily or transition notifications.
        return self._snapshot(now)

    def _command(self, update, snap):
        message = update.get('message')
        text = message.get('text') if isinstance(message, dict) else None
        if text not in COMMANDS:
            return None
        started = self._get('cloud_started_at')
        if started and (type(message.get('date')) is not int
                        or message['date'] < timestamp(started).timestamp() - 5):
            return None
        reply = super()._command(update, snap)  # Exact private owner authentication.
        if reply is None:
            return None
        self._set('cloud_command:' + str(update['update_id']), text)
        return PREFIX + reply


class BoundedTransport:
    def __init__(self, token, owner, monitor, deadline, transport=_http):
        self.prefix = 'https://api.telegram.org/bot' + token + '/'
        self.owner, self.monitor, self.deadline = owner, monitor, deadline
        self.transport, self.calls, self.sent = transport, 0, 0

    def __call__(self, method, url, headers, body):
        now = datetime.now(UTC)
        if now >= self.deadline or method != 'POST' or url not in {
            self.prefix + name for name in ('getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage')
        } or self.calls >= 80:
            raise ValueError('cloud_telegram_transport_blocked')
        payload = json.loads(body)
        if url.endswith('/sendMessage'):
            hour = now.strftime('%Y-%m-%dT%H')
            total = self.monitor._get('cloud_send_attempts', 0)
            hourly = self.monitor._get('cloud_hour:' + hour, 0)
            if (payload.get('chat_id') != self.owner or 'allow_paid_broadcast' in payload
                    or self.sent >= 8 or total >= 200 or hourly >= 30):
                raise ValueError('cloud_telegram_delivery_blocked')
            # Charge attempts before I/O, including ambiguous failures.
            self.monitor._set('cloud_send_attempts', total + 1)
            self.monitor._set('cloud_hour:' + hour, hourly + 1)
            self.sent += 1
        self.calls += 1
        return self.transport(method, url, headers, body)


def guard(engine, now, deadline):
    locked_defaults(engine.c)
    if deadline.tzinfo is None or not now < deadline <= now + timedelta(hours=24):
        raise ValueError('cloud_telegram_expired_or_invalid_deadline')
    exp = engine.experiment()
    if exp['status'] != 'NEW' or exp['start_at'] or exp['authorization_hash']:
        raise ValueError('cloud_telegram_requires_unstarted_staging')


def campaign_state(store):
    # Every campaign table is covered; Telegram state/outbox are the only allowed writes.
    tables = [row['name'] for row in store.rows("SELECT name FROM sqlite_master WHERE type='table'")
              if row['name'] not in {'telegram_state', 'telegram_outbox', 'sqlite_sequence'}]
    return {name: store.rows('SELECT * FROM "' + name + '" ORDER BY rowid')
            for name in tables}


def audit(monitor, now, deadline, username, before):
    if campaign_state(monitor.s) != before:
        raise ValueError('cloud_telegram_unexpected_campaign_mutation')
    received, confirmed = {}, {}
    for row in monitor.s.rows("SELECT key,value FROM telegram_state WHERE key LIKE 'cloud_command:%'"):
        command = json.loads(row['value'])
        if command not in COMMANDS:
            continue
        received[command] = received.get(command, 0) + 1
        uid = row['key'].split(':', 1)[1]
        sent = monitor.s.one('SELECT state FROM telegram_outbox WHERE event_key=?', ('command:' + uid,))
        if sent and sent['state'] == 'SENT':
            confirmed[command] = confirmed.get(command, 0) + 1
    return {'status': 'query_receiver_ready', 'observed_at': iso(now), 'expires_at': iso(deadline),
            'bot_username': username, 'commands_received': received, 'replies_api_confirmed': confirmed,
            'campaign_unchanged': True, 'data_source': 'actual_staging_only',
            'synthetic_data_imported': False, 'api_publishing': 'OFF', 'linkedin': 'BLOCKED',
            'budget_usd': 20, 'model_calls': 0, 'social_posts_sent': 0,
            'send_attempts': monitor._get('cloud_send_attempts', 0),
            'telegram_delivery_states': monitor.s.rows('SELECT state,count(*) n FROM telegram_outbox GROUP BY state')}


def write_report(path, report):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def run(root, deadline, *, seconds=40):
    root = Path(root).resolve()
    deadline = timestamp(deadline)
    os.umask(0o077)
    if not 1 <= seconds <= 45 or not (root / 'state/gtm.sqlite').is_file():
        raise ValueError('cloud_telegram_invalid_workspace')
    now = datetime.now(UTC)
    if now >= deadline:
        return {'status': 'expired_no_network', 'api_publishing': 'OFF'}
    for directory in (root, root / 'state', root / 'reports'):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('cloud_telegram_private_workspace_required')
    # This is a dedicated, bounded worker process. Registry and learning paths in
    # the deployment config are relative to its application root, not Hermes home.
    os.chdir(root)
    cfg = Config.load(root / 'config.toml')
    locked_defaults(cfg)
    values = read_private_environment(root / 'state/telegram-owner.env')
    if set(values) != {'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_USER_ID'}:
        raise ValueError('cloud_telegram_unexpected_credentials')
    with publication_lock(True), process_lock(root / 'state/gtm.sqlite'):
        store = Store(root / 'state/gtm.sqlite')
        try:
            engine = Engine(cfg, store)  # No publisher/model/collector/scheduler.
            guard(engine, now, deadline)
            before = campaign_state(store)
            client = TelegramClient(values['TELEGRAM_BOT_TOKEN'], values['TELEGRAM_CHAT_ID'], values['TELEGRAM_USER_ID'])
            monitor = QueryMonitor(engine, client, enabled=True)
            first = monitor._get('cloud_started_at')
            if first and deadline > timestamp(first) + timedelta(hours=24):
                raise ValueError('cloud_telegram_deadline_extension_blocked')
            transport = BoundedTransport(values['TELEGRAM_BOT_TOKEN'], client.chat_id, monitor, deadline)
            client._transport = transport
            username = SetupAPI(values['TELEGRAM_BOT_TOKEN'], transport).inspect()
            del values
            if username != 'disanchor_gtm_bot':
                raise ValueError('cloud_telegram_unexpected_bot')
            if first is None:
                monitor._set('cloud_started_at', iso(now))
                monitor._queue('cloud-connected', PREFIX + '조회 전용 연결 완료.\n/status /mcp /excluded /sentiment\n'
                               + '신규 수집·학습·모델·SNS 게시를 실행하지 않습니다.\n수신 종료: ' + iso(deadline), now, 8)
            end = time.monotonic() + seconds
            report = audit(monitor, now, deadline, username, before)
            while time.monotonic() < end and datetime.now(UTC) < deadline:
                tick_now = datetime.now(UTC)
                guard(engine, tick_now, deadline)
                tick = monitor.poll_and_notify(tick_now)
                report = audit(monitor, tick_now, deadline, username, before)
                report['last_poll_status'] = tick['status']
                write_report(root / 'reports/cloud-telegram-status.json', report)
                if tick['status'] not in {'sent', 'idle', 'cooldown'}:
                    break
                time.sleep(1)
            return report
        finally:
            store.close()
