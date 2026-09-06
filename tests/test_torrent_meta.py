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


class ShortNameCompanyTests(unittest.TestCase):
    def test_short_name_requires_company(self):
        from tool.egs_match import THRESHOLD, score_candidate

        game = {"name": "HOME", "company": "SORAREVO", "date": "2026-08", "release_date": "2025-11-18"}
        wrong = {"nyaa_title": "[251118] [Shiravune] Home Sweet Homecoming [English]", "nyaa_date": "2025-11-18"}
        right = {"nyaa_title": "[251118] [SORAREVO] HOME", "nyaa_date": "2025-11-18"}

        s_wrong, d_wrong = score_candidate(game, wrong)
        self.assertLess(s_wrong, THRESHOLD)
        self.assertTrue(d_wrong.get("short_name_requires_company"))

        s_right, d_right = score_candidate(game, right)
        self.assertGreaterEqual(s_right, THRESHOLD)
        self.assertNotIn("short_name_requires_company", d_right)

    def test_longer_name_does_not_require_company(self):
        from tool.egs_match import score_candidate
        game = {"name": "マガルミナ 豪華版", "company": "Purple software", "date": "2026-06", "release_date": "2026-06-26"}
        cand = {"nyaa_title": "[260626] [パープルソフトウェア] マガルミナ 豪華版", "nyaa_date": "2026-06-26"}
        s, d = score_candidate(game, cand)
        self.assertNotIn("short_name_requires_company", d)


class DownloadFailedDetectionTests(unittest.TestCase):
    def test_check_magnet_exists_marks_failed_task(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = 'ab' * 20
        magnet = f'magnet:?xt=urn:btih:{h}'
        with patch('tool.p115_client.offline_list', return_value={
            'success': True,
            'tasks': [{'info_hash': h, 'url': magnet, 'display_status': 'failed', 'status': -1}],
        }):
            out = check_magnet_exists(magnet, '')
        self.assertTrue(out['download_failed'])
        self.assertFalse(out['exists'])
        self.assertFalse(out['in_offline_tasks'])

    def test_check_magnet_exists_distinguishes_pending_task(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = 'cd' * 20
        magnet = f'magnet:?xt=urn:btih:{h}'
        with patch('tool.p115_client.offline_list', return_value={
            'success': True,
            'tasks': [{'info_hash': h, 'url': magnet, 'display_status': '', 'status': 0}],
        }):
            out = check_magnet_exists(magnet, '')
        self.assertFalse(out['download_failed'])
        self.assertTrue(out['in_offline_tasks'])
        self.assertTrue(out['exists'])


class PendingRowsDownloadFailedTests(unittest.TestCase):
    def test_pending_rows_includes_download_failed_despite_history(self):
        import tempfile, os
        import sqlite3
        from tool.egs_core import ensure_egs_schema, open_egs_db
        from tool.egs_magnet import ensure_egs_magnet_schema, pending_rows

        tmp = tempfile.mktemp(suffix='.db')
        conn = open_egs_db(tmp)
        ensure_egs_schema(conn)
        ensure_egs_magnet_schema(conn)
        # 2026-01: 两条有磁链的行，一条 download_failed=1，一条正常
        conn.execute("""INSERT INTO egs_games
            (egs_id, model, egs_date, egs_name, egs_company, date, name, company,
             release_ts, link, downloaded, submitted_115, download_failed)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,0,?)""",
            (1, 'PC', '2026-01-01', '失败行', 'X', '2026-01', '失败行', 'X',
             '2026-01-01', 'magnet:?xt=urn:btih:' + '1'*40, 1))
        conn.execute("""INSERT INTO egs_games
            (egs_id, model, egs_date, egs_name, egs_company, date, name, company,
             release_ts, link, downloaded, submitted_115, download_failed)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0)""",
            (2, 'PC', '2026-01-01', '正常行', 'X', '2026-01', '正常行', 'X',
             '2026-01-01', 'magnet:?xt=urn:btih:' + '2'*40))
        # 两条都有搜索历史
        conn.executemany("INSERT INTO egs_nyaa_search_log (egs_id, selected_infohash) VALUES (?,?)",
                         [(1, '1'*40), (2, '2'*40)])
        conn.commit()
        rows = pending_rows(conn, 2026, force=False)
        ids = [r['egs_id'] for r in rows]
        self.assertIn(1, ids)
        self.assertNotIn(2, ids)
        conn.close()
        os.unlink(tmp)
