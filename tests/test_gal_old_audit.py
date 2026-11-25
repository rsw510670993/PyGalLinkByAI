"""旧库审计（gal_old_audit）与维护清理（tool.ops.prune）的离线回归检查。

agent_jobs 文件信箱已退役（2026-11-25），原 tests/test_agent_jobs.py 里
信箱相关用例随文件删除，这里只保留旧库审计与 prune 的部分。
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool import gal_old_audit, ops


class GalOldAuditPureTests(unittest.TestCase):
    def test_core_normalizes_case_width_and_brackets(self):
        self.assertEqual(gal_old_audit._core('[240329] [Miel] 人妻天国２'),
                         gal_old_audit._core('人妻天国2'))

    def test_noise_markers(self):
        self.assertTrue(gal_old_audit.is_noise('[Anime-sharing.com] girlcelly@x.txt'))
        self.assertTrue(gal_old_audit.is_noise('NemuAndHaruka.png'))
        self.assertFalse(gal_old_audit.is_noise('(18禁ゲーム) [240329] [Miel] 作品.rar'))

    def test_verdict_buckets(self):
        self.assertEqual(gal_old_audit.verdict(set(), {('a', '1')}), 'old_empty')
        self.assertEqual(gal_old_audit.verdict({('a', '1')}, {('a', '1'), ('b', '2')}),
                         'old_subset_new')
        self.assertEqual(gal_old_audit.verdict({('a', '1'), ('c', '3')}, {('a', '1')}),
                         'has_common')
        self.assertEqual(gal_old_audit.verdict({('z', '9')}, {('a', '1')}), 'no_overlap')


class GalOldAuditResumeTests(unittest.TestCase):
    """可续跑 / 进度 / 单项超时——都是为“115 慢且会整片超时”设计的。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state.json'
        self.entries = [{'n': 'A', 'cid': 'a', 'is_dir': True},
                        {'n': 'B', 'cid': 'b', 'is_dir': True}]
        self.calls = []

    def _ctx(self, walk):
        return (
            patch.object(gal_old_audit, 'resolve', return_value='oldcid'),
            patch.object(gal_old_audit, '_children', return_value=self.entries),
            patch.object(gal_old_audit, 'find_counterpart',
                         return_value={'cid': 'new', 'folder_name': 'x'}),
            patch.object(gal_old_audit, 'walk_files', side_effect=walk),
            patch('tool.egs_core.open_egs_db', return_value=MagicMock()),
        )

    def test_reports_progress_and_resumes_from_state_file(self):
        def walk(cid, max_pages=90):
            self.calls.append(cid)
            return [{'path': 'f', 'sha1': '1'}]

        seen = []
        ctx = self._ctx(walk)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
            result = gal_old_audit.audit_old_year(2020, write_manifest=False,
                                                  state_path=self.state,
                                                  progress=lambda d, t, c: seen.append((d, t, c)))
        self.assertEqual(seen, [(1, 2, 'A'), (2, 2, 'B')])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['counts']['old_subset_new'], 2)
        self.assertEqual(len(self.calls), 4)          # 2 条 × 2 次 walk

        # 第二次：同一 state 文件 → 全部命中缓存，不再打 115
        self.calls.clear()
        ctx = self._ctx(lambda cid, max_pages=90: self.fail('命中缓存时不应再 walk'))
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
            again = gal_old_audit.audit_old_year(2020, write_manifest=False, state_path=self.state)
        self.assertEqual(again['counts']['old_subset_new'], 2)
        self.assertEqual(self.calls, [])

    def test_deadline_stops_early_and_marks_partial(self):
        ctx = self._ctx(lambda cid, max_pages=90: [{'path': 'f', 'sha1': '1'}])
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
            result = gal_old_audit.audit_old_year(2020, write_manifest=False,
                                                  state_path=self.state,
                                                  deadline=time.time() - 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['partial'], 2)
        self.assertEqual(result['counts']['total'], 2)

    def test_single_item_timeout_does_not_kill_the_round(self):
        def walk(cid, max_pages=90):
            time.sleep(30)                            # 模拟卡在 115
            return []

        ctx = self._ctx(walk)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
            result = gal_old_audit.audit_old_year(2020, write_manifest=False,
                                                  state_path=self.state, per_item_timeout=1)
        self.assertEqual(result['status'], 'complete')   # 整轮没有失败
        self.assertEqual(result['counts']['timeout'], 2)  # 两条各自被单项超时兜住


class GalOldCleanTests(unittest.TestCase):
    """清理只碰「旧⊆新」候选，删前现场复验，默认只预览。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state.json'
        self.state.write_text(json.dumps({'year': 2021, 'items': {
            'OLD-A': {'name': 'OLD-A', 'verdict': 'old_subset_new',
                      'counterpart': {'cid': 'new', 'folder_name': 'NEW-A'}},
            'OLD-B': {'name': 'OLD-B', 'verdict': 'no_overlap',
                      'counterpart': {'cid': 'new2', 'folder_name': 'NEW-B'}},
        }}, ensure_ascii=False), encoding='utf-8')
        self.entry = {'n': 'OLD-A', 'cid': 'oldcid', 'is_dir': True}

    def _ctx(self, verdict='old_subset_new', delete=None):
        return (
            patch.object(gal_old_audit, '_find_entry', return_value=self.entry),
            patch.object(gal_old_audit, '_analyze_entry',
                         return_value={'verdict': verdict, 'old_only_count': 0}),
            patch('tool.egs_core.open_egs_db', return_value=MagicMock()),
            patch('tool.p115_client.delete_item',
                  return_value=delete or {'success': True}),
        )

    def test_requires_audit_state_first(self):
        result = gal_old_audit.clean_old_year(2021, execute=True, write_manifest=False,
                                              state_path=Path(self.temp.name) / 'missing.json')
        self.assertFalse(result['ok'])
        self.assertIn('先跑', result['error'])

    def test_only_subset_candidates_are_considered(self):
        ctx = self._ctx()
        with ctx[0], ctx[1], ctx[2], ctx[3] as delete:
            result = gal_old_audit.clean_old_year(2021, execute=False,
                                                  write_manifest=False, state_path=self.state)
        self.assertEqual(result['counts']['candidates'], 1)      # OLD-B 不在候选里
        self.assertEqual(result['counts']['verified'], 1)
        self.assertEqual(result['counts']['deleted'], 0)
        delete.assert_not_called()                              # 预览绝不删

    def test_execute_deletes_verified_candidates(self):
        ctx = self._ctx()
        with ctx[0], ctx[1], ctx[2], ctx[3] as delete:
            result = gal_old_audit.clean_old_year(2021, execute=True,
                                                  write_manifest=False, state_path=self.state)
        self.assertEqual(result['counts']['deleted'], 1)
        delete.assert_called_once_with('oldcid')

    def test_verify_failure_skips_instead_of_deleting(self):
        ctx = self._ctx(verdict='has_common')
        with ctx[0], ctx[1], ctx[2], ctx[3] as delete:
            result = gal_old_audit.clean_old_year(2021, execute=True,
                                                  write_manifest=False, state_path=self.state)
        self.assertEqual(result['counts']['verify_failed'], 1)
        self.assertEqual(result['counts']['deleted'], 0)
        delete.assert_not_called()

    def test_delete_failure_is_counted(self):
        ctx = self._ctx(delete={'success': False, 'message': 'errno 990009'})
        with ctx[0], ctx[1], ctx[2], ctx[3]:
            result = gal_old_audit.clean_old_year(2021, execute=True,
                                                  write_manifest=False, state_path=self.state)
        self.assertEqual(result['counts']['delete_failed'], 1)
        self.assertEqual(result['counts']['deleted'], 0)


class OpsPruneTests(unittest.TestCase):
    """维护清理：默认只预览；保留最新 N 个 + 最近 N 天；inbox 永不删。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'spool'
        for sub in ('inbox', 'working', 'outbox'):
            (self.root / sub).mkdir(parents=True)
        self.db_dir = base / 'db_backups'
        self.db_dir.mkdir()
        self.history = base / 'history.jsonl'
        now = time.time()
        # outbox：3 个任务，越新越靠前
        for idx in range(3):
            path = self.root / 'outbox' / f'job{idx}.json'
            path.write_text('{}', encoding='utf-8')
            os.utime(path, (now - idx * 20 * 86400, now - idx * 20 * 86400))
        # db_backups：5 个快照
        for idx in range(5):
            path = self.db_dir / f'egs.before_test_{idx}.db'
            path.write_bytes(b'x' * 100)
            os.utime(path, (now - idx * 10 * 86400, now - idx * 10 * 86400))
        # inbox 里永远不能被删
        inbox = self.root / 'inbox' / 'pending.json'
        inbox.write_text('{}', encoding='utf-8')
        os.utime(inbox, (now - 100 * 86400, now - 100 * 86400))
        self.inbox = inbox

    def _prune(self, execute):
        return ops.prune(keep=1, days=1, db_keep=1, db_days=1, execute=execute,
                         root=self.root, db_dir=self.db_dir,
                         history_path=self.history)

    def test_preview_reports_but_deletes_nothing(self):
        result = self._prune(execute=False)
        # 返回字段齐全（网页直接吃这些键）
        for key in ('status', 'execute', 'requested_by', 'params', 'spool', 'db_backups',
                    'inventory', 'deleted', 'freed_bytes', 'errors', 'highlights'):
            self.assertIn(key, result)
        for key in ('total', 'delete', 'keep', 'total_bytes', 'delete_bytes',
                    'oldest_to_delete', 'newest_kept'):
            self.assertIn(key, result['spool'])
            self.assertIn(key, result['db_backups'])
        self.assertFalse(result['execute'])
        self.assertEqual(result['spool']['delete'], 2)
        self.assertEqual(result['db_backups']['delete'], 4)
        self.assertGreater(result['db_backups']['delete_bytes'], 0)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(len(list((self.root / 'outbox').glob('*.json'))), 3)
        self.assertEqual(len(list(self.db_dir.glob('*.db'))), 5)
        self.assertFalse(self.history.exists())          # 预览不写履历

    def test_execute_keeps_newest_and_writes_history(self):
        result = self._prune(execute=True)
        self.assertTrue(result['execute'])
        self.assertEqual(result['deleted'], 6)
        self.assertGreater(result['freed_bytes'], 0)
        self.assertEqual([p.name for p in (self.root / 'outbox').glob('*.json')], ['job0.json'])
        self.assertEqual([p.name for p in self.db_dir.glob('*.db')], ['egs.before_test_0.db'])
        self.assertTrue(self.inbox.exists())             # inbox 永不删
        lines = self.history.read_text(encoding='utf-8').strip().split('\n')
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry['mode'], 'execute')
        self.assertEqual(entry['deleted'], 6)

    def test_keeps_anything_inside_recent_days(self):
        now = time.time()
        recent = self.root / 'outbox' / 'recent.json'
        recent.write_text('{}', encoding='utf-8')
        os.utime(recent, (now - 3600, now - 3600))       # 1 小时前
        result = ops.prune(keep=0, days=3, db_keep=0, db_days=0,
                           execute=False, root=self.root, db_dir=self.db_dir,
                           history_path=self.history)
        # job0（刚刚）与 recent（1 小时前）都在「最近 3 天」里 → 只删 job1/job2 两条老任务
        self.assertEqual(result['spool']['delete'], 2)
        self.assertTrue(recent.exists())
        self.assertTrue((self.root / 'outbox' / 'job0.json').exists())


if __name__ == '__main__':
    unittest.main()
