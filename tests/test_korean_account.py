"""Korean account output retains disclosure, duplication and promotion guards."""
from pathlib import Path
import unittest

from gtm.accounts import AccountRegistry
from gtm.policy import comparison_body, validate_content
from gtm.routing import validate_stage

ROOT = Path(__file__).resolve().parents[1]
BODY = '공용 컴퓨터를 쓸 수 있어도 개인적인 일을 하기는 불편할 수 있습니다. 내 작업 세션을 스스로 통제할 수 있는지도 살펴봐야 합니다.'


class KoreanAccountTests(unittest.TestCase):
    def test_only_technodemocracy_changes_language_and_platforms_stay_distinct(self):
        registry = AccountRegistry(ROOT / 'accounts.example.toml')
        self.assertEqual(registry.context('personal_threads')['language'], 'ko')
        for row in registry.all():
            if row['id'] != 'personal_threads': self.assertEqual(row['language'], 'en')
        self.assertEqual(registry.get('default_threads')['platform'], 'threads')
        self.assertEqual(registry.get('default_instagram')['platform'], 'instagram')
        self.assertEqual(registry.get('default_threads')['handle'], registry.get('default_instagram')['handle'])

    def test_natural_korean_disclosures_pass_but_past_or_negated_status_does_not(self):
        for phrase in ('Disanchor를 개발하면서, ', '개발 중인 Disanchor를 생각하며, ',
                       'Disanchor는 아직 개발 중입니다. '):
            self.assertTrue(validate_content('threads', phrase + BODY, language='ko'))
        for phrase in ('Disanchor는 개발 중이 아닙니다. ', 'Disanchor는 개발 중이었습니다. ',
                       'Disanchor를 개발했던 때에는, ', 'Disanchor를 연구하면서, '):
            with self.assertRaises(ValueError): validate_content('threads', phrase + BODY, language='ko')

    def test_moving_disclosure_cannot_hide_duplicate_korean_content(self):
        a = 'Disanchor를 개발하면서, ' + BODY
        b = BODY + ' Disanchor는 아직 개발 중입니다.'
        self.assertEqual(comparison_body(a), comparison_body(b))
        with self.assertRaisesRegex(ValueError, 'similar'):
            validate_content('threads', b, [a], language='ko')

    def test_korean_promotional_actions_are_rejected(self):
        for suffix in ('지금 가입하세요.', '프로필 링크를 확인하세요.', '좋아요를 눌러주세요.',
                       '친구에게 공유해 주세요.', '성능 보장!', '무료 체험을 시작하세요.'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                validate_content('threads', 'Disanchor를 개발하면서, ' + BODY + suffix, language='ko')

    def test_selected_korean_account_cannot_return_an_english_body(self):
        account = AccountRegistry(ROOT / 'accounts.example.toml').context('personal_threads')
        context = {'slots': [{'id': 'fixture', 'platform': 'threads', 'account_key': 'personal_threads'}],
                   'accounts': [account], 'plan': {'hypothesis': 'H2', 'hook': 'question'}}
        draft = {'slot_id': 'fixture', 'platform': 'threads', 'text': 'Disanchor를 개발하면서, ' + BODY,
                 'hypothesis': 'H2', 'hook': 'question', 'cta': 'none'}
        validate_stage('writing', {'drafts': [draft]}, context)
        draft['text'] = "While we're building Disanchor, a shared computer may be available but still awkward for private work. Control of a session matters alongside access to a machine."
        with self.assertRaisesRegex(ValueError, 'Korean'):
            validate_stage('writing', {'drafts': [draft]}, context)
