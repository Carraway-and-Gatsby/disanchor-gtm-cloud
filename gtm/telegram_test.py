"""Bounded local owner test. Does not enable Cloud, models or social publishing."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import time

from .config import Config, UTC
from .engine import Engine
from .environment import read_private_environment
from .review_drafts import ACCOUNTS, draft_review
from .safety import publication_lock
from .store import Store, process_lock
from .telegram import TelegramClient, TelegramMonitor, _http
from .telegram_setup import SetupAPI


def locked_defaults(c):
    if (c.publishing.auto_publish or not c.publishing.dry_run or c.publishing.account_remaining_quota != 0
            or c.telegram.enabled or c.model.enabled or c.routing.enabled or c.clone.enabled
            or c.budget.target_total_usd != 20 or c.platforms.enabled != ['x', 'threads']):
        raise ValueError('local_test_requires_locked_defaults')


class TestTransport:
    """No social/provider credentials, redirects or arbitrary API methods."""
    def __init__(self, token, owner):
        self.prefix = 'https://api.telegram.org/bot' + token + '/'
        self.owner, self.sent, self.calls = owner, 0, 0

    def __call__(self, method, url, headers, body):
        allowed = {self.prefix + name for name in ('getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage')}
        if method != 'POST' or url not in allowed or self.calls >= 150:
            raise ValueError('local_test_transport_limit')
        payload = json.loads(body)
        if url.endswith('/sendMessage'):
            if payload.get('chat_id') != self.owner or self.sent >= 8 or 'allow_paid_broadcast' in payload:
                raise ValueError('local_test_delivery_limit')
            self.sent += 1
        self.calls += 1
        return _http(method, url, headers, body)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Temporary local Telegram status/draft test; social posting locked OFF')
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 600:
        raise ValueError('test_duration_must_be_10_to_600_seconds')
    root = Path(__file__).resolve().parents[1]
    cfg = Config.load(root / 'config.toml')
    locked_defaults(cfg)
    # Do not export the credential file to the environment or construct a social client.
    values = read_private_environment(root / 'state/runtime.env')
    client = TelegramClient(values.get('TELEGRAM_BOT_TOKEN', ''), values.get('TELEGRAM_CHAT_ID', ''), values.get('TELEGRAM_USER_ID', ''))
    transport = TestTransport(values['TELEGRAM_BOT_TOKEN'], client.chat_id)
    client._transport = transport
    username = SetupAPI(values['TELEGRAM_BOT_TOKEN'], transport).inspect()
    del values
    path = root / 'state/gtm.sqlite'
    received, acknowledged = {}, set()
    result = {'status': 'waiting_for_owner_commands', 'bot_username': username,
              'cloud_created': False, 'social_posts_sent': 0, 'model_calls': 0,
              'budget_usd': 20, 'api_publishing': 'OFF', 'linkedin_publication': 'BLOCKED'}
    with publication_lock(True), process_lock(path):
        store = Store(path)
        try:
            engine = Engine(cfg, store)  # No publisher, feedback client, model or scheduler.
            initial = engine.experiment()
            if initial['status'] != 'NEW' or initial['start_at'] or initial['authorization_hash']:
                raise ValueError('test_requires_unstarted_unauthorized_experiment')
            monitor = TelegramMonitor(engine, client, enabled=True)
            if store.one("SELECT count(*) n FROM telegram_outbox WHERE state IN ('PENDING','RETRY')")['n']:
                raise ValueError('review_existing_pending_telegram_messages_first')
            before = {table: store.one(f'SELECT count(*) n FROM {table}')['n']
                      for table in ('post_drafts', 'publishing_jobs', 'cost_ledger')}
            original = monitor._command
            commands = {'/status', '/drafts', '/help', '/budget', '/posts', '/feedback'} | {'/drafts ' + key for key in ACCOUNTS}
            def record_command(update, snap):
                reply = original(update, snap)
                text = update.get('message', {}).get('text') if isinstance(update.get('message'), dict) else None
                if reply and text in commands:
                    received[update['update_id']] = text
                return reply
            monitor._command = record_command
            # Explicitly label the actual local state; no fake incoming command.
            now = datetime.now(UTC)
            session = str(time.time_ns())
            monitor._queue('local-test:' + session + ':status',
                           '로컬 연결 테스트 · Cloud 미생성 · LinkedIn 게시 차단\n' + monitor.preview(now), now, 8)
            monitor._queue('local-test:' + session + ':drafts', draft_review('/drafts default_threads'), now, 7)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline and transport.sent < 8:
                tick = monitor.poll_and_notify(datetime.now(UTC))
                for uid, command in received.items():
                    row = store.one('SELECT state FROM telegram_outbox WHERE event_key=?', ('command:' + str(uid),))
                    if row and row['state'] == 'SENT': acknowledged.add(command)
                required = {'/status', '/drafts default_threads', '/drafts personal_threads'}
                if required <= acknowledged:
                    result['status'] = 'owner_command_delivery_verified'
                    break
                if tick['status'] in {'delivery_unknown', 'rejected', 'telegram_rejected', 'telegram_protocol_unknown', 'telegram_transport_unknown', 'rate_limited', 'telegram_rate_limited'}:
                    result['status'] = tick['status']
                    break
                time.sleep(2)
            if result['status'] == 'waiting_for_owner_commands':
                result['status'] = 'bounded_test_finished_owner_commands_incomplete'
            after = {table: store.one(f'SELECT count(*) n FROM {table}')['n'] for table in before}
            if before != after or engine.experiment() != initial:
                raise ValueError('unexpected_experiment_mutation')
            result.update({'owner_commands_received': sorted(set(received.values())),
                           'owner_command_replies_confirmed': sorted(acknowledged),
                           'telegram_delivery_states': store.rows('SELECT state,count(*) n FROM telegram_outbox GROUP BY state'),
                           'telegram_api_calls': transport.calls, 'experiment_unchanged': True,
                           'persistent_telegram_enabled': cfg.telegram.enabled, 'listener_running': False})
            report = root / 'state/telegram-test-result.json'
            fd = os.open(report, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result['status'] == 'owner_command_delivery_verified' else 2
        finally:
            store.close()
