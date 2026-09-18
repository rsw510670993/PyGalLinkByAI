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
        self.assertFalse(_torrent_name_matches(
            '[251128] [Whirlpool] 猫忍えくすはーとSPIN！ 2 通常版',
            '[230928] [Whirlpool] 猫忍えくすはーとSPIN！ + Bonus'))
        self.assertFalse(_torrent_name_matches(
            '[260327] [CLIP☆CRAFT] ユニオリズム・カルテット B2-STYLE + Mini Drama',
            '[141226] [CLIP☆CRAFT] ユニオリズム・カルテット'))
        self.assertFalse(_torrent_name_matches(
            '[250228] [エウシュリー] 百千の定にかわたれし剋 彼女たちの東奔西走拡張パック',
            '[240830] [エウシュリー] 百千の定にかわたれし剋 + Append + Update 1.01'))
        self.assertFalse(_torrent_name_matches(
            'LESSON',
            '[241220] [だーくワン！] 催眠性指導 -Secret Lesson- + Tokuten'))


class ShortNameCompanyTests(unittest.TestCase):
    def test_english_candidate_requires_marker_in_game_name(self):
        from tool.egs_match import allows_english_candidate

        self.assertFalse(allows_english_candidate(
            "作品名", "Translated title [English Patched]"))
        self.assertTrue(allows_english_candidate(
            "作品名 English版", "Translated title [English]"))
        self.assertTrue(allows_english_candidate(
            "作品名 英語版", "Translated title [English]"))
        self.assertTrue(allows_english_candidate(
            "作品名", "作品名 多国語版 Chinese-English"))
        self.assertTrue(allows_english_candidate(
            "作品名", "作品名 [English, Japanese, Chinese]"))
        self.assertTrue(allows_english_candidate(
            "作品名", "作品名 [EN/JP/CHT]"))
        self.assertTrue(allows_english_candidate("作品名", "作品名 [Japanese]"))

    def test_short_name_length_ignores_punctuation(self):
        from tool.egs_match import is_abnormally_short_name

        self.assertTrue(is_abnormally_short_name("Re:BF"))
        self.assertTrue(is_abnormally_short_name("D.C.5"))
        self.assertFalse(is_abnormally_short_name("悪魔の少女"))

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

    def test_trailing_number_mismatch_blocks_wrong_sequel(self):
        from tool.egs_match import THRESHOLD, score_candidate

        game = {
            "name": "光翼戦姫エクスティアコンチェルト4",
            "company": "Lusterise",
            "date": "2024-08",
            "release_date": "2024-08-30",
        }
        wrong = {
            "nyaa_title": "│2D.G.F.│[240726][next_0414][Lusterise] 光翼戦姫エクスティアコンチェルト3 DL版 + FANZA特典 [1319MB]",
            "nyaa_date": "2024-08-07 02:52",
        }
        right = {
            "nyaa_title": "[240830] [Lusterise] 光翼戦姫エクスティアコンチェルト4 + Voice Drama",
            "nyaa_date": "2024-08-30",
        }
        s_wrong, d_wrong = score_candidate(game, wrong)
        self.assertLess(s_wrong, THRESHOLD)
        self.assertTrue(d_wrong.get("edition_mismatch"))
        s_right, d_right = score_candidate(game, right)
        self.assertGreaterEqual(s_right, THRESHOLD)
        self.assertNotIn("edition_mismatch", d_right)

    def test_unnumbered_base_does_not_match_numbered_sequel(self):
        from tool.egs_match import THRESHOLD, score_candidate

        candidate = {
            "nyaa_title": "[260416][エロフラ部] 睡眠姦シミュレーション9 [RJ01605313].zip",
            "nyaa_date": "2026-04-16",
        }
        base_score, base_detail = score_candidate({
            "name": "睡眠姦シミュレーション",
            "company": "エロフラ部",
            "date": "2023-01",
            "release_date": "2023-01-09",
        }, candidate)
        sequel_score, sequel_detail = score_candidate({
            "name": "睡眠姦シミュレーション9",
            "company": "エロフラ部",
            "date": "2026-04",
            "release_date": "2026-04-16",
        }, candidate)

        self.assertLess(base_score, THRESHOLD)
        self.assertTrue(base_detail.get("edition_mismatch"))
        self.assertGreaterEqual(sequel_score, THRESHOLD)
        self.assertNotIn("edition_mismatch", sequel_detail)


class DownloadFailedDetectionTests(unittest.TestCase):
    def test_offline_list_reads_every_page(self):
        from unittest.mock import patch
        from tool.p115_client import offline_list

        class Client:
            def __init__(self):
                self.pages = []

            def offline_list(self, payload):
                page = payload['page']
                self.pages.append(page)
                return {
                    'page': page, 'page_count': 2,
                    'tasks': [{'info_hash': str(page) * 40}],
                }

        client = Client()
        with patch('tool.p115_client.load_client', return_value=client), \
             patch('tool.p115_client._import_p115client', return_value=(None, lambda value: value)):
            result = offline_list()
        self.assertTrue(result['success'])
        self.assertEqual(client.pages, [1, 2])
        self.assertEqual(len(result['tasks']), 2)

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
            strict = check_magnet_exists(magnet, '', strict_infohash=True)
        self.assertFalse(out['download_failed'])
        self.assertTrue(out['in_offline_tasks'])
        self.assertTrue(out['exists'])
        self.assertTrue(strict['in_offline_tasks'])
        self.assertFalse(strict['exists'])

    def test_check_magnet_exists_exposes_pending_task_submission_time(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = 'ef' * 20
        magnet = f'magnet:?xt=urn:btih:{h}'
        with patch('tool.p115_client.offline_list', return_value={
            'success': True,
            'tasks': [{
                'info_hash': h, 'url': magnet, 'display_status': 'downloading',
                'status': 1, 'add_time': 123456, 'percentDone': 90.5,
            }],
        }):
            out = check_magnet_exists(magnet, '', strict_infohash=True)
        self.assertEqual(out['offline_task_add_time'], 123456)
        self.assertEqual(out['offline_percent'], 90.5)

    def test_scoped_file_search_does_not_fallback_to_global_root(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = '12' * 20
        magnet = (f'magnet:?xt=urn:btih:{h}'
                  '&dn=%5B260101%5D%5BBrand%5DGame')
        with (
            patch('tool.p115_client.offline_list',
                  return_value={'success': True, 'tasks': []}),
            patch('tool.p115_client._resolve_path_to_cid', return_value=0),
            patch('tool.p115_client.search_files') as search,
        ):
            out = check_magnet_exists(
                magnet, '', strict_infohash=False,
                allowed_save_paths=['/GAL/GAL-2026'],
            )
        self.assertFalse(out['exists'])
        search.assert_not_called()

    def test_scoped_check_rejects_finished_product_outside_target_directory(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = 'ef' * 20
        magnet = f'magnet:?xt=urn:btih:{h}'
        task = {
            'info_hash': h, 'url': magnet, 'display_status': 'finished',
            'status': 2, 'file_id': 'product',
        }
        with (
            patch('tool.p115_client._resolve_path_to_cid', return_value=2026),
            patch('tool.p115_client.get_item_info',
                  return_value={'pid': 'old-parent'}),
            patch('tool.p115_client.parent_crumbs_path',
                  return_value='/GAL.old/GAL-2025'),
        ):
            outside = check_magnet_exists(
                magnet, '', strict_infohash=True, offline_tasks=[task],
                allowed_save_paths=['/GAL/GAL-2026'],
            )
        self.assertFalse(outside['exists'])
        self.assertFalse(outside['in_offline_tasks'])

        with (
            patch('tool.p115_client._resolve_path_to_cid', return_value=2026),
            patch('tool.p115_client.get_item_info',
                  return_value={'pid': 'current-parent'}),
            patch('tool.p115_client.parent_crumbs_path',
                  return_value='/GAL/GAL-2026'),
        ):
            inside = check_magnet_exists(
                magnet, '', strict_infohash=True, offline_tasks=[task],
                allowed_save_paths=['/GAL/GAL-2026'],
            )
        self.assertTrue(inside['exists'])
        self.assertTrue(inside['in_offline_tasks'])

    def test_scoped_search_rejects_hit_leaked_from_gal_old(self):
        from unittest.mock import patch
        from tool.p115_client import check_magnet_exists
        h = '12' * 20
        magnet = f'magnet:?xt=urn:btih:{h}&dn=[260227]Game'
        leaked = {
            'cid': 'old-folder', 'pid': 'old-year', 'fc': 0,
            'n': '[260227]Game', 'pc': 'old-pick',
        }
        with (
            patch('tool.p115_client._resolve_path_to_cid', return_value='new-year'),
            patch('tool.p115_client.search_files', return_value=[leaked]),
            patch('tool.p115_client.parent_crumbs_path',
                  return_value='/GAL.old/GAL-2026'),
        ):
            result = check_magnet_exists(
                magnet, '/GAL/GAL-2026', offline_tasks=[],
            )
        self.assertFalse(result['exists'])
        self.assertEqual(result['matched_files'], [])


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
