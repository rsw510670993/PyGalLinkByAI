"""标题相似但非同一作品的候选风险规则（2026-11-25 抽样驱动）。"""
import unittest

from tool import egs_match


def score(title, date=None, game=None):
    return egs_match.score_candidate(
        game or {'name': 'しゅぷれ～むキャンディ ～王道には王道たる理由があるんです!～',
                 'company': '枕', 'date': '2008-09'},
        {'nyaa_title': title, 'nyaa_date': date},
    )


class RiskRuleTests(unittest.TestCase):
    def test_hcg_candidate_pushed_below_threshold(self):
        s, d = score('[080926] [枕] しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～ HCG.rar')
        self.assertLess(s, egs_match.THRESHOLD)
        self.assertTrue(d.get('non_game_content'))

    def test_series_volume_pushed_below_threshold(self):
        game = {'name': 'アルタードピンク ～特務戦隊デュエルレンジャー～',
                'company': 'Black Rainbow', 'date': '2008-09'}
        s, d = score('[080829][Black Rainbow] アルタードピンク ～特務戦隊デュエルレンジャー～ 第2話 磐田くるみ編',
                     game=game)
        self.assertLess(s, egs_match.THRESHOLD)
        self.assertTrue(d.get('series_volume'))

    def test_volume_inside_game_name_not_flagged(self):
        game = {'name': '作品 第2話', 'company': 'X', 'date': '2020-01'}
        s, d = score('作品 第2話', game=game)
        self.assertFalse(d.get('series_volume'))

    def test_far_release_date_only_flagged(self):
        # 老作补种很常见：年代差只做标记供人工排序，不压分。
        game = {'name': '秋のうららの ～あかね色商店街～', 'company': 'ブルームハンドル', 'date': '2007-01'}
        s, d = score('(18禁ゲーム)[100430][ブルームハンドル]秋のうららの～あかね色商店街～',
                     date='2010-04-30', game=game)
        self.assertTrue(d.get('date_far'))
        self.assertGreaterEqual(s, egs_match.THRESHOLD - 1)

    def test_normal_candidate_unaffected(self):
        s, d = score('[080926] [枕] しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～')
        self.assertGreaterEqual(s, egs_match.THRESHOLD)
        self.assertFalse(d.get('non_game_content'))
        self.assertFalse(d.get('series_volume'))


if __name__ == '__main__':
    unittest.main()
