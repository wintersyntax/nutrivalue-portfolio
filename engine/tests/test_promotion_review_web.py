"""TASK-055: local web review over the synthetic week. NO external network."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

from nutrivalue.promotion_extraction import build, import_extraction
from nutrivalue.promotion_review_web import serve, week_overview
from test_promotion_extraction import ROOT, SyntheticWeek


class WebReviewTests(SyntheticWeek):
    def setUp(self):
        super().setUp()
        self.execute()
        self.register_run()
        import_extraction(build(self.run_dir, self.out, ocr_lookup=self.lookup()), self.db,
                          ROOT / "analytics")
        self.server = serve(self.db, ROOT / "analytics", self.tmp, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, body, header=True):
        request = urllib.request.Request(self.base + "/api/decision", method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  **({"X-NutriValue-Review": "1"} if header else {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_page_week_and_image(self):
        status, kind, body = self.get("/")
        self.assertEqual((status, kind.split(";")[0]), (200, "text/html"))
        self.assertIn(b"promotion review", body)
        week = json.loads(self.get("/api/week")[2])
        self.assertEqual((week["week"], len(week["items"]), len(week["pages"])), ("2026-W44", 3, 3))
        first = week["items"][0]
        self.assertEqual((first["vkey"], first["member_only"]), ("n_for_m|3|2|None|None", False))
        self.assertIn(b"Accept all", body)
        self.assertEqual(week["pages"]["cat|2"]["answers"]["openai/gpt-6-luna"]["error"],
                         "answer is not JSON")
        status, kind, image = self.get(
            f"/page-image?extraction={quote(week['extraction_id'])}&catalog=cat&page=1")
        self.assertEqual((status, kind, image[:3]), (200, "image/jpeg", b"\xff\xd8\xff"))
        with self.assertRaises(urllib.error.HTTPError):
            self.get(f"/page-image?extraction={quote(week['extraction_id'])}&catalog=../x&page=1")
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/week?week=../../etc")

    def test_decisions_need_the_header_and_valid_items(self):
        week = week_overview(self.db, ROOT / "analytics")
        open_item = next(i for i in week["items"] if i["final_status"] == "needs_review")
        body = {"extraction_id": week["extraction_id"], "catalog_id": "cat",
                "page": open_item["page"], "item_no": open_item["item_no"], "decision": "accept"}
        self.assertEqual(self.post(body, header=False)[0], 403)
        self.assertEqual(self.post(dict(body, item_no=99))[0], 400)
        self.assertEqual(self.post(dict(body, decision="page_checked"))[0], 400)
        self.assertEqual(self.post(body), (200, {"ok": True}))
        accepted = next(i for i in week["items"] if i["final_status"] == "accepted")
        self.assertEqual(self.post(dict(body, item_no=accepted["item_no"], decision="reject",
                                        note="spot-check: not on this page"))[0], 200)
        self.assertEqual(self.post({"extraction_id": week["extraction_id"], "catalog_id": "cat",
                                    "page": 2, "item_no": None, "decision": "page_checked"})[0],
                         200)
        after = {i["item_no"]: i["final_status"] for i in
                 week_overview(self.db, ROOT / "analytics")["items"]}
        self.assertEqual(after[open_item["item_no"]], "accepted_by_review")
        self.assertEqual(after[accepted["item_no"]], "rejected")
        self.assertIsNotNone(week_overview(self.db, ROOT / "analytics")["pages"]["cat|2"]
                             ["page_decision"])


if __name__ == "__main__":
    unittest.main()
