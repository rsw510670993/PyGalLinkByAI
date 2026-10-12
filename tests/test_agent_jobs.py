"""任务信箱（agent_jobs）与旧库只读审计的离线回归检查。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool import agent_jobs, gal_old_audit


class AgentJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'agent_jobs'

    def test_enqueue_writes_request_contract(self):
        job = agent_jobs.enqueue('crawl', 2020, dry_run=False, requested_by='web', root=self.root)
        path = self.root / 'inbox' / f"{job['job_id']}.json"
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(payload['action'], 'crawl')
        self.assertEqual(payload['year'], 2020)
        self.assertEqual(payload['year_end'], 2020)
        self.assertEqual(payload['requested_by'], 'web')
        self.assertEqual(payload['status'], 'queued')

    def test_enqueue_rejects_unknown_action_and_bad_range(self):
        with self.assertRaises(ValueError):
            agent_jobs.enqueue('nope', 2020, root=self.root)
        with self.assertRaises(ValueError):
            agent_jobs.enqueue('crawl', 2021, year_end=2020, root=self.root)

    def test_claim_is_fifo_and_atomic(self):
        first = agent_jobs.enqueue('crawl', 2020, root=self.root)
        second = agent_jobs.enqueue('magnet', 2021, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        self.assertEqual(claimed['job_id'], first['job_id'])
        self.assertTrue((self.root / 'working' / f"{first['job_id']}.json").exists())
        self.assertFalse((self.root / 'inbox' / f"{first['job_id']}.json").exists())
        self.assertTrue((self.root / 'inbox' / f"{second['job_id']}.json").exists())
        self.assertEqual(agent_jobs.read_job(first['job_id'], root=self.root)['_state'], 'running')

    def test_run_job_writes_outbox_and_clears_working(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        canned = agent_jobs._result(claimed, True, 'complete', counts={'total': 3},
                                    highlights=['ok'])
        with patch.object(agent_jobs, '_run_old_audit', return_value=canned):
            result = agent_jobs.run_job(claimed, root=self.root)
        self.assertEqual(result['job_id'], job['job_id'])
        self.assertTrue((self.root / 'outbox' / f"{job['job_id']}.json").exists())
        self.assertFalse((self.root / 'working' / f"{job['job_id']}.json").exists())
        stored = agent_jobs.read_job(job['job_id'], root=self.root)
        self.assertEqual(stored['_state'], 'finished')
        self.assertEqual(stored['counts']['total'], 3)

    def test_run_job_never_raises_and_reports_error(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        with patch.object(agent_jobs, '_run_old_audit', side_effect=RuntimeError('boom')):
            result = agent_jobs.run_job(claimed, root=self.root)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('boom', result['error'])

    def test_pipeline_action_maps_counts_and_waits_for_completion(self):
        job = {'job_id': 'j1', 'action': 'crawl', 'label': '获取游戏清单',
               'year': 2020, 'year_end': 2020, 'dry_run': False}
        states = [
            {'running': True, 'job_id': 'p1', 'pid': os.getpid(), 'updated_at': int(time.time()),
             'done': 1, 'total': 10},
            {'running': False, 'job_id': 'p1', 'outcome': 'complete', 'done': 10, 'total': 10,
             'success': 9, 'failed': 0, 'skipped': 1, 'updated_at': int(time.time())},
        ]
        with patch('tool.pipeline.start', return_value={'status': 'success', 'job_id': 'p1'}), \
             patch('tool.pipeline.status', side_effect=states), \
             patch.object(agent_jobs.time, 'sleep', lambda _s: None):
            result = agent_jobs._run_pipeline_action(job)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['counts']['success'], 9)
        self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(result['pipeline_job_id'], 'p1')

    def test_pipeline_action_reports_start_refusal(self):
        job = {'job_id': 'j2', 'action': 'check', 'label': '校对115',
               'year': 2020, 'year_end': 2020, 'dry_run': False}
        with patch('tool.pipeline.start',
                   return_value={'status': 'error', 'message': '已有任务运行，请等待完成或停止'}):
            result = agent_jobs._run_pipeline_action(job)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('已有任务运行', result['error'])

    def test_run_job_enforces_hard_timeout(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        started = time.time()
        with patch.object(agent_jobs, '_run_old_audit', side_effect=lambda _job, _root=None: time.sleep(30)):
            result = agent_jobs.run_job(claimed, root=self.root, timeout_s=1)
        self.assertLess(time.time() - started, 20)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('超时', result['error'])
        self.assertEqual(agent_jobs.read_job(job['job_id'], root=self.root)['_state'], 'finished')

    def test_stop_archives_cancelled_and_signals_pid(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        claimed['pid'] = 424242
        agent_jobs._update_working(claimed, root=self.root)
        with patch('os.kill') as kill, patch.object(agent_jobs.time, 'sleep', lambda _s: None):
            result = agent_jobs.stop(job['job_id'], root=self.root)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(kill.called)
        stored = agent_jobs.read_job(job['job_id'], root=self.root)
        self.assertEqual(stored['status'], 'cancelled')
        self.assertEqual(stored['_state'], 'finished')

    def test_stop_message_keeps_sigterm_when_process_dies_between_signals(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        claimed = agent_jobs.claim(root=self.root)
        claimed['pid'] = 424242
        agent_jobs._update_working(claimed, root=self.root)
        calls = {'n': 0}

        def fake_kill(_pid, _sig):
            calls['n'] += 1
            if calls['n'] > 1:            # SIGTERM 生效后进程已消失
                raise ProcessLookupError()

        with patch('os.kill', side_effect=fake_kill), \
             patch.object(agent_jobs.time, 'sleep', lambda _s: None):
            result = agent_jobs.stop(job['job_id'], root=self.root)
        self.assertEqual(result['status'], 'success')
        self.assertIn('SIGTERM', result['message'])
        self.assertNotIn('已不存在', result['message'])

    def test_stop_refuses_when_not_running(self):
        job = agent_jobs.enqueue('old_audit', 2020, root=self.root)
        self.assertEqual(agent_jobs.stop(job['job_id'], root=self.root)['status'], 'error')

    def test_timeout_defaults_per_action(self):
        self.assertEqual(agent_jobs.timeout_for('old_audit'), agent_jobs.DEFAULT_TIMEOUTS['old_audit'])
        self.assertEqual(agent_jobs.timeout_for('crawl'), agent_jobs.FALLBACK_TIMEOUT)
        self.assertEqual(agent_jobs.timeout_for('crawl', 7), 7)

    def test_list_jobs_reports_states(self):
        queued = agent_jobs.enqueue('crawl', 2020, root=self.root)
        done = agent_jobs.enqueue('old_audit', 2021, root=self.root)
        claimed = agent_jobs.claim(done['job_id'], root=self.root)
        with patch.object(agent_jobs, '_run_old_audit',
                          return_value=agent_jobs._result(claimed, True, 'complete')):
            agent_jobs.run_job(claimed, root=self.root)
        states = {item['job_id']: item['_state'] for item in agent_jobs.list_jobs(root=self.root)}
        self.assertEqual(states[queued['job_id']], 'queued')
        self.assertEqual(states[done['job_id']], 'finished')


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


if __name__ == '__main__':
    unittest.main()
