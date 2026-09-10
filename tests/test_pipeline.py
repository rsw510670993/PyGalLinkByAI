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
    def test_compute_target_name_normalizes_legacy_iso_date_template(self):
        self.assertEqual(
            organize.compute_target_name(
                '2026-05-29', 'Brand', 'Game',
                {'organize_name_format': '[{dn_date}][{company}]{name}'},
                date_code='260529',
            ),
            '[20260529][Brand]Game',
        )

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
        self.stack.enter_context(patch('tool.p115_client.offline_list', return_value={'success':True,'tasks':[]}))
        self.stack.enter_context(patch('time.sleep'))

    def state(self, action, execute=False):
        return dict(action=action, start_year=2026, end_year=2026, month=1, execute=execute,
                    done=0, total=0, success=0, failed=0, skipped=0, results=[])

    def run_job(self, action, execute=False, stop=lambda:False):
        state = self.state(action, execute)
        pipeline.execute_job(state, lambda:None, stop)
        return state

    @contextlib.contextmanager
    def exact_location(self, location):
        """Expose a test folder through an exact-infohash mapping, not title search."""
        match = {
            'source': 'egs', 'infohash_hex': 'a' * 40,
            'cid': location['cid'], 'pid': location.get('pid'),
            'pick_code': location.get('pick_code'), 'name': location.get('name'),
            'folder_path': (
                location.get('parent_path', '').rstrip('/') + '/' + location.get('name', '')
            ),
        }
        with patch.object(organize, 'find_downloaded_folder_by_infohash', return_value=match), \
             patch.object(organize, 'get_item_name', return_value=location.get('name')), \
             patch.object(organize, 'parent_crumbs_path', return_value=location.get('parent_path')):
            yield

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
            self.assertTrue(all(c.kwargs.get('strict_infohash') for c in check.call_args_list))
            self.assertTrue(all(c.kwargs.get('offline_tasks') == [] for c in check.call_args_list))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT downloaded FROM egs_games WHERE egs_id=4').fetchone()[0],0)
            self.assertEqual(
                conn.execute(
                    'SELECT downloaded,submitted_115 FROM egs_games WHERE egs_id=1'
                ).fetchone(), (1, 1),
            )

    def test_calendar_excludes_duplicate_and_submission_excluded_from_magnet_total(self):
        from tool import cli
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE egs_games SET downloaded=1,submitted_115=1 WHERE egs_id=1')
            conn.execute('UPDATE egs_games SET submission_excluded=1 WHERE egs_id=2')
            conn.execute('UPDATE egs_games SET magnet_duplicate=1,duplicate_of_egs_id=1 WHERE egs_id=3')
            conn.commit()
        with patch.object(cli, '_print') as emit:
            cli.cmd_calendar(type('Args', (), {'year': 2026})())
        payload = emit.call_args.args[0]
        jan = next(y for y in payload['years'] if y['year'] == 2026)['months'][0]
        self.assertEqual((jan['magnet_total'], jan['magnet_submitted']), (1, 1))
        self.assertTrue(jan['all_magnet_submitted'])

    def test_same_infohash_reuses_folder_but_different_magnet_does_not(self):
        legacy_db = str(Path(self.temp.name) / 'getchu.db')
        legacy = sqlite3.connect(legacy_db)
        legacy.executescript('''
            CREATE TABLE getchu_games (
                date TEXT, name TEXT, link TEXT, infohash_hex TEXT, downloaded INTEGER
            );
            CREATE TABLE getchu_115_folders (
                date TEXT, name TEXT, cid TEXT, pid TEXT, pick_code TEXT,
                folder_name TEXT, folder_path TEXT
            );
        ''')
        legacy.execute(
            'INSERT INTO getchu_games VALUES (?,?,?,?,?)',
            ('2025-01', 'Legacy', MAGNET, None, 1),
        )
        legacy.execute(
            'INSERT INTO getchu_115_folders VALUES (?,?,?,?,?,?,?)',
            ('2025-01', 'Legacy', 'legacy-cid', 'legacy-pid', 'pick',
             'Legacy Folder', '/GAL/GAL-2025/Legacy Folder'),
        )
        legacy.commit()
        legacy.close()

        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        match = organize.adopt_downloaded_folder_by_infohash(
            conn, '2026-01', 'Game1', MAGNET,
            getchu_db_path=legacy_db, verify_remote=False,
        )
        self.assertEqual((match['source'], match['cid']), ('getchu', 'legacy-cid'))
        self.assertEqual(
            conn.execute(
                'SELECT downloaded,submitted_115 FROM egs_games WHERE egs_id=1'
            ).fetchone(),
            (1, 1),
        )
        different = 'magnet:?xt=urn:btih:' + 'b' * 40 + '&dn=[260101]Game'
        self.assertIsNone(organize.find_downloaded_folder_by_infohash(
            conn, different, stored_infohash='a' * 40, getchu_db_path=legacy_db,
        ))

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
        with self.exact_location(location), patch.object(organize,'read_config',return_value={}):
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
            with self.exact_location(location), patch.object(organize,'read_config',return_value={}), patch.object(organize,'resolve_cid',return_value='12'), patch.object(organize,'list_dir_children_names',return_value=children), patch.object(organize,'move_item') as move:
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
        with self.exact_location(location), patch.object(organize,'read_config',return_value={}), patch.object(organize,'resolve_cid',return_value='12'), patch.object(organize,'list_dir_children_names',return_value=[]), patch.object(organize,'rename_item',return_value={'success':True}) as rename, patch.object(organize,'get_item_name',side_effect=['OldName',target]), patch.object(organize,'move_item',return_value={'success':True}) as move, patch.object(organize,'parent_crumbs_path',side_effect=['/Old','/GAL/GAL-2026']):
            result=organize.organize_single('2026-01','Game1',dry_run=False,conn=conn)
        self.assertEqual(result['status'],'renamed_moved')
        rename.assert_called_once_with('123',target)
        move.assert_called_once_with('123','12')
        self.assertEqual(conn.execute('SELECT downloaded,submitted_115 FROM egs_games WHERE egs_id=1').fetchone(),(1,1))
        self.assertIn('/Old/OldName',conn.execute('SELECT payload FROM egs_115_operations').fetchone()[0])

    def test_organizer_cross_year_move_requires_confirmation(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        location=dict(cid='123',pid='5',name='[20240101][Brand]Game1',
                      parent_path='/GAL/GAL-2024',is_dir=True)
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            result=organize.organize_single('2026-01','Game1',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'cross_year_confirm')
        self.assertTrue(result['requires_confirmation'])
        self.assertEqual(result['source_year'],2024)
        self.assertEqual(result['target_year'],2026)

    def test_cross_year_rejection_is_scoped_to_candidate_cid(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        location=dict(cid='old-123',pid='5',name='[20240101][Brand]Game1',
                      parent_path='/GAL/GAL-2024',is_dir=True)
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            first=organize.organize_single('2026-01','Game1',dry_run=True,conn=conn)
        organize.record_organize_issue(
            conn, '2026-01', 'Game1', first['status'], executed=False,
            detail=first, egs_id=1,
        )
        issue_id=conn.execute('SELECT id FROM egs_organize_issues WHERE resolved=0').fetchone()[0]
        self.assertTrue(organize.reject_organize_issue(conn,issue_id)['success'])
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            second=organize.organize_single('2026-01','Game1',dry_run=True,conn=conn)
        self.assertEqual(second['status'],'cross_year_rejected')
        self.assertEqual(organize.organize_report_outcome(second['status']),'skipped')

    def test_normal_result_closes_stale_ambiguous_issue(self):
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_issue_schema(conn)
        conn.execute(
            """INSERT INTO egs_organize_issues
               (egs_id,date,name,status,outcome,message,detail,run_at,resolved)
               VALUES (1,'2026-01','Game1','ambiguous','failed','old','{}','2026-01-01',0)"""
        )
        conn.commit()
        organize.record_organize_issue(
            conn, '2026-01', 'Game1', 'not_downloaded', executed=False,
            detail={'status': 'not_downloaded'}, egs_id=1,
        )
        self.assertEqual(
            conn.execute('SELECT resolved FROM egs_organize_issues').fetchone()[0], 1
        )

    def test_torrent_info_name_blocks_short_title_fallback(self):
        wrong = [
            {'cid': '1', 'pid': '10', 'fc': 0, 'n': '[241229][LunaSystem] 七ヶ音学園旅行部'},
            {'cid': '2', 'pid': '10', 'fc': 0, 'n': '[211224] [Key] LUNARiA -Virtualized Moonchild-'},
        ]
        torrent_name = '[WorkNite Games] LUNA v1.2.056'
        with patch.object(organize, 'search_files', return_value=wrong):
            result = organize.locate_by_search(
                '[WorkNite Games] LUNA [English] [Uncensored]',
                'LUNA', torrent_name=torrent_name,
            )
        self.assertIsNone(result)

        exact = {'cid': '3', 'pid': '10', 'fc': 0, 'n': torrent_name}
        with patch.object(organize, 'search_files', return_value=wrong + [exact]), \
             patch.object(organize, 'parent_crumbs_path', return_value='/Downloads'):
            result = organize.locate_by_search(
                '[WorkNite Games] LUNA [English] [Uncensored]',
                'LUNA', torrent_name=torrent_name,
            )
        self.assertEqual(result['cid'], '3')
        self.assertFalse(result['ambiguous'])

    def test_duplicate_magnet_is_not_an_organize_issue(self):
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        conn.execute(
            'UPDATE egs_games SET magnet_duplicate=1,duplicate_of_egs_id=2 WHERE egs_id=1'
        )
        conn.commit()

        with patch.object(organize, 'locate_by_search') as locate:
            result = organize.organize_single('2026-01', 'Game1', dry_run=True, conn=conn)
        self.assertEqual(result['status'], 'duplicate_magnet')
        self.assertIn('不应提交', result['message'])
        locate.assert_not_called()

        organize.ensure_issue_schema(conn)
        conn.execute(
            """INSERT INTO egs_organize_issues
               (egs_id,date,name,status,outcome,message,detail,run_at,resolved)
               VALUES (1,'2026-01','Game1','shared_cid','failed','old','{}','2026-01-01',0)"""
        )
        conn.execute(
            """INSERT INTO egs_organize_issues
               (egs_id,date,name,status,outcome,message,detail,run_at,resolved)
               VALUES (4,'2026-02','Game4','shared_cid','failed','old','{}','2026-01-01',0)"""
        )
        conn.commit()
        listed = organize.list_organize_issues(conn, include_resolved=False)
        self.assertEqual(listed['counts']['open'], 0)
        excluded = conn.execute(
            'SELECT submission_excluded,submission_excluded_reason FROM egs_games WHERE egs_id=4'
        ).fetchone()
        self.assertEqual(excluded, (1, 'shared_cid'))

    def test_names_match_rejects_unbounded_predecessor(self):
        from tool.p115_client import _names_match, _normalize_for_comparison
        dn=_normalize_for_comparison('[260227][Cuteuphoria] ドラコンカフェ2')
        old=_normalize_for_comparison('[241129][Cuteuphoria] ドラコンカフェ')
        same=_normalize_for_comparison('[260227][Cuteuphoria] ドラコンカフェ2')
        self.assertFalse(_names_match(dn, old))
        self.assertTrue(_names_match(dn, same))

    def test_names_match_rejects_numbered_sequel_after_removed_punctuation(self):
        from tool.p115_client import _names_match, _normalize_for_comparison
        sequel = _normalize_for_comparison(
            '[251128] [Whirlpool] 猫忍えくすはーとSPIN！ 2 通常版')
        predecessor = _normalize_for_comparison(
            '[230928] [Whirlpool] 猫忍えくすはーとSPIN！ + Bonus')
        self.assertFalse(_names_match(sequel, predecessor))

    def test_names_match_rejects_ascii_style_suffix_on_old_title(self):
        from tool.p115_client import _names_match, _normalize_for_comparison
        remake = _normalize_for_comparison(
            '[260327] [CLIP☆CRAFT] ユニオリズム・カルテット B2-STYLE + Mini Drama')
        original = _normalize_for_comparison(
            '[141226] [CLIP☆CRAFT] ユニオリズム・カルテット')
        self.assertFalse(_names_match(remake, original))

        generic = _normalize_for_comparison('style')
        self.assertFalse(_names_match(remake, generic))

    def test_month_shift_prefers_magnet_dn_and_requires_confirmation(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (10,'PC','2025-12-26','Game10','Brand','2025-12','Game10','Brand','2025-12-26',?)""",
                     ('magnet:?xt=urn:btih:' + 'b'*40 + '&dn=%5B260227%5D%20%5BBrand%5D%20Game10',))
        conn.commit()
        location=dict(cid='123',pid='5',name='[2026-02-27][Brand]Game10',
                      parent_path='/GAL/GAL-2026',is_dir=True)
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            result=organize.organize_single('2025-12','Game10',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'month_shift_confirm')
        self.assertTrue(result['requires_confirmation'])
        self.assertEqual(result['confirmation_kind'],'month_shift')
        self.assertEqual(result['dn_date'],'2026-02-27')
        self.assertEqual(result['target_name'],'[20260227][Brand]Game10')
        self.assertEqual(result['proposed_actual_release_month'],'2026-02')
        self.assertIsNone(conn.execute('SELECT actual_release_ts FROM egs_games WHERE egs_id=10').fetchone()[0])

    def test_month_shift_rejection_is_persistent(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (10,'PC','2025-12-26','Game10','Brand','2025-12','Game10','Brand','2025-12-26',?)""",
                     ('magnet:?xt=urn:btih:' + 'b'*40 + '&dn=%5B260227%5D%20%5BBrand%5D%20Game10',))
        conn.commit()
        location=dict(cid='123',pid='5',name='[20260227][Brand]Game10',parent_path='/GAL/GAL-2026',is_dir=True)
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            first=organize.organize_single('2025-12','Game10',dry_run=True,conn=conn)
        self.assertEqual(first['status'],'month_shift_confirm')
        organize.record_organize_issue(
            conn, '2025-12', 'Game10', first['status'], executed=False,
            detail=first, egs_id=10,
        )
        issue_id=conn.execute('SELECT id FROM egs_organize_issues WHERE resolved=0').fetchone()[0]
        rejected=organize.reject_organize_issue(conn,issue_id)
        self.assertTrue(rejected['success'])
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            second=organize.organize_single('2025-12','Game10',dry_run=True,conn=conn)
        self.assertEqual(second['status'],'month_shift_rejected')
        self.assertEqual(organize.organize_report_outcome(second['status']),'skipped')

    def test_month_shift_confirmation_updates_display_and_actual(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        target='[20260227][Brand]Game10'
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (10,'PC','2025-12-26','Game10','Brand','2025-12','Game10','Brand','2025-12-26',?)""",
                     ('magnet:?xt=urn:btih:' + 'b'*40 + '&dn=%5B260227%5D%20%5BBrand%5D%20Game10',))
        conn.commit()
        location=dict(cid='123',pid='5',name=target,
                      parent_path='/GAL/GAL-2026',is_dir=True)
        with self.exact_location(location), \
             patch.object(organize,'read_config',return_value={}):
            result=organize.organize_single('2025-12','Game10',dry_run=False,conn=conn,
                                            confirmed_month_shift=True)
        self.assertEqual(result['status'],'found_set_downloaded')
        self.assertIn('month_shift',result['actions'])
        row=conn.execute('SELECT date,release_ts,actual_release_ts FROM egs_games WHERE egs_id=10').fetchone()
        self.assertEqual(row,('2026-02','2026-02-27','2026-02-27'))
        self.assertIsNotNone(conn.execute('SELECT 1 FROM egs_115_folders WHERE date=? AND name=?',('2026-02','Game10')).fetchone())

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

    def test_success_backup_cleanup_only_deletes_pipeline_backup(self):
        root = Path(self.temp.name)
        backup_dir = root / 'db_backups'
        backup_dir.mkdir()
        generated = backup_dir / 'egs.before_pipeline_job.db'
        unrelated = backup_dir / 'manual.db'
        generated.write_bytes(b'db')
        unrelated.write_bytes(b'db')
        with patch.object(pipeline, 'repo_root', return_value=str(root)):
            self.assertTrue(pipeline.cleanup_success_backup(str(generated)))
            self.assertFalse(pipeline.cleanup_success_backup(str(unrelated)))
        self.assertFalse(generated.exists())
        self.assertTrue(unrelated.exists())

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

    def test_download_failed_record_does_not_accept_partial_folder(self):
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        conn.execute('UPDATE egs_games SET download_failed=1 WHERE egs_id=1')
        organize.save_folder_record(
            conn, '2026-01', 'Game1', cid='partial', pid='12',
            folder_name='Partial Game1', target_name='[20260101][Brand]Game1',
        )
        with patch.object(organize, 'get_item_name') as get_name:
            result = organize.organize_single('2026-01', 'Game1', dry_run=False, conn=conn)
        self.assertEqual(result['status'], 'not_downloaded')
        self.assertIn('下载失败', result['message'])
        get_name.assert_not_called()
        self.assertEqual(conn.execute('SELECT downloaded FROM egs_games WHERE egs_id=1').fetchone()[0], 0)

    def test_pending_offline_task_is_not_organized(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        magnet='magnet:?xt=urn:btih:' + 'd'*40 + '&dn=%5B260210%5D%20%5BBrand%5D%20Game20'
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (20,'PC','2026-02-10','Game20','Brand','2026-02','Game20','Brand','2026-02-10',?)""",(magnet,))
        conn.commit()
        task={'info_hash':'d'*40,'url':magnet,'percentDone':50,'display_status':'downloading'}
        with patch.object(organize,'locate_by_search',return_value=None), \
             patch.object(organize,'locate_in_year_dir',return_value=None), \
             patch.object(organize,'resolve_cid',return_value=0), \
             patch.object(organize,'read_config',return_value={}), \
             patch('tool.p115_client.offline_list',return_value={'success':True,'tasks':[task]}):
            result=organize.organize_single('2026-02','Game20',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'in_offline')

    def test_completed_offline_single_file_auto_folder_is_renamed(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        magnet='magnet:?xt=urn:btih:' + 'd'*40 + '&dn=%5B260210%5D%20%5BBrand%5D%20Game20'
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (20,'PC','2026-02-10','Game20','Brand','2026-02','Game20','Brand','2026-02-10',?)""",(magnet,))
        conn.commit()
        task={'info_hash':'d'*40,'url':magnet,'percentDone':100,'display_status':'finished',
              'file_id':'999','name':'RJ01557970.zip','wp_path_id':'5'}
        # 115 单文件离线任务返回的是自动创建的 .zip 同名目录；目录内才是 fid 文件。
        info={'cid':'999','pid':'5','n':'RJ01557970.zip','fc':0,'pc':'pick','is_dir':True}
        with patch.object(organize,'locate_by_search',return_value=None), \
             patch.object(organize,'locate_in_year_dir',return_value=None), \
             patch.object(organize,'resolve_cid',return_value=0), \
             patch.object(organize,'read_config',return_value={}), \
             patch.object(organize,'get_item_info',return_value=info), \
             patch.object(organize,'parent_crumbs_path',return_value='/GAL/GAL-2026'), \
             patch('tool.p115_client.offline_list',return_value={'success':True,'tasks':[task]}):
            result=organize.organize_single('2026-02','Game20',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'would_rename')
        self.assertEqual(result['old_name'],'RJ01557970.zip')
        self.assertEqual(result['target_path'],'/GAL/GAL-2026/[20260210][Brand]Game20')

    def test_completed_offline_direct_file_is_wrapped(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        magnet='magnet:?xt=urn:btih:' + 'd'*40 + '&dn=%5B260210%5D%20%5BBrand%5D%20Game20'
        conn.execute("""INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link)
                        VALUES (20,'PC','2026-02-10','Game20','Brand','2026-02','Game20','Brand','2026-02-10',?)""",(magnet,))
        conn.commit()
        task={'info_hash':'d'*40,'url':magnet,'percentDone':100,'display_status':'finished',
              'file_id':'888','name':'RJ01557970.zip','wp_path_id':'5'}
        info={'cid':'888','pid':'5','n':'RJ01557970.zip','fc':1,'pc':'pick',
              'fid':'888','is_dir':False}
        with patch.object(organize,'resolve_cid',return_value=0), \
             patch.object(organize,'read_config',return_value={}), \
             patch.object(organize,'get_item_info',return_value=info), \
             patch.object(organize,'parent_crumbs_path',return_value='/GAL/GAL-2026'), \
             patch('tool.p115_client.offline_list',return_value={'success':True,'tasks':[task]}):
            result=organize.organize_single('2026-02','Game20',dry_run=True,conn=conn)
        self.assertEqual(result['status'],'would_wrap_file')
        self.assertEqual(result['cid'],'888')

    def test_list_dir_children_uses_fid_for_files_and_cid_for_directories(self):
        class Client:
            def fs_files(self, _payload):
                return {'data': [
                    {'cid':'dir-1','pid':'parent','n':'folder.zip','fc':0,'pc':'dir-pick'},
                    {'fid':'file-1','cid':'parent','n':'payload.zip','fc':1,'pc':'file-pick'},
                ]}
        with patch.object(organize,'_load_client',return_value=Client()), \
             patch('tool.p115_client._import_p115client',return_value=(None,lambda value:value)):
            items=organize.list_dir_children('parent')
        self.assertEqual(items[0]['cid'],'dir-1')
        self.assertTrue(items[0]['is_dir'])
        self.assertEqual(items[1]['cid'],'file-1')
        self.assertEqual(items[1]['pid'],'parent')
        self.assertFalse(items[1]['is_dir'])

    def test_old_double_wrapped_single_file_is_safely_flattened(self):
        conn=sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        organize.ensure_folder_schema(conn)
        target='[20260101][Brand]Game1'
        conn.execute('UPDATE egs_games SET torrent_name=? WHERE egs_id=1',('payload.zip',))
        organize.save_folder_record(
            conn,'2026-01','Game1',cid='outer',pid='year',folder_name=target,
            folder_path='/GAL/GAL-2026/'+target,target_name=target,status='wrapped_file',
        )
        outer=[{'cid':'inner','pid':'outer','n':'payload.zip','fc':0,'is_dir':True}]
        payload=[{'cid':'file-fid','pid':'inner','n':'payload.zip','fc':1,
                  'fid':'file-fid','is_dir':False}]
        with patch.object(organize,'get_item_name',return_value=target), \
             patch.object(organize,'parent_crumbs_path',return_value='/GAL/GAL-2026'), \
             patch.object(organize,'read_config',return_value={}), \
             patch.object(organize,'list_dir_children',side_effect=[outer,payload]), \
             patch.object(organize,'move_item') as move, \
             patch.object(organize,'delete_item') as delete:
            preview=organize.organize_single('2026-01','Game1',dry_run=True,conn=conn)
        self.assertEqual(preview['status'],'would_repair_double_wrap')
        move.assert_not_called()
        delete.assert_not_called()

        with patch.object(organize,'get_item_name',return_value=target), \
             patch.object(organize,'parent_crumbs_path',return_value='/GAL/GAL-2026'), \
             patch.object(organize,'read_config',return_value={}), \
             patch.object(organize,'list_dir_children',side_effect=[outer,payload,[]]), \
             patch.object(organize,'move_item',return_value={'success':True}) as move, \
             patch.object(organize,'delete_item',return_value={'success':True}) as delete:
            result=organize.organize_single('2026-01','Game1',dry_run=False,conn=conn)
        self.assertEqual(result['status'],'repaired_double_wrap')
        move.assert_called_once_with('file-fid','outer')
        delete.assert_called_once_with('inner')
        self.assertEqual(
            conn.execute("SELECT status FROM egs_115_folders WHERE date='2026-01' AND name='Game1'").fetchone()[0],
            'repaired_double_wrap',
        )

    def test_scope_validation(self):
        for args in [('other',2026,2026,0),('crawl',2026,2025,0),('check',2026,2026,13)]:
            with self.assertRaises(ValueError):pipeline.validate(*args)

    def test_date_priority_and_invalid_date(self):
        self.assertEqual(organize.resolve_dn_timestamp(MAGNET,'2026-02-03')[0],'2026-01-01')
        self.assertEqual(organize.resolve_dn_timestamp('magnet:?xt=urn:btih:' + 'c'*40,'2026-02-03')[0],'2026-02-03')


if __name__ == '__main__':
    unittest.main()


class NormalizeRegressionTests(unittest.TestCase):
    def test_normalize_keeps_ascii_plus_inside_company_bracket(self):
        from tool.p115_client import _normalize_for_comparison
        # 公司名里的 +1 不能被当作追加段截断（曾把 [あざらしそふと+1] 截成 [あざらしそふと）
        s = _normalize_for_comparison('[20260327][あざらしそふと+1]あまねぇ -幼馴染お姉ちゃん')
        self.assertIn('あまねぇ', s)
        self.assertIn('+1', s)
