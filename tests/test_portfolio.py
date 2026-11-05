import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'src'))
from build_demo import build  # noqa: E402
from nutrivalue.pricing import protein_value  # noqa: E402

class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        build(self.db)

    def tearDown(self):
        self.db.close()

    def report(self,name):
        return self.db.execute((ROOT/'analytics/queries'/name).read_text()).fetchall()

    def test_no_demo_prices_in_verified_ranking(self):
        self.assertEqual(self.report('best_protein_value.sql'), [])

    def test_synthetic_value_ranking(self):
        rows=self.report('demo_protein_value.sql')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][0],'Demo Protein B')
        self.assertEqual(rows[0][-1],0.6)
        self.assertEqual(rows[1][-1],0.83)

    def test_reference_coverage_preserves_missing_values(self):
        row=self.report('mvt_nutrition_coverage.sql')[0]
        self.assertEqual(row[2:],(4,3,1,3,1,2))

    def test_reference_nutrition_and_density(self):
        rows=self.report('mvt_protein_per_100kcal.sql')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][1],'Demo reference food A')
        self.assertEqual(rows[0][5],16.67)
        reference=self.report('mvt_nutrition_per_100g.sql')
        self.assertEqual(len(reference),2)
        self.assertEqual(reference[0][1],'Demo reference food A')
        self.assertEqual(reference[0][3:5],(20.0,120.0))

    def test_group_counts(self):
        rows=self.report('mvt_reference_group_summary.sql')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][2],2)

    def test_precision_and_reject_invalid_inputs(self):
        value=protein_value('60','400','18')
        self.assertEqual(value.total_protein_g,Decimal('72'))
        self.assertEqual(value.nok_per_g_protein.quantize(Decimal('0.01')),Decimal('0.83'))
        for values in [(0,400,18),(60,0,18),(60,400,0),(60,400,101),
                       (60,400,'NaN'),(60,'Infinity',18)]:
            with self.subTest(values=values):
                with self.assertRaises(ValueError): protein_value(*values)

if __name__=='__main__':
    unittest.main()
