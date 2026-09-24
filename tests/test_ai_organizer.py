import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool import ai_organizer, ai_responses_driver, p115_client


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

        with self.assertRaisesRegex(ValueError, "必须指定年份"):
            ai_organizer.start_job("task", db_path=self.db)

    def test_start_refuses_unavailable_worker_login_before_writing_job(self):
        with patch.object(ai_organizer, "driver_status", return_value={
            "available": True, "logged_in": False, "message": "Not logged in"
        }):
            with self.assertRaisesRegex(RuntimeError, "尚未配置"):
                ai_organizer.start_job("task", scope_year=2022, db_path=self.db)
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
            "human_review": [{
                "review_key": "egs-1-owner",
                "category": "ownership",
                "target": "EGS #1 / CID 1",
                "question": "归给哪条记录？",
                "reason": "存在两个候选",
                "evidence": ["infohash matched"],
                "recommended_action": "归给 EGS #1",
                "alternatives": ["保留在 todo"],
            }],
        })
        decision = conn.execute(
            "SELECT decision,requires_human,status,human_review_json FROM egs_ai_decisions WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        self.assertEqual(tuple(decision[:3]), ("partial", 1, "recorded"))
        self.assertIn("egs-1-owner", decision["human_review_json"])
        action = conn.execute(
            "SELECT action_type,status FROM egs_ai_actions WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        self.assertEqual(tuple(action), ("move", "reported"))

    def test_human_review_schema_and_terminal_state_are_consistent(self):
        import json
        schema = json.loads(
            (Path(__file__).parents[1] / "tool" / "ai_organizer_output.schema.json").read_text()
        )
        self.assertIn("human_review", schema["required"])
        item = schema["properties"]["human_review"]["items"]
        self.assertIn("question", item["required"])
        result = ai_responses_driver._normalize_final_result({
            "outcome": "completed", "actions": [], "human_review": [{"review_key": "x"}],
            "requires_human": False,
        })
        self.assertTrue(result["requires_human"])
        self.assertEqual(result["outcome"], "blocked")

    def test_year_job_has_no_fixed_candidate_ids(self):
        job = ai_organizer.create_job(
            "AI读取115后判断", scope_year=2022, db_path=self.db
        )
        self.assertEqual(job["driver"], "responses")
        self.assertEqual(job["scope_year"], 2022)
        self.assertEqual(job["scope_egs_ids"], [])

    def test_responses_tools_read_live_115_and_enforce_year_scope(self):
        conn = ai_organizer.open_egs_db(self.db)
        self.addCleanup(conn.close)
        ai_organizer.ensure_egs_schema(conn)
        conn.execute(
            """INSERT INTO egs_games
               (egs_id,model,egs_date,egs_name,egs_company,date,name,company,
                release_ts,link,infohash_hex,downloaded,submitted_115)
               VALUES (1,'PC','2022-01-01','Live Match','Studio','2022-01',
                       'Live Match','Studio','2022-01-01','magnet:?xt=urn:btih:abc',
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
        state = {"saw_live_115": False, "known_cids": set(), "previews": {}}

        with patch.object(ai_responses_driver, "list_directory", return_value={
            "success": True, "path": "/GAL/GAL-2022", "total": 1,
            "has_more": False,
            "items": [{"id": "11522", "name": "Live Match", "is_dir": True}],
        }) as mocked:
            result = ai_responses_driver.execute_tool(
                conn, year=2022, name="list_115_path",
                arguments={"path": "/GAL/GAL-2022", "offset": 0, "limit": 100},
                state=state,
            )
        self.assertTrue(result["success"])
        self.assertTrue(state["saw_live_115"])
        self.assertIn("11522", state["known_cids"])
        mocked.assert_called_once_with(path="/GAL/GAL-2022", offset=0, limit=100)
        with self.assertRaisesRegex(ValueError, "连续分页"):
            ai_responses_driver.execute_tool(
                conn, year=2022, name="list_115_path",
                arguments={"path": "/GAL/GAL-2022", "offset": 0, "limit": 100},
                state=state,
            )


        records = ai_responses_driver.execute_tool(
            conn, year=2022, name="list_egs_year",
            arguments={"year": 2022, "offset": 0, "limit": 100}, state=state,
        )
        self.assertEqual([row["egs_id"] for row in records["records"]], [1])
        with self.assertRaisesRegex(ValueError, "不属于任务年份"):
            ai_responses_driver.execute_tool(
                conn, year=2022, name="get_egs_record", arguments={"egs_id": 2},
                state=state,
            )
        with self.assertRaisesRegex(ValueError, "超出任务范围"):
            ai_responses_driver.execute_tool(
                conn, year=2022, name="list_115_path",
                arguments={"path": "/GAL.old", "offset": 0, "limit": 100},
                state=state,
            )

    def test_live_115_directory_listing_is_normalized(self):
        class FakeClient:
            def fs_files(self, payload):
                self.payload = payload
                return {
                    "count": 2,
                    "data": [
                        {"cid": "11", "pid": "99", "n": "Folder"},
                        {"fid": "12", "cid": "99", "n": "file.bin", "s": "3"},
                    ],
                }

        client = FakeClient()
        with patch.object(p115_client, "load_client", return_value=client), patch.object(
            p115_client, "_import_p115client", return_value=(object, lambda value: value)
        ):
            result = p115_client.list_directory(
                path="/GAL/GAL-2022", cid=99, offset=0, limit=100,
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"][0]["id"], "11")
        self.assertTrue(result["items"][0]["is_dir"])
        self.assertEqual(result["items"][1]["id"], "12")
        self.assertFalse(result["items"][1]["is_dir"])
        self.assertEqual(result["items"][1]["parent_id"], "99")


if __name__ == "__main__":
    unittest.main()
