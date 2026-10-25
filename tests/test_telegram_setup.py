import json
import os
from pathlib import Path
import tempfile
import unittest

from gtm.environment import read_private_environment
from gtm.telegram import TelegramError
from gtm.telegram_setup import SetupAPI, existing_values, paired_owner, save_credentials, FIELDS

TOKEN = '123456:offline-telegram-fixture-credential'


def update(owner=123, text='/start fixture_challenge', **extra):
    return {'message': {'text': text, 'from': {'id': owner, 'is_bot': False},
                        'chat': {'id': owner, 'type': 'private'}, **extra}}


class TelegramSetupTest(unittest.TestCase):
    def test_pairing_requires_exact_nonce_and_private_same_owner(self):
        good = update()
        variants = [update(text='/start wrong'), update(forward_origin={}), update(sender_chat={}),
                    update(forward_from={})]
        group = update(); group['message']['chat']['type'] = 'group'
        mismatch = update(); mismatch['message']['chat']['id'] = 999
        bot = update(); bot['message']['from']['is_bot'] = True
        for item in variants + [group, mismatch, bot]:
            self.assertIsNone(paired_owner([item], 'fixture_challenge'))
        self.assertEqual(paired_owner([good, good], 'fixture_challenge'), '123')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            paired_owner([good, update(456)], 'fixture_challenge')

    def test_only_read_methods_and_no_offset_or_subscription_change(self):
        calls = []
        def transport(method, url, headers, body):
            calls.append((method, url, json.loads(body)))
            result = {'is_bot': True, 'username': 'offline_fixture_bot'} if url.endswith('/getMe') else {'url': ''} if url.endswith('/getWebhookInfo') else []
            return {'status_code': 200, 'body': {'ok': True, 'result': result}}
        api = SetupAPI(TOKEN, transport)
        self.assertEqual(api.inspect(), 'offline_fixture_bot')
        api.call('getUpdates')
        self.assertEqual(calls[-1][2], {'limit': 100, 'timeout': 0})
        for method in ('sendMessage', 'deleteWebhook', 'setWebhook', 'logOut'):
            with self.assertRaises(ValueError): api.call(method)
        self.assertEqual(len(calls), 3)
        api.call('getUpdates'); api.call('getUpdates')
        with self.assertRaises(ValueError): api.call('getUpdates')

    def test_webhook_and_transport_failures_do_not_retry_or_leak(self):
        calls = []
        def fail(*args):
            calls.append(1)
            raise RuntimeError('https://api.telegram.org/bot' + TOKEN)
        with self.assertRaises(TelegramError) as error:
            SetupAPI(TOKEN, fail).inspect()
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertEqual(len(calls), 1)
        def hooked(method, url, headers, body):
            result = {'is_bot': True, 'username': 'offline_fixture_bot'} if url.endswith('/getMe') else {'url': 'https://offline.invalid'}
            return {'status_code': 200, 'body': {'ok': True, 'result': result}}
        with self.assertRaisesRegex(ValueError, 'webhook'):
            SetupAPI(TOKEN, hooked).inspect()

    def test_atomic_private_save_preserves_other_credentials_and_detects_change(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'state'; parent.mkdir(mode=0o700)
            path = parent / 'runtime.env'
            path.write_text('UPLOAD_POST_API_KEY=offline-existing-fixture\n')
            path.chmod(0o600)
            old = existing_values(path)
            values = dict(zip(FIELDS, (TOKEN, '123', '123')))
            save_credentials(path, values, old)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_private_environment(path), {**old, **values})
            self.assertEqual(list(parent.iterdir()), [path])
            with self.assertRaisesRegex(ValueError, 'changed'):
                save_credentials(path, values, old)
            self.assertEqual(read_private_environment(path), {**old, **values})

    def test_symlinks_and_public_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / 'outside'; target.write_text('outside')
            link = parent / 'runtime.env'; link.symlink_to(target)
            with self.assertRaises(ValueError): existing_values(link)
            with self.assertRaises(ValueError):
                save_credentials(link, dict(zip(FIELDS, (TOKEN, '123', '123'))), {})
            self.assertEqual(target.read_text(), 'outside')
            link.unlink(); parent.chmod(0o755)
            with self.assertRaisesRegex(ValueError, '700'):
                save_credentials(link, dict(zip(FIELDS, (TOKEN, '123', '123'))), {})
            self.assertFalse(link.exists())

    def test_invalid_owner_and_token_cannot_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.env'
            with self.assertRaises(ValueError):
                save_credentials(path, dict(zip(FIELDS, (TOKEN, '123', '456'))), {})
            with self.assertRaises(ValueError):
                save_credentials(path, dict(zip(FIELDS, ('invalid', '123', '123'))), {})
            self.assertFalse(path.exists())
