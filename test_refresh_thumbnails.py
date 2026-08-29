import unittest
from unittest.mock import patch

from tool.refresh_thumbnails import _dedup_key, _edition_primary_rank, _sidecar_stamp


class EditionDedupKeyTest(unittest.TestCase):
    def test_game_and_three_item_sets_share_key(self):
        game_set = "制服カノジョ2 せりか 熱愛BOX ゲームセット"
        three_item_set = "制服カノジョ2 せりか 熱愛BOX 3点セット"
        self.assertEqual(
            _dedup_key(game_set, aggressive=True),
            _dedup_key(three_item_set, aggressive=True),
        )

    def test_power_up_kit_with_variant_shares_key(self):
        standalone = "三國志8 REMAKE パワーアップキット"
        bundle = "三國志8 REMAKE with パワーアップキット"
        self.assertEqual(
            _dedup_key(standalone, aggressive=True),
            _dedup_key(bundle, aggressive=True),
        )


class EditionPrimaryRankTest(unittest.TestCase):
    def _row(self, name, **values):
        return {
            "name": name,
            "downloaded": values.get("downloaded", 0),
            "submitted_115": values.get("submitted_115", 0),
            "link": values.get("link"),
        }

    def test_complete_game_bundle_is_preferred_without_115_data(self):
        game_set = self._row("制服カノジョ2 せりか 熱愛BOX ゲームセット")
        three_item_set = self._row("制服カノジョ2 せりか 熱愛BOX 3点セット")
        with_kit = self._row("三國志8 REMAKE with パワーアップキット")
        standalone_kit = self._row("三國志8 REMAKE パワーアップキット")
        self.assertLess(_edition_primary_rank(game_set), _edition_primary_rank(three_item_set))
        self.assertLess(_edition_primary_rank(with_kit), _edition_primary_rank(standalone_kit))

    def test_existing_link_still_has_priority(self):
        bundle = self._row("三國志8 REMAKE with パワーアップキット")
        linked = self._row("三國志8 REMAKE パワーアップキット", link="magnet:test")
        self.assertLess(_edition_primary_rank(linked), _edition_primary_rank(bundle))


class SidecarStampTest(unittest.TestCase):
    @patch("tool.refresh_thumbnails.time.time_ns", side_effect=[100, 101])
    @patch("tool.refresh_thumbnails.time.strftime", return_value="20260829_214222")
    def test_same_second_stamps_are_unique(self, _strftime, _time_ns):
        self.assertNotEqual(_sidecar_stamp(), _sidecar_stamp())


if __name__ == "__main__":
    unittest.main()
