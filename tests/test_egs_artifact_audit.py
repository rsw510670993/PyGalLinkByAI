"""残留 link 巡检（tool.egs_core.audit_artifact_links）的离线回归检查。

四类口径来自 2026-11-25 清理裁定：失败态不留 hash link、记录 hash 必须与
link 指向的 artifact hash 一致、一个 egs 只挂一条 link、有 hash 不能只剩
folder-only link。人工/dlc link 与共享目录 dlc 不在自动清理范围。
"""
import sqlite3
import unittest

from tool import cli, egs_core

HASH_A = 'a' * 40
HASH_B = 'b' * 40
HASH_C = 'c' * 40


def _new_db():
    conn = sqlite3.connect(':memory:')
    egs_core.ensure_egs_schema(conn)
    egs_core.ensure_artifact_schema(conn)
    return conn


def _add_game(conn, egs_id, link='', infohash=None, failed=0):
    conn.execute(
        "INSERT INTO egs_games (egs_id, model, egs_date, egs_name, egs_company,"
        " date, name, company, link, infohash_hex, download_failed)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (egs_id, 'x', '2020-01-01', f'g{egs_id}', 'c', '2020-01',
         f'g{egs_id}', 'c', link, infohash, failed),
    )


def _add_artifact(conn, artifact_id, infohash=None, cid=None):
    conn.execute(
        "INSERT INTO egs_artifacts (artifact_id, infohash_hex, folder_cid, kind)"
        " VALUES (?,?,?,?)",
        (artifact_id, infohash, cid, 'both' if infohash and cid else
         ('torrent' if infohash else 'folder')),
    )


def _add_link(conn, egs_id, artifact_id, relation='primary', source='auto'):
    conn.execute(
        "INSERT INTO egs_artifact_links (egs_id, artifact_id, relation, source)"
        " VALUES (?,?,?,?)",
        (egs_id, artifact_id, relation, source),
    )


class AuditArtifactLinksTests(unittest.TestCase):
    def test_clean_db_is_ok(self):
        conn = _new_db()
        self.addCleanup(conn.close)
        _add_game(conn, 1, link='magnet:x', infohash=HASH_A)
        _add_artifact(conn, 10, HASH_A)
        _add_link(conn, 1, 10)
        result = egs_core.audit_artifact_links(conn)
        self.assertTrue(result['tables_present'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['counts'], {
            'failed_hash_links': 0, 'hash_mismatch': 0,
            'multi_link_egs': 0, 'hash_with_folder_only': 0,
        })
        self.assertEqual(result['orphan_artifacts'], 0)

    def test_each_residue_class_is_counted(self):
        conn = _new_db()
        self.addCleanup(conn.close)
        # a 失败态仍挂 hash link
        _add_game(conn, 1, link='', infohash=HASH_A, failed=1)
        _add_artifact(conn, 10, HASH_A)
        _add_link(conn, 1, 10)
        # b 记录 hash 与 link hash 不一致
        _add_game(conn, 2, link='magnet:y', infohash=HASH_B)
        _add_artifact(conn, 20, HASH_C)
        _add_link(conn, 2, 20)
        # c 一个 egs 多条 link
        _add_game(conn, 3, link='magnet:z')
        _add_artifact(conn, 30, 'd' * 40)
        _add_artifact(conn, 31, 'e' * 40)
        _add_link(conn, 3, 30)
        _add_link(conn, 3, 31, relation='member')
        # d 有 hash 但只挂 folder-only link
        _add_game(conn, 4, link='magnet:w', infohash='f' * 40)
        _add_artifact(conn, 40, None, cid='cid1')
        _add_link(conn, 4, 40)
        # 孤儿 artifact
        _add_artifact(conn, 50, 'g' * 40)
        conn.commit()

        result = egs_core.audit_artifact_links(conn)
        self.assertFalse(result['ok'])
        self.assertEqual(result['counts'], {
            'failed_hash_links': 1, 'hash_mismatch': 1,
            'multi_link_egs': 1, 'hash_with_folder_only': 1,
        })
        self.assertEqual(result['samples']['failed_hash_links'], [1])
        self.assertEqual(result['samples']['hash_mismatch'], [2])
        self.assertEqual(result['samples']['multi_link_egs'], [3])
        self.assertEqual(result['samples']['hash_with_folder_only'], [4])
        self.assertEqual(result['orphan_artifacts'], 1)

    def test_protected_links_are_not_reported_as_failed_residue(self):
        """人工 link 与 dlc link 受保护：失败态挂着也不算 a 类残留。"""
        conn = _new_db()
        self.addCleanup(conn.close)
        _add_game(conn, 1, link='', infohash=HASH_A, failed=1)
        _add_artifact(conn, 10, HASH_A)
        _add_link(conn, 1, 10, source='manual')
        _add_game(conn, 2, link='', infohash=HASH_B, failed=1)
        _add_artifact(conn, 20, HASH_B)
        _add_link(conn, 2, 20, relation='dlc', source='manual')
        result = egs_core.audit_artifact_links(conn)
        self.assertEqual(result['counts']['failed_hash_links'], 0)
        self.assertTrue(result['ok'])

    def test_dlc_link_does_not_trigger_hash_mismatch(self):
        """共享目录 dlc 允许不同 hash 的产物挂同一目录。"""
        conn = _new_db()
        self.addCleanup(conn.close)
        _add_game(conn, 1, link='magnet:x', infohash=HASH_A)
        _add_artifact(conn, 10, HASH_B, cid='cid1')
        _add_link(conn, 1, 10, relation='dlc')
        result = egs_core.audit_artifact_links(conn)
        self.assertEqual(result['counts']['hash_mismatch'], 0)
        self.assertTrue(result['ok'])

    def test_missing_tables_reports_not_present(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        result = egs_core.audit_artifact_links(conn)
        self.assertFalse(result['tables_present'])
        self.assertFalse(result['ok'])


class CliWiringTests(unittest.TestCase):
    def test_egs_artifact_audit_subcommand_registered(self):
        parser = cli.build_parser()
        args = parser.parse_args(['egs', 'artifact_audit'])
        self.assertIs(args.func, cli.cmd_egs_artifact_audit)
        self.assertFalse(args.strict)

    def test_strict_flag(self):
        parser = cli.build_parser()
        args = parser.parse_args(['egs', 'artifact_audit', '--strict'])
        self.assertTrue(args.strict)


if __name__ == '__main__':
    unittest.main()
