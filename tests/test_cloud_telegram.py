from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gtm.cloud_telegram import QueryMonitor, BoundedTransport, guard, audit, campaign_state, run, COMMANDS
from gtm.config import UTC, iso
from tests import test_telegram as fixture
from tests.test_telegram import TOKEN, update


class CloudQueryTests(unittest.TestCase):
    setUp = fixture.TelegramMonitorTests.setUp

    def test_four_commands_auth_and_immutable_campaign(self):
        monitor = QueryMonitor(self.e, self.client, enabled=True)
        before = campaign_state(self.s)
        self.t.updates = [update(i + 1, command) for i, command in enumerate(COMMANDS)]
        with patch.object(self.e, 'collect', side_effect=AssertionError('collect')), \
             patch.object(self.e, 'learn', side_effect=AssertionError('learn')):
            for i in range(4):
                monitor.poll_and_notify(self.now + timedelta(seconds=2 * i))
        self.assertEqual(len(self.t.sent()), 4)
        self.assertTrue(all(x['text'].startswith('Cloud staging') for x in self.t.sent()))
        result = audit(monitor, self.now, self.now + timedelta(hours=1), 'disanchor_gtm_bot', before)
        self.assertEqual(set(result['replies_api_confirmed']), set(COMMANDS))
        self.assertEqual(before, campaign_state(self.s))
        for command in COMMANDS:
            self.assertIsNone(monitor._command(update(10, command, sender=99), monitor._snapshot(self.now)))
        self.assertIsNone(monitor._command(update(11, '/collect'), monitor._snapshot(self.now)))
        self.assertIsNone(monitor._command(update(12, '/drafts'), monitor._snapshot(self.now)))

    def test_old_queued_commands_do_not_count_as_cloud_test(self):
        monitor = QueryMonitor(self.e, self.client, enabled=True)
        monitor._set('cloud_started_at', iso(self.now))
        message = update(1, '/status')
        message['message']['date'] = int(self.now.timestamp()) - 60
        self.assertIsNone(monitor._command(message, monitor._snapshot(self.now)))
        message['message']['date'] += 60
        self.assertTrue(monitor._command(message, monitor._snapshot(self.now)))

    def test_guard_blocks_campaign_and_invalid_deadline(self):
        # The fixture is a started campaign; Cloud staging must reject it.
        with self.assertRaises(ValueError): guard(self.e, self.now, self.now + timedelta(hours=1))
        with patch('gtm.cloud_telegram.locked_defaults'):
            for deadline in (self.now, self.now + timedelta(hours=25)):
                with self.assertRaises(ValueError): guard(self.e, self.now, deadline)
            self.s.db.execute("UPDATE experiments SET status='RUNNING'")
            with self.assertRaises(ValueError): guard(self.e, self.now, self.now + timedelta(hours=1))

    def test_transport_origin_methods_owner_budget_and_expiry(self):
        monitor = QueryMonitor(self.e, self.client, enabled=True)
        t = BoundedTransport(TOKEN, 42, monitor, datetime.now(UTC) + timedelta(hours=1), self.t)
        prefix = 'https://api.telegram.org/bot' + TOKEN + '/'
        for method, url, payload in [('GET', prefix + 'getUpdates', {}),
                                     ('POST', prefix + 'deleteWebhook', {}),
                                     ('POST', 'https://evil.example/getMe', {}),
                                     ('POST', prefix + 'sendMessage', {'chat_id': 99})]:
            with self.assertRaises(ValueError): t(method, url, {}, json.dumps(payload).encode())
        monitor._set('cloud_send_attempts', 200)
        with self.assertRaises(ValueError): t('POST', prefix + 'sendMessage', {}, b'{"chat_id":42}')
        t.deadline = datetime.now(UTC) - timedelta(seconds=1)
        with self.assertRaises(ValueError): t('POST', prefix + 'getMe', {}, b'{}')
        self.assertEqual(self.t.calls, [])

    def test_audit_detects_campaign_value_change_and_no_auto_notices(self):
        monitor = QueryMonitor(self.e, self.client, enabled=True)
        before = campaign_state(self.s)
        monitor.notify(self.now)
        self.assertEqual(self.t.sent(), [])
        self.s.db.execute("UPDATE experiments SET status='PAUSED'")
        with self.assertRaises(ValueError): audit(monitor, self.now, self.now, 'bot', before)

    def test_worker_resolves_app_paths_from_hermes_home_and_persists_only_safe_report(self):
        from gtm.config import Config
        from gtm.engine import Engine
        from gtm.store import Store
        source = Path(__file__).resolve().parents[1]
        cwd, mask = Path.cwd(), os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in ('state', 'reports'): (root / name).mkdir(mode=0o700)
                for name in ('config.toml', 'accounts.example.toml'):
                    shutil.copyfile(source / name, root / name)
                shutil.copytree(source / 'prompts', root / 'prompts')
                os.chdir(root)
                store = Store(root / 'state/gtm.sqlite')
                Engine(Config.load(root / 'config.toml'), store).init(datetime.now(UTC))
                store.close()
                secret = root / 'state/telegram-owner.env'
                secret.write_text('TELEGRAM_BOT_TOKEN=' + TOKEN + '\nTELEGRAM_CHAT_ID=42\nTELEGRAM_USER_ID=42\n')
                secret.chmod(0o600)
                calls = []
                def wire(method, url, headers, body):
                    name = url.rsplit('/', 1)[-1]
                    calls.append(name)
                    result = {'getMe': {'is_bot': True, 'username': 'disanchor_gtm_bot'},
                              'getWebhookInfo': {'url': ''}, 'getUpdates': [],
                              'sendMessage': {'message_id': 1}}[name]
                    return {'status_code': 200, 'body': {'ok': True, 'result': result}}
                def bounded(*args): return BoundedTransport(*args, transport=wire)
                os.chdir(source)  # Deliberately different from the deployment root.
                with patch('gtm.cloud_telegram.BoundedTransport', side_effect=bounded):
                    result = run(root, datetime.now(UTC) + timedelta(hours=1), seconds=1)
                self.assertTrue(result['campaign_unchanged'])
                self.assertIn('sendMessage', calls)
                raw = (root / 'reports/cloud-telegram-status.json').read_text()
                self.assertNotIn(TOKEN, raw)
                self.assertEqual(result['data_source'], 'actual_staging_only')
                self.assertEqual((root / 'reports/cloud-telegram-status.json').stat().st_mode & 0o777, 0o600)
                self.assertFalse((root / 'learning').exists())
        finally:
            os.chdir(cwd)
            os.umask(mask)
