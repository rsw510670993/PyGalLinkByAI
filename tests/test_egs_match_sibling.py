"""关系图守卫：候选指向兄弟作品时判 reject（2026-11-27 盲测驱动）。

盲测暴露的两类误伤：
1. 同作另一个版本（パッケージ版/DL版/特典）被当成兄弟作；
2. 本体名是兄弟名前缀时（巨乳ファンタジー外伝2 vs ...外伝2 after）判断失效。
"""
import unittest

from tool import egs_match


class SiblingConflictTests(unittest.TestCase):
    def test_same_work_edition_is_not_conflict(self):
        # 盲测错例：候选是本体另一个版本，含本体名 → 不判冲突。
        conflict, hit = egs_match.sibling_conflict(
            '巨乳ファンタジー外伝2 after',
            '(18禁ゲーム) [160923] [WAFFLE] 巨乳ファンタジー外伝2 after パッケージ版',
            [('sequel', '巨乳ファンタジー外伝2')],
        )
        self.assertFalse(conflict)
        self.assertIsNone(hit)

    def test_sequel_is_conflict(self):
        conflict, hit = egs_match.sibling_conflict(
            'アイキス2',
            '│2D.G.F.│[220325][1159434][戯画] アイキス3 sexy パッケージ版',
            [('sequel', 'アイキス3 sexy')],
        )
        self.assertTrue(conflict)
        self.assertEqual(hit, 'アイキス3 sexy')

    def test_sibling_sharing_main_title_is_not_conflict(self):
        # 兄弟名就是本体主标题 → 同一系列主线，不能把特典误杀。
        conflict, _ = egs_match.sibling_conflict(
            '天冥のコンキスタ -魔族制圧編-',
            '│2D.G.F.│ [200529] [エウシュリー] 天冥のコンキスタ ソフマップ特典',
            [('sequel', '天冥のコンキスタ')],
        )
        self.assertFalse(conflict)

    def test_short_sibling_name_ignored(self):
        conflict, _ = egs_match.sibling_conflict(
            'ある作品のタイトル', '別作品 アイキス3 の種',
            [('sequel', 'アイキス3')],
        )
        self.assertFalse(conflict)

    def test_transplant_kind_ignored(self):
        conflict, _ = egs_match.sibling_conflict(
            'ある作品のタイトル', 'ある作品のタイトル PSVita移植版',
            [('transplant', 'ある作品のタイトル移植')],
        )
        self.assertFalse(conflict)

    def test_no_siblings(self):
        conflict, _ = egs_match.sibling_conflict('ある作品のタイトル', '全然違うゲーム', None)
        self.assertFalse(conflict)


if __name__ == '__main__':
    unittest.main()
