"""Owner queries read persisted evidence only; no collection or skill writes."""
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gtm.mcp_status import record_result, read_status
from gtm.telegram import TelegramClient, TelegramMonitor
from tests import test_telegram as telegram
from tests import test_mcp_feedback as ingestion


class MCPMonitoringTests(unittest.TestCase):
    setUp=telegram.TelegramMonitorTests.setUp
    baseline=telegram.TelegramMonitorTests.baseline

    def test_three_queries_never_fetch_or_learn_or_create_skill_state(self):
        before=self.s.db.total_changes
        with patch.object(self.e,'collect',side_effect=AssertionError('collection')), \
             patch.object(self.e,'learn',side_effect=AssertionError('learning')), \
             patch('gtm.mcp_feedback.collect_mcp',side_effect=AssertionError('MCP import')), \
             patch('gtm.mcp_read.http',side_effect=AssertionError('MCP network')), \
             patch('gtm.telegram._http',side_effect=AssertionError('Telegram network')), \
             patch('gtm.learning.LearningLoop.update',side_effect=AssertionError('skill write')):
            snap=self.m._snapshot(self.now)
            for command in ('/mcp','/excluded','/sentiment'):
                text=self.m._command(telegram.update(1,command),snap)
                self.assertTrue(1<=len(text)<=4096)
            self.assertIn('반입 기록 없음',self.m._command(telegram.update(1,'/mcp'),snap))
            self.assertIn('교훈 없음',self.m._command(telegram.update(1,'/sentiment'),snap))
        self.assertEqual(self.s.db.total_changes,before)
        self.assertFalse((self.s.path.parent/'learning').exists())

    def test_audit_reasons_are_durable_closed_and_immutable(self):
        record_result(self.e,self.now,{'status':'collected','accepted_posts':0,'rejected_posts':3,
                                     'reasons':{'reply_permission_unverified':2,'insufficient_classified_comments':1},
                                     'mcp_calls':6,'identity_reads':1})
        restarted=TelegramMonitor(self.e,self.client,enabled=True)
        snap=restarted._snapshot(self.now)
        text=restarted._command(telegram.update(1,'/excluded'),snap)
        self.assertIn('Threads 답글 읽기 범위 미확인: 2개 글',text)
        self.assertIn('분류 가능한 댓글 수 또는 분류율 부족: 1개 글',text)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError): self.s.db.execute('DELETE FROM mcp_import_events')

    def test_unknown_provider_fields_never_echo_and_malformed_record_fails_closed(self):
        record_result(self.e,self.now,{'status':'PRIVATE_SECRET','reason':'PRIVATE_SECRET',
                                     'reasons':{'PRIVATE_PROVIDER_TEXT':1},'api_key':'test-secret-not-a-real-credential'})
        snap=self.m._snapshot(self.now)
        for command in ('/mcp','/excluded','/sentiment'):
            self.assertNotIn('PRIVATE_SECRET',self.m._command(telegram.update(1,command),snap))
        self.assertNotIn('PRIVATE_SECRET',json.dumps(self.s.records('mcp_import_events')))
        self.assertNotIn('PRIVATE_PROVIDER_TEXT',json.dumps(self.s.records('mcp_import_events')))
        self.s.record('mcp_import_events','bad',self.now,'fixture',
                      {'account_fingerprint':self.e.account_fingerprint(),'reason':'PRIVATE_SECRET'})
        snap=self.m._snapshot(self.now)
        self.assertIn('상태 확인 필요',self.m._command(telegram.update(1,'/mcp'),snap))
        self.assertNotIn('PRIVATE_SECRET',self.m._command(telegram.update(1,'/mcp'),snap))

    def test_other_binding_future_and_stale_records_do_not_claim_current_data(self):
        self.s.record('mcp_import_events','foreign',self.now,'fixture',{'account_fingerprint':'b'*64})
        self.assertEqual(read_status(self.e,self.now)['status'],'no_record')
        record_result(self.e,self.now,{'status':'collected','accepted_posts':3,'rejected_posts':0})
        self.assertEqual(read_status(self.e,self.now-timedelta(seconds=1))['status'],'unavailable')
        self.assertTrue(read_status(self.e,self.now+timedelta(hours=49))['stale'])
        text=self.m._command(telegram.update(1,'/mcp'),self.m._snapshot(self.now+timedelta(hours=49)))
        self.assertIn('48시간 초과',text)

    def test_unfinished_claim_is_not_shown_as_success(self):
        self.s.db.execute('CREATE TABLE mcp_import_runs(event_key TEXT PRIMARY KEY,state TEXT)')
        self.s.db.execute('INSERT INTO mcp_import_runs VALUES (?,?)',('mcp:'+self.e.account_fingerprint()+':1','CLAIMED'))
        text=self.m._command(telegram.update(1,'/mcp'),self.m._snapshot(self.now))
        self.assertIn('작업 중단',text)
        self.assertIn('미확인',text)
        self.assertNotIn('반입 완료',text)

    def test_commands_reject_injection_unauthorized_forwarded_and_group_messages(self):
        snap=self.m._snapshot(self.now)
        for command in ('/mcp','/excluded','/sentiment'):
            messages=[telegram.update(1,command,sender=99),telegram.update(2,command,chat=-1,chat_type='group'),
                      telegram.update(3,command,bot=True),telegram.update(4,command+' now'),
                      telegram.update(5,command+'\n/collect'),telegram.update(6,command+'@otherbot')]
            forwarded=telegram.update(7,command);forwarded['message']['forward_origin']={}
            messages.append(forwarded)
            for message in messages: self.assertIsNone(self.m._command(message,snap))

    def test_mcp_timestamp_changes_do_not_repeat_automatic_notifications(self):
        record_result(self.e,self.now,{'status':'skipped','reason':'disabled','accepted_posts':0,'rejected_posts':0})
        self.baseline()
        later=self.now+timedelta(hours=6)
        record_result(self.e,later,{'status':'skipped','reason':'disabled','accepted_posts':0,'rejected_posts':0})
        self.m.notify(later)
        self.assertEqual(self.t.sent(),[])

    def test_real_command_outbox_is_deduplicated(self):
        self.baseline()
        self.t.updates=[telegram.update(20,'/mcp'),telegram.update(21,'/excluded'),telegram.update(22,'/sentiment')]
        for seconds in (2,4,6,8): self.m.poll_and_notify(self.now+timedelta(seconds=seconds))
        self.assertEqual(len(self.t.sent()),3)
        self.assertFalse(self.s.rows('SELECT * FROM publishing_jobs'))

    def test_local_short_poll_is_explicit_and_cloud_default_unchanged(self):
        self.client.get_updates(0,timeout=0)
        self.assertEqual(self.t.calls[-1][2]['timeout'],0)
        self.client.get_updates(0)
        self.assertEqual(self.t.calls[-1][2]['timeout'],5)
        for value in (True,-1,6,'0'):
            with self.assertRaises(ValueError): self.client.get_updates(0,timeout=value)


class SentimentMonitoringTests(unittest.TestCase):
    setUp=ingestion.MCPIngestionTests.setUp
    collect=ingestion.MCPIngestionTests.collect

    def text(self):
        client=TelegramClient(telegram.TOKEN,42,42,telegram.Transport())
        monitor=TelegramMonitor(self.engine,client,enabled=True)
        return monitor._command(telegram.update(1,'/sentiment'),monitor._snapshot(self.now))

    def test_verified_skill_is_read_only_and_invalidated_after_rejection(self):
        self.collect()
        recorded=read_status(self.engine,self.now)
        self.assertEqual(recorded['status'],'collected')
        self.assertEqual(recorded['accepted_posts'],3)
        self.assertEqual(recorded['rejected_posts'],0)
        before=self.store.db.total_changes
        files={p:p.read_bytes() for p in (self.root/'learning').rglob('*') if p.is_file()}
        text=self.text()
        self.assertIn('현재 사용 가능한 교훈 1개',text)
        self.assertIn('긍정 1',text)
        self.assertEqual(self.store.db.total_changes,before)
        self.assertTrue(all(p.read_bytes()==value for p,value in files.items()))
        self.assertEqual(set(files),{p for p in (self.root/'learning').rglob('*') if p.is_file()})
        self.mcp.comments.pop('source');self.now+=timedelta(hours=6);self.collect()
        self.assertIn('현재 사용 가능한 교훈 0개',self.text())
        self.assertEqual(read_status(self.engine,self.now)['reasons'],{'mentions_source_unverified':3})

    def test_stale_or_tampered_skill_cannot_appear_usable(self):
        self.collect();self.now+=timedelta(hours=49)
        self.assertIn('현재 사용 가능한 교훈 0개',self.text())
        self.now-=timedelta(hours=49)
        skill=self.root/'learning/hermes/skills/disanchor-marketing-feedback/SKILL.md'
        skill.write_text('PRIVATE_SECRET tampered skill')
        text=self.text()
        self.assertIn('현재 사용 가능한 교훈 0개',text)
        self.assertNotIn('PRIVATE_SECRET',text)

    def test_read_only_skill_handle_rejects_mutations(self):
        self.collect()
        from gtm.learning import LearningLoop
        loop=LearningLoop(self.root/'learning',read_only=True)
        try:
            with self.assertRaisesRegex(ValueError,'read_only'):
                loop.update(self.engine.feedback(self.now),now=self.now,policy_version='test',account_fingerprint=self.engine.account_fingerprint())
        finally: loop.close()
