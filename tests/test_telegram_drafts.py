from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gtm.config import Config
from gtm.review_drafts import ACCOUNTS, draft_review
from gtm.telegram_test import TestTransport, locked_defaults
from tests import test_telegram as fixtures


class DraftDocumentTests(unittest.TestCase):
    def test_all_seven_accounts_show_two_reviewed_drafts_in_the_correct_language(self):
        for key, (label, status, language, _) in ACCOUNTS.items():
            with self.subTest(key=key):
                text = draft_review('/drafts ' + key)
                self.assertIn(label, text)
                self.assertIn(status, text)
                self.assertIn('1. ', text)
                self.assertIn('2. ', text)
                self.assertIn('API 게시 OFF', text)
                self.assertLessEqual(len(text), 4096)
                if language == 'ko':
                    self.assertIn('도구를 쓰는 데는 10분', text)
                    self.assertNotIn('A tool may', text)

    def test_menu_and_exact_commands_only(self):
        menu = draft_review('/drafts')
        self.assertIn('LinkedIn', menu)
        self.assertIn('게시 차단', menu)
        for text in ('/drafts ../state/runtime.env', '/drafts personal_threads; publish',
                     '/drafts personal_threads\n/status', '/drafts@otherbot', '/drafts unknown', '/approve'):
            self.assertIsNone(draft_review(text))

    def test_changed_document_or_symlink_never_sends_unreviewed_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs').mkdir()
            path = root / 'docs/account-drafts.en-ko.md'
            path.write_text('DO_NOT_SEND_PRIVATE_CONTENT')
            text = draft_review('/drafts default_threads', root=root)
            self.assertIn('검증이 필요', text)
            self.assertNotIn('DO_NOT_SEND', text)
            path.unlink()
            path.symlink_to(Path(__file__).resolve().parents[1] / 'docs/account-drafts.en-ko.md')
            self.assertIn('검증이 필요', draft_review('/drafts default_threads', root=root))

    def test_local_test_rejects_changed_safety_defaults(self):
        locked_defaults(Config())
        for field, value in (('auto_publish', True), ('dry_run', False), ('account_remaining_quota', 1)):
            c = Config()
            setattr(c.publishing, field, value)
            with self.assertRaises(ValueError): locked_defaults(c)
        c = Config()
        c.budget.target_total_usd = 21
        with self.assertRaises(ValueError): locked_defaults(c)

    def test_local_transport_never_calls_social_origins_or_paid_broadcast(self):
        transport = TestTransport('fixture-not-real', 42)
        for url, body in (('https://api.upload-post.com/api/upload_text', b'{}'),
                          (transport.prefix + 'setWebhook', b'{}'),
                          (transport.prefix + 'sendMessage', b'{"chat_id":43}'),
                          (transport.prefix + 'sendMessage', b'{"chat_id":42,"allow_paid_broadcast":true}')):
            with patch('gtm.telegram_test._http', side_effect=AssertionError('network forbidden')):
                with self.assertRaises(ValueError): transport('POST', url, {}, body)


class TelegramDraftCommandTests(unittest.TestCase):
    setUp = fixtures.TelegramMonitorTests.setUp
    baseline = fixtures.TelegramMonitorTests.baseline

    def test_draft_query_is_owner_only_and_never_changes_the_experiment_or_calls_a_model(self):
        snap = self.m._snapshot(self.now)
        before = self.s.db.total_changes
        with patch.object(self.e, 'draft', side_effect=AssertionError('draft mutation')), \
             patch.object(self.e, 'approve', side_effect=AssertionError('approval mutation')), \
             patch.object(self.e, 'schedule', side_effect=AssertionError('publishing mutation')), \
             patch('gtm.routing.HermesStageRunner.run', side_effect=AssertionError('model invocation')):
            self.assertIn('운영·재연결 보류', self.m._command(fixtures.update(1, '/drafts personal_threads'), snap))
            self.assertIn('한국어', self.m._command(fixtures.update(2, '/drafts personal_threads'), snap))
            self.assertIn('운영 후보', self.m._command(fixtures.update(3, '/drafts default_threads'), snap))
            self.assertIsNone(self.m._command(fixtures.update(4, '/drafts personal_threads', sender=99), snap))
            self.assertIsNone(self.m._command(fixtures.update(5, '/drafts personal_threads', chat=-1, chat_type='group'), snap))
        self.assertEqual(self.s.db.total_changes, before)
        self.assertEqual(self.t.calls, [])

    def test_draft_command_uses_existing_outbox_deduplication(self):
        self.baseline()
        self.t.updates = [fixtures.update(20, '/drafts personal_threads')]
        from datetime import timedelta
        self.m.poll_and_notify(self.now + timedelta(seconds=2))
        self.m.poll_and_notify(self.now + timedelta(seconds=4))
        self.assertEqual(len(self.t.sent()), 1)
        self.assertIn('한국어', self.t.sent()[0]['text'])
        self.assertEqual(self.s.rows('SELECT * FROM publishing_jobs'), [])
