"""外部 ID 映射表与封面匹配放宽的回归测试（2026-11-26）。"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool import egs_extids  # noqa: E402
from tool import egs_thumb  # noqa: E402


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE egs_games (
            egs_id INTEGER PRIMARY KEY, name TEXT, company TEXT, egs_name TEXT,
            egs_company TEXT, date TEXT, getchu_id TEXT, dlsite_id TEXT, dmm TEXT,
            downloaded INTEGER DEFAULT 0, updated_at TEXT, official_url TEXT
        )"""
    )
    egs_extids.ensure_external_id_schema(conn)
    return conn


class ExternalIdTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.conn.execute(
            "INSERT INTO egs_games (egs_id, name, company, date, getchu_id, dlsite_id, dmm) "
            "VALUES (1, 'A', 'C1', '2020-01', '111', NULL, NULL)"
        )
        self.conn.execute(
            "INSERT INTO egs_games (egs_id, name, company, date, getchu_id, dlsite_id, dmm) "
            "VALUES (2, 'B', 'C2', '2020-02', NULL, 'RJ000001', NULL)"
        )
        self.conn.commit()

    def test_backfill_is_idempotent(self):
        first = egs_extids.backfill_from_columns(self.conn)
        self.assertEqual(first["inserted"], 2)
        second = egs_extids.backfill_from_columns(self.conn)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM egs_external_ids").fetchone()[0], 2)

    def test_attach_writes_legacy_only_when_empty(self):
        res = egs_extids.attach_external_id(self.conn, 2, "getchu", "222", origin="manual")
        self.assertTrue(res["legacy_written"])
        row = self.conn.execute(
            "SELECT getchu_id FROM egs_games WHERE egs_id=2").fetchone()
        self.assertEqual(row["getchu_id"], "222")
        # 已有值不覆盖
        res2 = egs_extids.attach_external_id(self.conn, 2, "getchu", "333", origin="manual")
        self.assertFalse(res2["legacy_written"])
        row2 = self.conn.execute(
            "SELECT getchu_id FROM egs_games WHERE egs_id=2").fetchone()
        self.assertEqual(row2["getchu_id"], "222")

    def test_resolve_prefers_confirmed(self):
        egs_extids.set_external_id(self.conn, 1, "dlsite", "RJ9", status="candidate")
        egs_extids.set_external_id(self.conn, 2, "dlsite", "RJ9", status="confirmed")
        self.assertEqual(egs_extids.resolve_egs_id(self.conn, "dlsite", "RJ9"), 2)

    def test_audit_reports_missing_mapping(self):
        result = egs_extids.audit(self.conn)
        self.assertEqual(result["missing_mapping"], 2)
        self.assertEqual(result["mapping_only"], 0)
        egs_extids.backfill_from_columns(self.conn)
        self.assertEqual(egs_extids.audit(self.conn)["missing_mapping"], 0)

    def test_audit_detects_conflict_and_orphan(self):
        egs_extids.set_external_id(self.conn, 1, "getchu", "111")
        egs_extids.set_external_id(self.conn, 1, "getchu", "999")
        egs_extids.set_external_id(self.conn, 999, "getchu", "888")
        result = egs_extids.audit(self.conn)
        self.assertEqual(result["conflict_total"], 1)
        self.assertEqual(result["orphan"], 1)

    def test_read_columns_coalesce_mapping_first(self):
        egs_extids.set_external_id(self.conn, 2, "getchu", "777")
        cols = egs_extids.external_id_columns()
        row = self.conn.execute(
            f"SELECT egs_id, {cols} FROM egs_games WHERE egs_id=2").fetchone()
        self.assertEqual(row["getchu_id"], "777")
        self.assertEqual(row["dlsite_id"], "RJ000001")


class DlsiteCdnTests(unittest.TestCase):
    def test_rj_and_vj_folders(self):
        urls = egs_thumb.dlsite_cdn_urls("RJ088973")
        self.assertIn("doujin/RJ089000/RJ088973_img_main.jpg", urls[0])
        self.assertEqual(len(urls), 2)
        vj = egs_thumb.dlsite_cdn_urls("VJ009516")
        self.assertIn("professional/VJ010000/VJ009516_img_main.jpg", vj[0])

    def test_unknown_prefix_returns_empty(self):
        self.assertEqual(egs_thumb.dlsite_cdn_urls("XX000001"), [])
        self.assertEqual(egs_thumb.dlsite_cdn_urls(""), [])

    def test_extract_id_from_dir_names(self):
        self.assertEqual(
            egs_thumb.extract_dlsite_id("[2013][x] (RJ091048) エリス"), "RJ091048")
        self.assertEqual(egs_thumb.extract_dlsite_id("no id here"), "")


class MatchRelaxTests(unittest.TestCase):
    """40193 案例：作品名 + 特典后缀，同人页无品牌。"""

    TARGET = "紳士 ドライバー ～ブレーキなしの淫行～"
    CANDIDATE = "紳士 ドライバー〜ブレーキなしの淫行〜＋特典CG集付き"

    def test_title_match_accepts_truncated_suffix(self):
        ok, score = egs_thumb.title_match(self.TARGET, self.CANDIDATE)
        self.assertTrue(ok)
        self.assertGreaterEqual(score, 0.90)

    def test_brand_state_distinguishes_empty(self):
        self.assertEqual(egs_thumb.brand_state("Nite", "")[0], "empty")
        self.assertEqual(egs_thumb.brand_state("Nite", "Nite")[0], "match")
        self.assertEqual(egs_thumb.brand_state("Nite", "OtherBrand")[0], "miss")

    def test_pick_accepts_single_candidate_without_brand(self):
        row = {"name": self.TARGET, "company": "WorkNite Games"}
        item = {"getchu_id": "1380067", "title": self.CANDIDATE, "brand": "",
                "media": "同人・アダルト"}
        match, evidence = egs_thumb.pick_getchu_match(row, [item])
        self.assertIsNotNone(match)
        self.assertEqual(match["getchu_id"], "1380067")
        self.assertIn("无品牌", evidence["reason"])

    def test_pick_rejects_non_game_media(self):
        row = {"name": self.TARGET, "company": "WorkNite Games"}
        item = {"getchu_id": "1", "title": self.CANDIDATE, "brand": "",
                "media": "CD"}
        match, _ = egs_thumb.pick_getchu_match(row, [item])
        self.assertIsNone(match)


class CoverShapeTests(unittest.TestCase):
    """2026-11-26 放宽：横图与方图是正常封面，只拒过小和超扁横幅。"""

    @staticmethod
    def _png(width, height, flat=False):
        import io
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (width, height), (200, 200, 200))
        if not flat:
            draw = ImageDraw.Draw(im)
            for i in range(0, width, 40):
                draw.rectangle([i, 0, i + 20, height],
                               fill=(30 + i % 200, 80, 120))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def test_accepts_landscape_4_3(self):
        ok, info = egs_thumb.image_looks_like_package(self._png(384, 288))
        self.assertTrue(ok, info)

    def test_accepts_square(self):
        ok, _ = egs_thumb.image_looks_like_package(self._png(300, 300))
        self.assertTrue(ok)

    def test_rejects_too_small(self):
        ok, _ = egs_thumb.image_looks_like_package(self._png(120, 90))
        self.assertFalse(ok)

    def test_rejects_banner(self):
        ok, _ = egs_thumb.image_looks_like_package(self._png(1200, 200))
        self.assertFalse(ok)

    def test_rejects_flat_placeholder(self):
        """官网 og:image 常返回整块背景色，尺寸比例合规但内容全黑。"""
        ok, info = egs_thumb.image_looks_like_package(self._png(800, 800, flat=True))
        self.assertFalse(ok)
        self.assertIn("近纯色", info)


if __name__ == "__main__":
    unittest.main()


class ThumbJobWiringTests(unittest.TestCase):
    """网页「抓封面」按钮对应的 pipeline thumb 动作与待补清单。"""

    def test_pipeline_has_thumb_action(self):
        from tool import pipeline
        self.assertIn("thumb", pipeline.ACTIONS)
        self.assertEqual(pipeline.LABELS["thumb"], "抓取封面")

    def test_pending_cover_rows_only_missing(self):
        conn = make_conn()
        conn.execute(
            "INSERT INTO egs_games (egs_id, name, company, date, dlsite_id, downloaded) "
            "VALUES (11, 'X', 'C', '2026-01', 'RJ000011', 1)"
        )
        conn.execute(
            "INSERT INTO egs_games (egs_id, name, company, date, dmm, downloaded) "
            "VALUES (12, 'Y', 'C', '2026-02', 'd_999', 1)"
        )
        conn.commit()
        rows = egs_thumb.pending_cover_rows(conn, 2026, 2026, 1)
        self.assertEqual([r["egs_id"] for r in rows], [11])
        rows_all = egs_thumb.pending_cover_rows(conn, 2026, 2026, None)
        self.assertEqual(sorted(r["egs_id"] for r in rows_all), [11, 12])
        conn.close()
