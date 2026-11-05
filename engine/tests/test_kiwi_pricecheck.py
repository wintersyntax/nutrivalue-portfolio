"""Offline contracts for a single first-party weekly KIWI price-check page."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.kiwi_pricecheck import (
    PriceCheckError, parse_html, stage_html,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics/kiwi_pricecheck_staging.sql"


def fixture(rows="", *, week=41, until="11.10.2026"):
    if not rows:
        rows = """
          <tr><td>LAKS PORSJON U/SKINN 4X125G LERØY</td>
              <td>99,00</td><td>79,20</td><td>99,00</td></tr>
          <tr><td>TORSKEFILET NATURELL U/SKINN CA400G</td>
              <td>387,00</td><td>319,90 pr.kg</td><td>387,00</td></tr>
          <tr><td>YOGHURT GRESK 160G</td>
              <td>26,4</td><td>20,00</td><td>26,4</td></tr>
          <tr><td></td><td></td><td></td><td></td></tr>
          <tr><td>JIF SPRAY 500ML</td>
              <td>39,90</td><td>16,70</td><td>39,90</td></tr>"""
    return f"""<html><script>var ignored='Uke 52';</script><h2>Uke {week}</h2>
    <p>Gjelder til og med {until}</p>
    <table><tr><th>VARETEKST</th><th>GAMMEL UTPRIS</th>
    <th>NY UTPRIS</th><th>LAVESTE PRIS SISTE 30 DAGENE</th></tr>{rows}</table></html>"""


class KiwiPriceCheckTests(unittest.TestCase):
    def test_four_source_rows_preserve_exact_price_and_basis(self):
        parsed = parse_html(fixture())
        self.assertEqual((parsed.year, parsed.week, parsed.valid_through),
                         (2026, 41, "2026-10-11"))
        self.assertEqual(len(parsed.rows), 4)
        self.assertEqual((parsed.rows[0].new_cents, parsed.rows[0].basis),
                         (7920, "per_package"))
        self.assertEqual((parsed.rows[1].new_cents, parsed.rows[1].basis),
                         (31990, "per_kg"))
        self.assertEqual(parsed.rows[2].old_cents, 2640)
        self.assertFalse(parsed.rows[3].candidate_protein)

    def test_three_column_real_world_row_preserves_missing_low_as_null(self):
        # Observed on KIWI page: row 137 lacked the fourth HTML cell.
        html = fixture(rows=(
            "<tr><td>BAMA G&amp;F SALAT ISBERG MIX 250G</td>"
            "<td></td><td>22,90</td></tr>"
        ))
        parsed = parse_html(html)
        self.assertEqual(len(parsed.rows), 1)
        row = parsed.rows[0]
        self.assertEqual(row.name, "BAMA G&F SALAT ISBERG MIX 250G")
        self.assertEqual(row.new_cents, 2290)
        self.assertEqual(row.basis, "per_package")
        self.assertIsNone(row.old_cents)
        self.assertIsNone(row.low_30d_cents)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "private.sqlite3"
            stage_html(html.encode(), db_path, SCHEMA)
            with sqlite3.connect(db_path) as db:
                stored = db.execute(
                    "SELECT price_cents, old_cents, low_30d_cents, review_state "
                    "FROM weekly_pricecheck_candidates"
                ).fetchone()
            self.assertEqual(stored, (2290, None, None, "source_only"))

    def test_nonempty_row_without_name_or_new_price_fails_closed(self):
        # A valid row followed by an invalid one must not mutate the DB
        # or silently omit an offer with an unknown actual new price.
        valid = (
            "<tr><td>EGG 12PK</td><td>40,00</td>"
            "<td>35,00</td><td>39,00</td></tr>"
        )
        invalid_rows = (
            "<tr><td>YOGHURT 500G</td><td>25,00</td><td></td></tr>",
            "<tr><td>YOGHURT 500G</td><td>25,00</td><td></td><td>23,00</td></tr>",
            "<tr><td></td><td>25,00</td><td>20,00</td></tr>",
            "<tr><td>YOGHURT 500G</td><td>25,00</td></tr>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "must_not_exist.sqlite3"
            for bad in invalid_rows:
                with self.subTest(bad=bad):
                    with self.assertRaises(PriceCheckError):
                        stage_html(fixture(rows=valid + bad).encode(), db_path, SCHEMA)
                    self.assertFalse(db_path.exists())

    def test_approximate_weight_without_price_unit_is_unknown(self):
        # Observed source price 272,00 on "LAKS LOIN CA300G"; do not
        # assume the list means NOK/package rather than NOK/kg.
        samples = (
            "<tr><td>LAKS LOIN CA300G FISKEMANNEN</td>"
            "<td>343,00</td><td>272,00</td><td>343,00</td></tr>"
            "<tr><td>TORSKEFILET CA400G</td>"
            "<td>387,00</td><td>319,90 pr.kg</td><td>387,00</td></tr>"
        )
        parsed = parse_html(fixture(rows=samples))
        self.assertEqual((parsed.rows[0].new_cents, parsed.rows[0].basis),
                         (27200, "unknown"))
        self.assertEqual(parsed.rows[1].basis, "per_kg")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "private.sqlite3"
            stage_html(fixture(rows=samples).encode(), db_path, SCHEMA)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT price_basis FROM weekly_pricecheck_candidates "
                        "ORDER BY row_no"
                    ).fetchall(),
                    [("unknown",), ("per_kg",)],
                )

    def test_legacy_private_db_migrates_without_losing_existing_rows(self):
        # A real KIWI snapshot was already imported with the first two-basis
        # schema. An updated parser must not break the user's existing DB.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "existing.sqlite3"
            old_schema = SCHEMA.read_text(encoding="utf-8").replace(
                "'per_package', 'per_kg', 'unknown'",
                "'per_package', 'per_kg'",
            )
            with sqlite3.connect(db_path) as db:
                db.executescript(old_schema)
                db.execute(
                    "INSERT INTO weekly_pricecheck_snapshots "
                    "(sha256, source_url, fetched_at, iso_year, iso_week, "
                    "valid_through, original_html) VALUES "
                    "(?, 'https://kiwi.no/dagligvarer/prissjekk', "
                    "'2026-10-10T13:00:00Z', 2026, 41, '2026-10-11', 'old')",
                    ("a" * 64,),
                )
                db.execute(
                    "INSERT INTO weekly_pricecheck_candidates "
                    "(snapshot_sha256, row_no, chain_name, product_text, "
                    "old_cents, price_cents, low_30d_cents, price_basis, "
                    "candidate_protein, review_state) VALUES "
                    "(?, 1, 'KIWI', 'OLDER PRODUCT', 2000, 1500, 2000, "
                    "'per_package', 0, 'source_only')",
                    ("a" * 64,),
                )
                db.execute(
                    "INSERT INTO weekly_pricecheck_candidates "
                    "(snapshot_sha256, row_no, chain_name, product_text, "
                    "old_cents, price_cents, low_30d_cents, price_basis, "
                    "candidate_protein, review_state) VALUES "
                    "(?, 2, 'KIWI', 'LAKS LOIN CA300G OLD SNAPSHOT', "
                    "34300, 27200, 34300, 'per_package', 1, 'source_only')",
                    ("a" * 64,),
                )
            html = fixture(rows=(
                "<tr><td>LAKS LOIN CA300G FISKEMANNEN</td>"
                "<td>343,00</td><td>272,00</td><td>343,00</td></tr>"
            ))
            stage_html(html.encode(), db_path, SCHEMA)
            with sqlite3.connect(db_path) as db:
                rows = db.execute(
                    "SELECT product_text, price_cents, price_basis "
                    "FROM weekly_pricecheck_candidates ORDER BY product_text"
                ).fetchall()
                index = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='weekly_pricecheck_review_idx'"
                ).fetchone()
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM weekly_pricecheck_snapshots"
                    ).fetchone()[0],
                    2,
                )
            self.assertEqual(
                rows,
                [
                    ("LAKS LOIN CA300G FISKEMANNEN", 27200, "unknown"),
                    ("LAKS LOIN CA300G OLD SNAPSHOT", 27200, "unknown"),
                    ("OLDER PRODUCT", 1500, "per_package"),
                ],
            )
            self.assertIsNotNone(index)

    def test_identical_source_is_idempotent_and_different_html_a_new_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "private.sqlite3"
            data = fixture().encode()
            _, digest = stage_html(data, db_path, SCHEMA, fetched_at="2026-10-10T13:00:00Z")
            stage_html(data, db_path, SCHEMA, fetched_at="2026-10-10T14:00:00Z")
            stage_html(fixture().replace("79,20", "78,90").encode(), db_path, SCHEMA)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM weekly_pricecheck_snapshots").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM weekly_pricecheck_candidates").fetchone()[0], 8)
                row = db.execute(
                    "SELECT price_cents, review_state FROM weekly_pricecheck_candidates WHERE snapshot_sha256=? AND row_no=1",
                    (digest,),
                ).fetchone()
            self.assertEqual(row, (7920, "source_only"))

    def test_missing_table_or_wrong_week_prevents_db_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "should_not_exist.sqlite3"
            for html in (
                fixture().replace("VARETEKST", "UNKNOWN"),
                fixture(week=40),
                fixture(until="32.10.2026"),
                fixture().replace("79,20", "free"),
            ):
                with self.subTest(sample=html[-60:]):
                    with self.assertRaises(PriceCheckError):
                        stage_html(html.encode(), db_path, SCHEMA)
                    self.assertFalse(db_path.exists())

    def test_source_without_title_or_new_price_cannot_create_a_fake_offer(self):
        partial = fixture(rows="<tr><td></td><td>20,00</td><td></td><td></td></tr>")
        with self.assertRaises(PriceCheckError):
            parse_html(partial)


if __name__ == "__main__":
    unittest.main()
