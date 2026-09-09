import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from tool import egs_core, egs_magnet as magnet

LOG = logging.getLogger('test_nyaa')
HASH = 'a' * 40
LINK = 'magnet:?xt=urn:btih:' + HASH
GAME = dict(name='Example Game!', company='Studio', date='2026-01', release_date='2026-01-01')


class Clock:
    def __init__(self): self.now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


def response(code=200, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = b''
    result.headers.update(headers or {})
    return result


class MagnetSpeedTests(unittest.TestCase):
    def setUp(self):
        magnet._timeout_count = 0

    def test_request_interval_shared_across_searches_without_final_sleep(self):
        clock = Clock()
        session = requests.Session()
        session._egs_pacer = magnet.RequestPacer(
            interval=2.5, min_interval=2.5, max_interval=2.5, torrent_interval=2.5)
        starts = []
        def fetch(*args, **kwargs):
            starts.append(clock.now)
            clock.now += 1
            return response()
        with patch.object(magnet.time,'monotonic',clock.monotonic), patch.object(magnet.time,'sleep',clock.sleep), patch.object(session,'get',side_effect=fetch), patch.object(magnet,'_parse_result_page',return_value=[]):
            magnet.search_candidates(session,'Game One','Studio',LOG)
            magnet.search_candidates(session,'Game Two','Studio',LOG)
        self.assertEqual(starts,[0,2.5,5,7.5])
        self.assertAlmostEqual(clock.now,8.5)
        self.assertAlmostEqual(session._egs_pacer.metrics['network_seconds'],4)
        self.assertAlmostEqual(session._egs_pacer.metrics['wait_seconds'],4.5)

    def test_pacing_config_overrides_defaults(self):
        with patch.object(magnet, 'read_config', return_value={
                'magnet_request_interval': 1.2,
                'magnet_min_request_interval': 0.8,
                'magnet_max_request_interval': 9.0,
                'magnet_torrent_interval': 0.7}):
            pacer = magnet.RequestPacer()
        self.assertEqual((pacer.interval, pacer.min_interval, pacer.max_interval),
                         (1.2, 0.8, 9.0))
        self.assertAlmostEqual(pacer.torrent_step(), 0.7)

    def test_429_backs_off_then_success_recovers_towards_floor(self):
        pacer = magnet.RequestPacer(interval=1.0, min_interval=0.5, max_interval=8.0,
                                    torrent_interval=0.4)
        pacer.on_rate_limited(0)
        self.assertAlmostEqual(pacer.interval, 2.0)
        pacer.on_rate_limited(0)
        self.assertAlmostEqual(pacer.interval, 4.0)
        pacer.on_rate_limited(0)
        self.assertAlmostEqual(pacer.interval, 8.0)
        pacer.on_rate_limited(0)
        self.assertAlmostEqual(pacer.interval, 8.0)
        self.assertEqual(pacer.metrics['backoffs'], 3)
        for _ in range(magnet.RECOVERY_STREAK * 14):
            pacer.on_success()
        self.assertAlmostEqual(pacer.interval, 0.5)
        self.assertGreaterEqual(pacer.metrics['recoveries'], 1)

    def test_torrent_step_shorter_than_search_and_grows_during_backoff(self):
        pacer = magnet.RequestPacer(interval=1.2, min_interval=0.8, max_interval=15.0,
                                    torrent_interval=0.6)
        self.assertAlmostEqual(pacer.torrent_step(), 0.6)
        pacer.interval = 15.0
        self.assertAlmostEqual(pacer.torrent_step(), 7.5)
        pacer.interval = 0.8
        self.assertAlmostEqual(pacer.torrent_step(), 0.6)

    def test_before_request_uses_explicit_step(self):
        clock = Clock()
        pacer = magnet.RequestPacer(interval=5.0, min_interval=1.0, max_interval=10.0)
        with patch.object(magnet.time, 'monotonic', clock.monotonic), \
             patch.object(magnet.time, 'sleep', clock.sleep):
            pacer.before_request()
            pacer.before_request(interval=0.5)
        self.assertAlmostEqual(clock.now, 5.0)
        self.assertAlmostEqual(pacer.next_request_at, 5.5)
        self.assertEqual(pacer.metrics['requests'], 2)

    def test_torrent_429_updates_shared_pacer(self):
        from tool import torrent_meta
        clock = Clock()
        pacer = magnet.RequestPacer(interval=1.0, min_interval=0.5, max_interval=8.0,
                                    torrent_interval=0.6)
        session = requests.Session()
        def fetch(*args, **kwargs):
            clock.now += 0.2
            return response(429, {'Retry-After': '30'})
        with patch.object(torrent_meta.time, 'monotonic', clock.monotonic), \
             patch.object(torrent_meta.time, 'sleep', clock.sleep), \
             patch.object(session, 'get', side_effect=fetch):
            result = torrent_meta.fetch_torrent_meta(
                session, 'https://sukebei.nyaa.si/view/123', pacer=pacer)
        self.assertIsNone(result)
        self.assertEqual(pacer.metrics['backoffs'], 1)
        self.assertAlmostEqual(pacer.interval, 2.0)
        self.assertGreaterEqual(pacer.next_request_at, 30.0)

    def test_maximum_score_stops_and_preserves_best(self):
        candidate=dict(nyaa_title='[girlcelly] [Studio] Example Game!',nyaa_date='2026-01-01 00:00',magnet=LINK,infohash_hex=HASH)
        session=requests.Session();session._egs_pacer=magnet.RequestPacer()
        with patch.object(magnet,'_search_once',return_value=[candidate]) as search:
            result=magnet.search_candidates(session,GAME['name'],GAME['company'],LOG,game=GAME)
        self.assertEqual(search.call_count,1)
        self.assertEqual(result[0]['infohash_hex'],HASH)
        self.assertEqual(result[0]['score'],65)
        self.assertEqual(session._egs_pacer.metrics['early_stops'],1)

    def test_below_maximum_retains_all_original_queries(self):
        candidate=dict(nyaa_title='[Studio] Example Game!',nyaa_date='2026-01-01 00:00',magnet=LINK,infohash_hex=HASH)
        with patch.object(magnet,'_search_once',return_value=[candidate]) as search:
            result=magnet.search_candidates(requests.Session(),GAME['name'],GAME['company'],LOG,game=GAME)
        self.assertEqual(search.call_count,3)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['score'],55)

    def test_confident_exact_company_match_stops_early(self):
        candidate = dict(nyaa_title='[girlcelly] [Studio] Example Game!',
                         nyaa_date='2026-01-20 00:00', magnet=LINK, infohash_hex=HASH)
        session = requests.Session(); session._egs_pacer = magnet.RequestPacer()
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 60}), \
             patch.object(magnet, '_search_once', return_value=[candidate]) as search:
            result = magnet.search_candidates(session, GAME['name'], GAME['company'], LOG, game=GAME)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result[0]['score'], 60)
        self.assertEqual(session._egs_pacer.metrics['early_stops'], 1)
        self.assertEqual(session._egs_pacer.metrics['queries_saved'], 2)

    def test_partial_name_match_does_not_stop_early(self):
        game = dict(name='MainTitleLong - SubtitleXYZ', company='Studio',
                    date='2026-01', release_date='2026-01-01')
        candidate = dict(nyaa_title='[girlcelly] [Studio] MainTitleLong',
                         nyaa_date='2026-01-01 00:00', magnet=LINK, infohash_hex=HASH)
        session = requests.Session(); session._egs_pacer = magnet.RequestPacer()
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 60}), \
             patch.object(magnet, '_search_once', return_value=[candidate]) as search:
            result = magnet.search_candidates(session, game['name'], game['company'], LOG, game=game)
        self.assertEqual(result[0]['score'], 60)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(session._egs_pacer.metrics['early_stops'], 0)

    def test_confident_stop_requires_company_when_known(self):
        candidate = dict(nyaa_title='[girlcelly] Example Game!',
                         nyaa_date='2026-01-01 00:00', magnet=LINK, infohash_hex=HASH)
        session = requests.Session(); session._egs_pacer = magnet.RequestPacer()
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 55}), \
             patch.object(magnet, '_search_once', return_value=[candidate]) as search:
            result = magnet.search_candidates(session, GAME['name'], GAME['company'], LOG, game=GAME)
        self.assertEqual(result[0]['score'], 55)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(session._egs_pacer.metrics['early_stops'], 0)

    def test_early_stop_score_at_max_restores_old_behavior(self):
        candidate = dict(nyaa_title='[girlcelly] [Studio] Example Game!',
                         nyaa_date='2026-01-20 00:00', magnet=LINK, infohash_hex=HASH)
        session = requests.Session(); session._egs_pacer = magnet.RequestPacer()
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 65}), \
             patch.object(magnet, '_search_once', return_value=[candidate]) as search:
            result = magnet.search_candidates(session, GAME['name'], GAME['company'], LOG, game=GAME)
        self.assertEqual(result[0]['score'], 60)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(session._egs_pacer.metrics['early_stops'], 0)

    def test_early_stop_threshold_is_clamped_and_safe(self):
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 200}):
            self.assertEqual(magnet.early_stop_threshold(), magnet.MAX_SCORE)
        with patch.object(magnet, 'read_config', return_value={'magnet_early_stop_score': 'bad'}):
            self.assertEqual(magnet.early_stop_threshold(), magnet.EARLY_STOP_SCORE)

    def test_failed_search_does_not_write_history_or_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn=egs_core.open_egs_db(str(Path(tmp)/'egs.db'))
            try:
                egs_core.ensure_egs_schema(conn);magnet.ensure_egs_magnet_schema(conn)
                conn.execute("INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts) VALUES (1,'PC','2026-01-01','Example Game!','Studio','2026-01','Example Game!','Studio','2026-01-01')")
                conn.commit()
                row=conn.execute('SELECT * FROM egs_games').fetchone()
                with patch.object(magnet,'_search_once',side_effect=RuntimeError('network failed')):
                    with self.assertRaisesRegex(RuntimeError,'network failed'):
                        magnet.process_game(conn,requests.Session(),row,LOG)
                self.assertEqual(conn.execute('SELECT count(*) FROM egs_nyaa_search_log').fetchone()[0],0)
                self.assertEqual(len(magnet.pending_rows(conn,2026,1)),1)
            finally:conn.close()

    def test_stop_interrupts_retry_wait(self):
        clock=Clock();session=requests.Session()
        session._egs_pacer=magnet.RequestPacer(lambda:clock.now >= .4)
        with patch.object(magnet.time,'monotonic',clock.monotonic),patch.object(magnet.time,'sleep',clock.sleep),patch.object(session,'get',side_effect=requests.Timeout()) as get:
            with self.assertRaises(magnet.SearchStopped):magnet._search_once(session,'Game',LOG)
        self.assertEqual(get.call_count,1)
        self.assertLess(clock.now,1)

    def test_429_respects_retry_after_and_does_not_sleep_after_final_failure(self):
        clock=Clock();session=requests.Session()
        with patch.object(magnet.time,'monotonic',clock.monotonic),patch.object(magnet.time,'sleep',clock.sleep),patch.object(session,'get',return_value=response(429,{'Retry-After':'20'})) as get:
            with self.assertRaisesRegex(RuntimeError,'HTTP 429'):magnet._search_once(session,'Game',LOG)
        self.assertEqual(get.call_count,2)
        self.assertAlmostEqual(clock.now,20)
        self.assertEqual(session._egs_pacer.metrics['http_429'],2)
        self.assertGreaterEqual(session._egs_pacer.next_request_at,50)

    def test_readonly_database_fails_before_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'egs.db')
            conn=egs_core.open_egs_db(db)
            egs_core.ensure_egs_schema(conn)
            conn.close()
            readonly=sqlite3.connect('file:'+db+'?mode=ro',uri=True)
            with patch.object(magnet,'open_egs_db',return_value=readonly), patch.object(requests.Session,'get') as get:
                with self.assertRaises(sqlite3.DatabaseError):
                    magnet.run_magnet(2026,month=1,logger=LOG)
            get.assert_not_called()

    def test_no_pending_rows_reports_skips_without_network_or_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'egs.db');conn=egs_core.open_egs_db(db)
            egs_core.ensure_egs_schema(conn);magnet.ensure_egs_magnet_schema(conn)
            for ident,link,release in [(1,LINK,'2026-01-01'),(2,None,'2026-01-01'),(3,None,'2999-01-01')]:
                conn.execute('INSERT INTO egs_games(egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link) VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,'PC',release,str(ident),'Studio','2026-01',str(ident),'Studio',release,link))
            conn.execute('INSERT INTO egs_nyaa_search_log(egs_id) VALUES (2)');conn.commit();conn.close()
            with patch.object(requests.Session,'get') as get,patch.object(magnet.time,'sleep') as sleep:
                result=magnet.run_magnet(2026,month=1,db_path=db,logger=LOG)
            get.assert_not_called();sleep.assert_not_called()
            self.assertEqual((result['total'],result['skip_linked'],result['skip_history'],result['skip_unreleased']),(0,1,1,1))
            self.assertEqual(result['metrics']['requests'],0)


    def test_shared_magnet_keeps_shortest_name_and_recovers_on_new_hash(self):
        other_hash = 'b' * 40
        other_link = 'magnet:?xt=urn:btih:' + other_hash
        with tempfile.TemporaryDirectory() as tmp:
            conn = egs_core.open_egs_db(str(Path(tmp) / 'egs.db'))
            try:
                egs_core.ensure_egs_schema(conn)
                magnet.ensure_egs_magnet_schema(conn)
                for ident, name in ((1, 'A Much Longer Game'), (2, 'Short'), (3, 'Medium Game')):
                    conn.execute(
                        """INSERT INTO egs_games
                           (egs_id,model,egs_date,egs_name,egs_company,date,name,company,
                            release_ts,link,infohash_hex,downloaded,submitted_115)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (ident, 'PC', '2026-01-01', name, 'Studio', '2026-01', name,
                         'Studio', '2026-01-01', LINK, HASH, 1, 1),
                    )
                egs_core.refresh_magnet_duplicates(conn)
                conn.commit()

                states = {
                    row['egs_id']: (row['magnet_duplicate'], row['duplicate_of_egs_id'])
                    for row in conn.execute(
                        'SELECT egs_id,magnet_duplicate,duplicate_of_egs_id FROM egs_games'
                    )
                }
                self.assertEqual(states, {1: (1, 2), 2: (0, None), 3: (1, 2)})
                self.assertEqual([row['egs_id'] for row in magnet.pending_rows(conn, 2026, 1)], [1, 3])

                row = conn.execute('SELECT * FROM egs_games WHERE egs_id=1').fetchone()
                candidate = dict(
                    nyaa_title='[Studio] A Much Longer Game', nyaa_date='2026-01-01 00:00',
                    size='1 GiB', magnet=other_link, infohash_hex=other_hash, view_url=None,
                )
                with patch.object(magnet, 'search_candidates', return_value=[candidate]), \
                     patch.object(magnet, 'select_best', return_value=(candidate, 99, {})), \
                     patch('tool.torrent_meta.fetch_torrent_meta', return_value=None):
                    status, result = magnet.process_game(conn, requests.Session(), row, LOG)

                self.assertEqual(status, 'selected')
                self.assertTrue(result['new_magnet'])
                recovered = conn.execute(
                    'SELECT infohash_hex,magnet_duplicate,duplicate_of_egs_id,downloaded,submitted_115 '
                    'FROM egs_games WHERE egs_id=1'
                ).fetchone()
                self.assertEqual(tuple(recovered), (other_hash, 0, None, 0, 0))
                remaining = conn.execute(
                    'SELECT magnet_duplicate,duplicate_of_egs_id FROM egs_games WHERE egs_id=3'
                ).fetchone()
                self.assertEqual(tuple(remaining), (1, 2))
            finally:
                conn.close()

    def test_newer_dlc_bundle_owns_shared_magnet(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = egs_core.open_egs_db(str(Path(tmp) / "egs.db"))
            try:
                egs_core.ensure_egs_schema(conn)
                for ident, date, release, name in (
                    (1, "2025-09", "2025-09-19", "ドキドキ!性感マッサージ"),
                    (2, "2026-06", "2026-06-28",
                     "ドキドキ!性感マッサージ【タツ姐編+本編カラー化DLC】"),
                ):
                    conn.execute(
                        """INSERT INTO egs_games
                           (egs_id,model,egs_date,egs_name,egs_company,date,name,company,
                            release_ts,link,infohash_hex,downloaded,submitted_115)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (ident, "PC", release, name, "ツインテール教", date, name,
                         "ツインテール教", release, LINK, HASH, 1, 1),
                    )
                egs_core.refresh_magnet_duplicates(conn)
                conn.commit()

                states = {
                    row["egs_id"]: (row["magnet_duplicate"], row["duplicate_of_egs_id"])
                    for row in conn.execute(
                        "SELECT egs_id,magnet_duplicate,duplicate_of_egs_id FROM egs_games"
                    )
                }
                self.assertEqual(states, {1: (1, 2), 2: (0, None)})
            finally:
                conn.close()

if __name__ == "__main__":unittest.main()
