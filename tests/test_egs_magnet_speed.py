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


if __name__ == '__main__':unittest.main()
