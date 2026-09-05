"""Offline regression checks for the unified EGS dashboard."""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool import egs_core, egs_organize as organize, pipeline

MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40 + '&dn=[260101]Game'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'egs.db')
        conn = egs_core.open_egs_db(self.db)
        egs_core.ensure_egs_schema(conn)
        for ident, date, downloaded, submitted in [(1,'2026-01',0,0),(2,'2026-01',0,1),(3,'2026-01',1,0),(4,'2026-02',0,0),(5,'2025-01',0,0)]:
            conn.execute('INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link,downloaded,submitted_115) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (ident,'PC',date+'-01',f'Game{ident}','Brand',date,f'Game{ident}','Brand',date+'-01',MAGNET,downloaded,submitted))
        conn.commit()
        conn.close()
        original = egs_core.open_egs_db
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(egs_core, 'open_egs_db', side_effect=lambda: original(self.db)))
        self.stack.enter_context(patch('tool.p115_client.get_login_status', return_value={'logged_in':True}))
        self.stack.enter_context(patch('time.sleep'))

    def state(self, action, execute=False):
        return dict(action=action, start_year=2026, end_year=2026, month=1, execute=execute,
                    done=0, total=0, success=0, failed=0, skipped=0, results=[])

    def run_job(self, action, execute=False, stop=lambda:False):
        state = self.state(action, execute)
        pipeline.execute_job(state, lambda:None, stop)
        return state

    def test_submit_scope_and_repeat(self):
        with patch.object(organize,'resolve_cid',return_value=12), patch('tool.p115_client.offline_submit',return_value={'success':True,'pick_code':'pick'}) as submit:
            state = self.run_job('submit')
            self.assertEqual(state['success'],1)
            submit.assert_called_once_with(MAGNET,'/GAL/GAL-2026')
            self.assertEqual(self.run_job('submit')['total'],0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT submitted_115 FROM egs_games WHERE egs_id=4').fetchone()[0],0)

    def test_submit_missing_directory_does_not_submit(self):
        with patch.object(organize,'resolve_cid',return_value=0), patch.object(organize,'mkdir_year_dir',return_value=None), patch('tool.p115_client.offline_submit') as submit:
            self.assertEqual(self.run_job('submit')['failed'],1)
            submit.assert_not_called()

    def test_check_scope_and_idempotency(self):
        with patch('tool.cli._check_magnet_exists_with_timeout',return_value=({'exists':True,'infohash_hex':'a'*40},None)) as check:
            self.assertEqual(self.run_job('check')['success'],2)
            self.assertEqual(check.call_count,2)
            self.assertEqual(self.run_job('check')['total'],0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT downloaded FROM egs_games WHERE egs_id=4').fetchone()[0],0)

    def test_stop_before_cloud_calls(self):
        with patch('tool.cli._check_magnet_exists_with_timeout') as check:
            self.assertEqual(self.run_job('check',stop=lambda:True)['done'],0)
            check.assert_not_called()

    def test_crawl_uses_selected_month_only(self):
        with patch.object(egs_core,'fetch_egs_month',return_value=[]) as fetch:
            self.assertEqual(self.run_job('crawl')['success'],1)
            fetch.assert_called_once_with(2026,1)

    def test_magnet_propagates_stop_and_errors(self):
        with patch('tool.egs_magnet.run_magnet',return_value={'stopped':True,'total':1,'selected':0,'no_result':0,'low_score':0,'error':0}) as magnet:
            self.assertEqual(self.run_job('magnet')['done'],0)
            self.assertEqual(magnet.call_args.kwargs['month'],1)
            self.assertTrue(callable(magnet.call_args.kwargs['should_stop']))

    def test_login_failure_no_cloud_action(self):
        with patch('tool.p115_client.get_login_status',return_value={'logged_in':False}), patch('tool.p115_client.offline_submit') as submit:
            with self.assertRaisesRegex(RuntimeError,'未登录'):
                self.run_job('submit')
            submit.assert_not_called()

    def test_organize_preview_and_execute_flag(self):
        with patch.object(organize,'organize_single',return_value={'status':'would_rename','message':'preview'}) as single:
            state=self.run_job('organize')
            self.assertEqual(state['total'],3)
            self.assertTrue(all(call.kwargs['dry_run'] for call in single.call_args_list))
            single.reset_mock()
            self.run_job('organize',execute=True)
            self.assertTrue(all(not call.kwargs['dry_run'] for call in single.call_args_list))

    def test_organizer_preview_does_not_restore_submitted(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        target='[20260101][Brand]Game3'
        location=dict(cid='123',pid='12',name=target,parent_path='/GAL/GAL-2026',is_dir=True)
        with patch.object(organize,'locate_by_search',return_value=location), patch.object(organize,'read_config',return_value={}):
            result=organize.organize_single('2026-01','Game3',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'would_set_downloaded')
        self.assertEqual(conn.execute('SELECT submitted_115 FROM egs_games WHERE egs_id=3').fetchone()[0],0)
        self.assertEqual(conn.execute('SELECT count(*) FROM egs_115_folders').fetchone()[0],0)

    def test_organizer_conflict_and_failed_listing_do_not_move(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        target='[20260101][Brand]Game1'
        location=dict(cid='123',pid='5',name=target,parent_path='/Old',is_dir=True)
        for children,expected in [([target],'conflict'),(None,'error')]:
            with patch.object(organize,'locate_by_search',return_value=location), patch.object(organize,'read_config',return_value={}), patch.object(organize,'resolve_cid',return_value='12'), patch.object(organize,'list_dir_children_names',return_value=children), patch.object(organize,'move_item') as move:
                result=organize.organize_single('2026-01','Game1',dry_run=False,conn=conn)
                self.assertEqual(result['status'],expected)
                move.assert_not_called()
            conn.execute('DELETE FROM egs_115_folders');conn.commit()

    def test_organizer_execute_records_original_and_updates_status(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        target='[20260101][Brand]Game1'
        location=dict(cid='123',pid='5',name='OldName',parent_path='/Old',is_dir=True)
        with patch.object(organize,'locate_by_search',return_value=location), patch.object(organize,'read_config',return_value={}), patch.object(organize,'resolve_cid',return_value='12'), patch.object(organize,'list_dir_children_names',return_value=[]), patch.object(organize,'rename_item',return_value={'success':True}) as rename, patch.object(organize,'get_item_name',return_value=target), patch.object(organize,'move_item',return_value={'success':True}) as move, patch.object(organize,'parent_crumbs_path',return_value='/GAL/GAL-2026'):
            result=organize.organize_single('2026-01','Game1',dry_run=False,conn=conn)
        self.assertEqual(result['status'],'renamed_moved')
        rename.assert_called_once_with('123',target)
        move.assert_called_once_with('123','12')
        self.assertEqual(conn.execute('SELECT downloaded,submitted_115 FROM egs_games WHERE egs_id=1').fetchone(),(1,1))
        self.assertIn('/Old/OldName',conn.execute('SELECT payload FROM egs_115_operations').fetchone()[0])

    def test_job_lock_and_stale_stop_isolation(self):
        root=Path(self.temp.name)
        task_paths=(root/'job.json',root/'job.lock',root/'job.stop')
        from tool.runtime import write_json_atomic
        with patch.object(pipeline,'paths',return_value=task_paths):
            with pipeline.locked():
                self.assertEqual(pipeline.start('crawl',2026,2026)['status'],'error')
            write_json_atomic(str(task_paths[0]),dict(running=True,pid=__import__('os').getpid(),job_id='current'))
            self.assertEqual(pipeline.stop('stale')['status'],'error')
            self.assertFalse(task_paths[2].exists())
            self.assertEqual(pipeline.stop('current')['status'],'success')

    def test_worker_persists_result_and_releases_lock(self):
        root=Path(self.temp.name)
        task_paths=(root/'job.json',root/'job.lock',root/'job.stop')
        from tool.runtime import write_json_atomic,read_json
        state=self.state('crawl')
        state.update(job_id='job',running=True)
        write_json_atomic(str(task_paths[0]),state)
        def run(state,save,stop):
            state.update(total=1,done=1,success=1)
        with patch.object(pipeline,'paths',return_value=task_paths), patch.object(pipeline,'backup',return_value='backup.db'), patch.object(pipeline,'execute_job',side_effect=run):
            pipeline.worker('job')
            saved=read_json(str(task_paths[0]),{})
            self.assertFalse(saved['running'])
            self.assertEqual(saved['outcome'],'complete')
            self.assertEqual(saved['done'],1)
            with pipeline.locked():
                pass

    def test_scope_validation(self):
        for args in [('other',2026,2026,0),('crawl',2026,2025,0),('check',2026,2026,13)]:
            with self.assertRaises(ValueError):pipeline.validate(*args)

    def test_date_priority_and_invalid_date(self):
        self.assertEqual(organize.resolve_dn_timestamp(MAGNET,'2026-02-03')[0],'2026-02-03')
        self.assertEqual(organize.resolve_dn_timestamp(MAGNET,'2026-02-30')[0],'2026-01-01')


if __name__ == '__main__':
    unittest.main()
