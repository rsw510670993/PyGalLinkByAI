import tempfile
import unittest
from pathlib import Path

from tool import ai_organizer


class AiOrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "egs.db")

    def test_create_list_detail_and_cancel_job(self):
        job = ai_organizer.create_job(
            "处理 2022 年整理待办", scope_year=2022, db_path=self.db
        )
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["scope_year"], 2022)
        self.assertEqual(job["events"][0]["event_type"], "job.queued")

        listing = ai_organizer.list_jobs(db_path=self.db)
        self.assertEqual(listing["counts"], {"queued": 1})
        self.assertEqual(listing["jobs"][0]["job_id"], job["job_id"])
        self.assertNotIn("prompt", listing["jobs"][0])

        stopped = ai_organizer.request_cancel(job["job_id"], db_path=self.db)
        self.assertTrue(stopped["success"])
        detail = ai_organizer.get_job(job["job_id"], db_path=self.db)
        self.assertTrue(detail["cancel_requested"])
        self.assertEqual(detail["events"][-1]["event_type"], "job.cancel_requested")

    def test_only_one_active_job_is_allowed(self):
        ai_organizer.create_job("first", db_path=self.db)
        with self.assertRaisesRegex(RuntimeError, "已有 AI 任务"):
            ai_organizer.create_job("second", db_path=self.db)

    def test_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            ai_organizer.create_job(" ", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "年份范围无效"):
            ai_organizer.create_job("task", scope_year=1800, db_path=self.db)
        with self.assertRaisesRegex(ValueError, "不支持"):
            ai_organizer.create_job("task", driver="unknown", db_path=self.db)

    def test_start_refuses_unavailable_worker_login_before_writing_job(self):
        from unittest.mock import patch
        with patch.object(ai_organizer, "driver_status", return_value={
            "available": True, "logged_in": False, "message": "Not logged in"
        }):
            with self.assertRaisesRegex(RuntimeError, "尚未登录"):
                ai_organizer.start_job("task", db_path=self.db)
        self.assertEqual(ai_organizer.list_jobs(db_path=self.db)["jobs"], [])

    def test_structured_result_is_written_to_decision_and_action_audit(self):
        job = ai_organizer.create_job("audit", db_path=self.db)
        conn = ai_organizer.open_egs_db(self.db)
        self.addCleanup(conn.close)
        ai_organizer.store_result_audit(conn, job["job_id"], {
            "outcome": "partial",
            "requires_human": True,
            "evidence": ["infohash matched"],
            "actions": [{"action": "move", "target": "cid:1", "result": "verified"}],
        })
        decision = conn.execute(
            "SELECT decision,requires_human,status FROM egs_ai_decisions WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        self.assertEqual(tuple(decision), ("partial", 1, "recorded"))
        action = conn.execute(
            "SELECT action_type,status FROM egs_ai_actions WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        self.assertEqual(tuple(action), ("move", "reported"))

    def test_year_preview_and_scoped_candidate_job(self):
        conn = ai_organizer.open_egs_db(self.db)
        ai_organizer.ensure_egs_schema(conn)
        conn.execute(
            """INSERT INTO egs_games
               (egs_id,model,egs_date,egs_name,egs_company,date,name,company,
                release_ts,link,infohash_hex,downloaded,submitted_115)
               VALUES (1,'PC','2022-01-01','Needs AI','Studio','2022-01',
                       'Needs AI','Studio','2022-01-01','magnet:?xt=urn:btih:abc',
                       'abc',1,1)"""
        )
        conn.execute(
            """INSERT INTO egs_games
               (egs_id,model,egs_date,egs_name,egs_company,date,name,company,
                release_ts,link,infohash_hex,downloaded,submitted_115)
               VALUES (2,'PC','2021-01-01','Other Year','Studio','2021-01',
                       'Other Year','Studio','2021-01-01','magnet:?xt=urn:btih:def',
                       'def',1,1)"""
        )
        conn.commit()
        conn.close()

        preview = ai_organizer.year_preview(2022, db_path=self.db)
        self.assertEqual(preview["counts"]["needs_ai"], 1)
        self.assertEqual(preview["candidates"][0]["egs_id"], 1)

        job = ai_organizer.create_job(
            "只处理页面选择", scope_year=2022, scope_egs_ids=[1], db_path=self.db
        )
        self.assertEqual(job["scope_egs_ids"], [1])
        with self.assertRaisesRegex(ValueError, "不属于 2022 年"):
            ai_organizer.create_job(
                "错误年份", scope_year=2022, scope_egs_ids=[2], db_path=self.db
            )


if __name__ == "__main__":
    unittest.main()
