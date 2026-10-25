from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from gtm.config import Config, slots, timestamp
from gtm.engine import Engine, GuardError
from gtm.store import Store
from gtm.clone import MockClone
from gtm.publisher import MockPublisher


class WeeklyScheduleTests(unittest.TestCase):
    def config(self):
        return Config.load(Path(__file__).resolve().parents[1] / 'config.toml')

    def test_actual_source_has_six_explicit_weekday_slots_and_all_locks(self):
        c = self.config()
        planned = slots(c, timestamp('2026-10-26T00:00:00-04:00'))
        self.assertEqual(len(planned), 6)
        for platform, expected in [('x', [(1, '12:30'), (2, '15:30'), (3, '12:30')]),
                                   ('threads', [(1, '15:00'), (2, '12:00'), (3, '12:00')])]:
            actual = [timestamp(p['scheduled_at']).astimezone(ZoneInfo('America/New_York'))
                      for p in planned if p['platform'] == platform]
            self.assertEqual([(d.weekday(), d.strftime('%H:%M')) for d in actual], expected)
        self.assertFalse(c.publishing.auto_publish)
        self.assertTrue(c.publishing.dry_run)
        self.assertEqual(c.publishing.account_remaining_quota, 0)
        self.assertEqual(c.budget.target_total_usd, 20)
        self.assertEqual(c.platforms.enabled, ['x', 'threads'])

    def test_dst_preserves_local_times_changes_kst_and_keeps_168h(self):
        for start, expected_hour in [('2026-10-26T00:00:00-04:00', 1),
                                     ('2026-11-02T00:00:00-05:00', 2),
                                     ('2026-03-06T12:00:00Z', 1),
                                     ('2026-10-30T12:00:00Z', 2)]:
            begin = timestamp(start)
            planned = slots(self.config(), begin)
            self.assertEqual(len(planned), 6)
            first_x = next(p for p in planned if p['platform'] == 'x')
            self.assertEqual(timestamp(first_x['scheduled_at']).astimezone(ZoneInfo('Asia/Seoul')).hour, expected_hour)
            self.assertTrue(all(begin <= timestamp(p['scheduled_at']) < begin + timedelta(hours=168) for p in planned))
            self.assertEqual(len({(p['platform'], p['local_date']) for p in planned}), len(planned))

    def test_midday_start_skips_missed_slot_without_startup_burst(self):
        begin = timestamp('2026-10-27T14:00:00-04:00')
        planned = slots(self.config(), begin)
        self.assertEqual(planned[0]['platform'], 'threads')
        self.assertEqual(planned[0]['scheduled_at'], '2026-10-27T19:00:00+00:00')
        self.assertFalse(any(timestamp(p['scheduled_at']) == begin + timedelta(minutes=m) for p in planned for m in (10, 20)))

    def test_bad_days_clocks_shapes_and_modes_are_rejected(self):
        for name, value in [('x_weekdays', [1, 1, 3]), ('x_weekdays', [1, True, 3]),
                            ('x_weekdays', [1, 2, 5]), ('x_weekdays', [1, 2, 3, 4]),
                            ('x_times', ['12:30']), ('x_times', ['12:30', '24:00', '12:30']),
                            ('x_times', ['12:30', '15:30Z', '12:30']), ('mode', 'unknown')]:
            c = self.config()
            setattr(c.schedule, name, value)
            with self.assertRaises(ValueError): c.validate()

    def test_lower_caps_and_fingerprint_prevent_expansion_or_old_authorization(self):
        c = self.config()
        before = c.fingerprint()
        c.schedule.x_times[0] = '13:00'
        self.assertNotEqual(c.fingerprint(), before)
        c.platforms.max_posts_total = 3
        c.platforms.max_posts_x = 1
        planned = slots(c, timestamp('2026-10-26T00:00:00-04:00'))
        self.assertEqual(len(planned), 3)
        self.assertEqual(sum(p['platform'] == 'x' for p in planned), 1)

    def test_weekly_clock_is_pinned_and_clone_cannot_move_it(self):
        c = self.config()
        c.publishing.account_remaining_quota = 6  # Synthetic adapter only.
        now = timestamp('2026-10-27T10:00:00-04:00')
        with tempfile.TemporaryDirectory() as directory:
            s = Store(Path(directory) / 'synthetic.sqlite')
            try:
                e = Engine(c, s, publisher=MockPublisher(),
                           clone=MockClone(suggestion={'time': '17:30'}), simulated=True)
                e.init(now); e.start(now)
                e.daily(now)
                self.assertFalse(s.records('experiment_decisions')[0]['payload']['accepted'])
                self.assertEqual(len(s.rows('SELECT * FROM publishing_jobs')), 2)
                post = s.one('SELECT * FROM post_drafts ORDER BY scheduled_at LIMIT 1')
                e.check_schedule_slot(post, e.experiment())
                post['scheduled_at'] = '2026-10-27T22:00:00+00:00'
                with self.assertRaisesRegex(GuardError, 'weekly calendar'):
                    e.check_schedule_slot(post, e.experiment())
            finally:
                s.close()

    def test_tampered_weekly_time_is_blocked_before_provider_reads_or_writes(self):
        c = self.config()
        now = timestamp('2026-10-27T10:00:00-04:00')
        with tempfile.TemporaryDirectory() as directory:
            s = Store(Path(directory) / 'synthetic.sqlite')
            try:
                publisher = MockPublisher()
                e = Engine(c, s, publisher=publisher, simulated=True)
                e.init(now); e.start(now)
                post = e.draft(slots(c, now)[0], now)
                s.db.execute('UPDATE post_drafts SET scheduled_at=? WHERE id=?',
                             ('2026-10-27T22:00:00+00:00', post['id']))
                with patch.object(publisher, 'quota', side_effect=AssertionError('provider read')), \
                     patch.object(publisher, 'schedule', side_effect=AssertionError('provider write')):
                    with self.assertRaisesRegex(GuardError, 'weekly calendar'):
                        e.schedule(post['id'], now)
                self.assertFalse(s.rows('SELECT * FROM publishing_jobs'))
            finally:
                s.close()
