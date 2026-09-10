"""다우 판매일보 06시 귀속 경계를 외부 호출 없이 검증한다."""
import unittest
from unittest.mock import Mock, patch
import scrape


class SalesCutoffTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            ("2026-09-09T05:59:59.999+09:00", "2026-09-08"),
            ("2026-09-09T06:00:00+09:00", "2026-09-09"),
            ("2026-09-09T23:59:59+09:00", "2026-09-09"),
            ("2026-09-10T00:46:57.471+09:00", "2026-09-09"),
            ("2026-09-10T05:59:59.999+09:00", "2026-09-09"),
            ("2026-09-10T06:00:00+09:00", "2026-09-10"),
            ("2026-09-09T15:46:57.471Z", "2026-09-09"),
            ("2026-09-10 00:46:57", "2026-09-09"),
            ("2027-01-01T00:01:00+09:00", "2026-12-31"),
            ("2026-03-01T00:01:00+09:00", "2026-02-28"),
            ("", ""),
        ]
        for created, expected in cases:
            with self.subTest(created=created):
                self.assertEqual(scrape.attribution_date(created), expected)

    def test_pangyo_original_timestamp_preserved(self):
        created = "2026-09-10T00:46:57.471+09:00"
        row = scrape.parse_post({"id": "1547272107992293376", "createdAt": created,
                                 "summary": "현대판교점 2026/09/09"}, None, False)
        self.assertEqual(row["date"], "2026-09-09")
        self.assertEqual(row["createdAt"], created)

    @patch.object(scrape, "fetch_post_full_content", return_value=None)
    def test_daily_collection_crosses_midnight_and_page_boundary(self, detail):
        def post(id, created):
            return {"id": id, "createdAt": created, "summary": "시험"}
        pages = [
            {"data": [post("later", "2026-09-10T06:00:00+09:00"),
                      post("1547272107992293376", "2026-09-10T00:46:57.471+09:00")], "hasNext": True},
            {"data": [post("evening", "2026-09-09T22:19:08+09:00"),
                      post("start", "2026-09-09T06:00:00+09:00"),
                      post("older", "2026-09-09T05:59:59+09:00")], "hasNext": True},
        ]
        session = Mock()
        session.get.side_effect = [Mock(json=Mock(return_value=p), status_code=200) for p in pages]
        rows = scrape.fetch_posts_for_date(session, "2026-09-09")
        self.assertEqual([p["id"] for p in rows], ["1547272107992293376", "evening", "start"])
        self.assertEqual({p["date"] for p in rows}, {"2026-09-09"})
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(detail.call_count, 3)


if __name__ == "__main__":
    unittest.main()
