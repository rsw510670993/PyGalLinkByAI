"""torrent_meta bencode 解析与工具函数测试（不依赖网络）。"""
import unittest

from tool import torrent_meta


class TorrentMetaTests(unittest.TestCase):
    def test_bdecode_roundtrip_and_info_hash(self):
        # 用小型 bencode 字典模拟 torrent 的 info 段
        info = {
            b'name': b'[260626][Brand] Game + Voice Drama',
            b'piece length': 262144,
            b'pieces': b'x' * 20,
            b'length': 12345,
        }
        data = torrent_meta.bencode({b'info': info, b'announce': b'udp://tracker'})
        parsed = torrent_meta.bdecode(data)
        self.assertEqual(parsed[b'info'][b'name'], info[b'name'])
        # info_hash 应可重算
        ih = torrent_meta.parse_torrent(data)  # 只验证解析
        self.assertEqual(ih['name'], '[260626][Brand] Game + Voice Drama')
        self.assertEqual(ih['total_size'], 12345)
        self.assertEqual(len(ih['infohash_hex']), 40)

    def test_parse_multi_file(self):
        info = {
            b'name': b'FolderName',
            b'piece length': 262144,
            b'pieces': b'y' * 20,
            b'files': [
                {b'length': 10, b'path': [b'FolderName', b'a.txt']},
                {b'length': 20, b'path.utf-8': [b'FolderName', b'b.txt']},
            ],
        }
        data = torrent_meta.bencode({b'info': info})
        meta = torrent_meta.parse_torrent(data)
        self.assertEqual(meta['name'], 'FolderName')
        self.assertEqual(meta['total_size'], 30)
        self.assertEqual(meta['files'][0]['path'], 'FolderName/a.txt')
        self.assertEqual(meta['files'][1]['path'], 'FolderName/b.txt')

    def test_torrent_id_from_url(self):
        self.assertEqual(torrent_meta.torrent_id_from_url('https://sukebei.nyaa.si/view/4636089'), '4636089')
        self.assertEqual(torrent_meta.torrent_id_from_url('https://sukebei.nyaa.si/download/4636089.torrent'), '4636089')
        self.assertIsNone(torrent_meta.torrent_id_from_url(None))
        self.assertIsNone(torrent_meta.torrent_id_from_url('https://example.com/nope'))


if __name__ == '__main__':
    unittest.main()


class TorrentNameMatchTests(unittest.TestCase):
    def test_iso_folder_and_extra_suffix_match(self):
        from tool.egs_organize import _torrent_name_matches
        self.assertTrue(_torrent_name_matches(
            '[260327] [Empress] 有閑夫人倶楽部',
            '[2026-03-27][Empress]有閑夫人倶楽部'))
        self.assertTrue(_torrent_name_matches(
            '[260327] [シルキーズプラス] リルカは幾重に夜を彩る + Voice Drama',
            '[2026-03-27][シルキーズプラス]リルカは幾重に夜を彩る'))
        self.assertFalse(_torrent_name_matches(
            '[260327] [Empress] Game A',
            '[260327] [Empress] Game B'))
