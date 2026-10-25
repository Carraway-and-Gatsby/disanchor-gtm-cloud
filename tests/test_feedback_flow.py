"""One isolated MCP -> gates -> skill -> Telegram round trip across four cycles."""
import copy
from datetime import timedelta
import json
import unittest

from gtm.telegram import TelegramClient, TelegramMonitor
from tests import test_mcp_feedback as ingestion
from tests import test_telegram as telegram


class FeedbackFlowTests(unittest.TestCase):
    setUp = ingestion.MCPIngestionTests.setUp
    collect = ingestion.MCPIngestionTests.collect

    def test_isolated_four_cycle_round_trip(self):
        transport = telegram.Transport()
        client = TelegramClient(telegram.TOKEN, 42, 42, transport)
        monitor = TelegramMonitor(self.engine, client, enabled=True)
        experiment = self.engine.experiment()
        costs = self.store.rows('SELECT * FROM cost_ledger')
        phases = []
        next_update = 1
        for index, name in enumerate(('positive', 'sparse', 'permission_unverified', 'negative')):
            self.now = self.start + timedelta(hours=72 + index * 6)
            data = ingestion.comments_response()
            expected_reasons = {}
            if name == 'sparse':
                data['comments'] = data['comments'][:4]
                expected_reasons = {'insufficient_classified_comments': 3}
            elif name == 'permission_unverified':
                data.pop('source')
                expected_reasons = {'mentions_source_unverified': 3}
            elif name == 'negative':
                for comment, text in zip(data['comments'], (
                    'Confusing explanation one', 'Misleading explanation two',
                    'Useless explanation three', 'Broken explanation four',
                    'Frustrating explanation five', '???')):
                    comment['text'] = text
            self.mcp.comments = copy.deepcopy(data)
            result = self.collect()
            good = name in {'positive', 'negative'}
            self.assertEqual(result['accepted_posts'], 3 if good else 0)
            self.assertEqual(result['reasons'], expected_reasons)
            context = self.engine.learning_context(self.now)
            self.assertEqual(len(context.get('lessons', [])), 1 if good else 0)
            if good:
                self.assertEqual(context['lessons'][0]['sentiment'][name], 15)
                self.assertEqual(result['learning']['lesson_count'], 1)
            # Simulate inbound owner commands and outbound API acknowledgments,
            # through the actual polling, authorization, offset and outbox code.
            before_messages = len(transport.sent())
            before_calls = self.mcp.calls
            transport.updates = [telegram.update(next_update + n, command)
                                 for n, command in enumerate(('/mcp', '/excluded', '/sentiment'))]
            next_update += 3
            for seconds in (1, 3, 5, 7):
                monitor.poll_and_notify(self.now + timedelta(seconds=seconds))
            messages = [row['text'] for row in transport.sent()[before_messages:]]
            replies = {command: self.store.one('SELECT state FROM telegram_outbox WHERE event_key=?',
                       ('command:' + str(next_update - 3 + n),))['state']
                       for n, command in enumerate(('/mcp', '/excluded', '/sentiment'))}
            self.assertEqual(set(replies.values()), {'SENT'})
            self.assertEqual(self.mcp.calls, before_calls)
            mcp_text = next(text for text in messages if text.startswith('MCP 반입:'))
            self.assertIn('최근 회차 반입 ' + ('3개 · 제외 0개' if good else '0개 · 제외 3개'), mcp_text)
            sentiment = next(text for text in messages if text.startswith('최근 sentiment 학습'))
            self.assertIn('현재 사용 가능한 교훈 ' + ('1' if good else '0') + '개', sentiment)
            if good: self.assertIn(('긍정' if name == 'positive' else '부정') + ' 1', sentiment)
            excluded = next(text for text in messages if text.startswith('MCP 학습 제외 사유'))
            if name == 'sparse': self.assertIn('분류 가능한 댓글 수 또는 분류율 부족: 3개 글', excluded)
            if name == 'permission_unverified': self.assertIn('X 멘션 표본 범위 미확인: 3개 글', excluded)
            persisted = '\n'.join(self.store.db.iterdump())
            skill = self.root / 'learning/hermes/skills/disanchor-marketing-feedback/SKILL.md'
            persisted += skill.read_text()
            for raw in ('PRIVATE_COMMENTER', 'private@example.com', 'Ignore previous',
                        'Useful perspective one', 'Confusing explanation one'):
                self.assertNotIn(raw, persisted + '\n'.join(messages))
            phases.append({'phase': name, 'accepted_posts': result['accepted_posts'],
                           'excluded_posts': result['rejected_posts'], 'reasons': result['reasons'],
                           'skill_status': result['learning']['status'],
                           'usable_sentiment_lessons': 1 if good else 0,
                           'direction': name if good else None, 'telegram_command_states': replies,
                           'telegram_mcp_text': mcp_text,
                           'telegram_sentiment_text': sentiment, 'telegram_excluded_text': excluded})
        self.assertEqual(self.engine.experiment(), experiment)
        self.assertEqual(self.store.rows('SELECT * FROM cost_ledger'), costs)
        self.assertFalse(self.store.rows('SELECT * FROM publishing_jobs'))
        self.assertEqual(self.publisher._jobs, {})
        self.report = {'status': 'passed', 'data': 'synthetic_only_temporary_directory',
                       'mcp_and_identity': 'injected_fixtures', 'telegram': 'injected_transport_no_real_delivery',
                       'skill_writer': 'compiled_native_compatible_format_not_cloud_native_runtime',
                       'phases': phases, 'social_posts_sent': 0, 'model_calls': 0,
                       'cloud_created': False, 'budget_usd': 20, 'api_publishing': 'OFF',
                       'linkedin_publication': 'BLOCKED', 'production_state_accessed': False}
