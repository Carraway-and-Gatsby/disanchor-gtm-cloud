"""Fixed provider batch gates; in-memory fixtures, no live state or API calls."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_ledger', ROOT / 'scripts/native-batch-ledger.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 26, 6, tzinfo=timezone.utc).astimezone(tz or timezone.utc)


class NativeBatchLedgerTests(unittest.TestCase):
    def setUp(self):
        data = json.loads((ROOT / 'docs/campaigns/north-america-20261027-35.json').read_text())
        items = []
        for platform, config in data['platforms'].items():
            items.extend({**copy, 'platform': platform, 'user': config['user'], 'handle': config['handle'],
                          'state': 'PREPARED', 'submission_attempts': 0} for copy in config['copy'])
        items.sort(key=lambda item: (item['scheduled_date'], item['platform']))
        self.plan = {'items': items, 'plan_sha256': data['plan_sha256'],
                     'status': 'AWAITING_EXACT_TEXT_APPROVAL', 'exact_text_approved': False}
        self.clock = patch.object(ledger, 'datetime', Frozen)
        self.clock.start(); self.addCleanup(self.clock.stop)

    def approve(self):
        ledger.transition(self.plan, 'approve')

    def test_validated_library_has_two_targets_35_distinct_slots_and_no_mutation(self):
        before = copy.deepcopy(self.plan)
        ledger.validate(self.plan, self.plan['plan_sha256'])
        self.assertEqual(self.plan, before)
        self.assertEqual(len(self.plan['items']), 35)

    def test_explicit_approval_is_required_and_cannot_be_replayed(self):
        with self.assertRaisesRegex(ValueError, 'approval_required'):
            ledger.transition(self.plan, 'claim', item_id=self.plan['items'][0]['id'])
        self.approve()
        with self.assertRaises(ValueError): self.approve()

    def test_claim_is_at_most_once_and_unresolved_outcome_stops_next_submission(self):
        self.approve()
        first, second = self.plan['items'][:2]
        ledger.transition(self.plan, 'claim', item_id=first['id'])
        with self.assertRaisesRegex(ValueError, 'no_resubmission'):
            ledger.transition(self.plan, 'claim', item_id=first['id'])
        with self.assertRaisesRegex(ValueError, 'unknown_submission'):
            ledger.transition(self.plan, 'claim', item_id=second['id'])
        self.assertEqual(second['submission_attempts'], 0)

    def test_finish_requires_all_provider_receipts_and_individual_readback(self):
        self.approve()
        with self.assertRaisesRegex(ValueError, 'provider_readback'):
            ledger.transition(self.plan, 'finish')
        for index, item in enumerate(self.plan['items']):
            ledger.transition(self.plan, 'claim', item_id=item['id'])
            job = 'offline-fixture-' + str(index)
            ledger.transition(self.plan, 'receipt', item_id=item['id'], job_id=job)
            with self.assertRaisesRegex(ValueError, 'receipt_mismatch'):
                ledger.transition(self.plan, 'verify', item_id=item['id'], job_id='wrong-fixture')
            ledger.transition(self.plan, 'verify', item_id=item['id'], job_id=job)
        ledger.transition(self.plan, 'finish')
        self.assertEqual(self.plan['status'], 'ACTIVE_BOUNDED_BATCH')

    def test_content_destination_time_or_count_changes_invalidate_the_batch(self):
        for mutate in (lambda p: p['items'][0].update(text='changed'),
                       lambda p: p['items'][0].update(user='other-profile'),
                       lambda p: p['items'][0].update(scheduled_date='2026-10-27T23:00:00Z'),
                       lambda p: p['items'].pop()):
            plan = copy.deepcopy(self.plan); mutate(plan)
            with self.assertRaises(ValueError): ledger.validate(plan, self.plan['plan_sha256'])
