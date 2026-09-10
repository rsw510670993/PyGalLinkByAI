import argparse
import os
import tempfile
import unittest
from unittest.mock import patch

from tool.cli import cmd_115_submit
from tool.egs_core import ensure_egs_schema, open_egs_db


MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"


class Submit115GuardTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = open_egs_db(self.db_path)
        ensure_egs_schema(conn)
        conn.execute(
            """INSERT INTO egs_games
               (egs_id,model,egs_date,egs_name,egs_company,date,name,company,release_ts,link,downloaded)
               VALUES (1,'PC','2025-01-01','Game','Brand','2025-01','Game','Brand','2025-01-01',?,0)""",
            (MAGNET,),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def args(self, magnet=MAGNET):
        return argparse.Namespace(egs_id=1, magnet=magnet, dir="/GAL/GAL-2025", db=self.db_path)

    @patch("tool.cli._print")
    @patch("tool.p115_client.offline_submit")
    def test_downloaded_game_is_not_submitted(self, submit, output):
        conn = open_egs_db(self.db_path)
        conn.execute("UPDATE egs_games SET downloaded=1 WHERE egs_id=1")
        conn.commit()
        conn.close()

        cmd_115_submit(self.args())

        submit.assert_not_called()
        self.assertEqual(output.call_args.args[0]["status"], "blocked")

    @patch("tool.cli._print")
    @patch("tool.p115_client.offline_submit", return_value={"success": True})
    def test_pending_game_with_matching_magnet_is_submitted(self, submit, output):
        cmd_115_submit(self.args())

        submit.assert_called_once_with(MAGNET, "/GAL/GAL-2025")
        output.assert_called_once_with({"success": True})

    @patch("tool.cli._print")
    @patch("tool.p115_client.offline_submit")
    def test_mismatched_magnet_is_not_submitted(self, submit, output):
        cmd_115_submit(self.args(MAGNET + "x"))

        submit.assert_not_called()
        self.assertEqual(output.call_args.args[0]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
