"""TASK-021: offline synthetic five-card pilot; actual REMA evidence stays private."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from nutrivalue.reconciliation_pilot import inspect, store

ROOT=Path(__file__).resolve().parents[1]
SHA='c'*64


class FiveCardPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.rema=self.root/'rema.sqlite3'
        self.kass=self.root/'kassalapp.sqlite3'
        with sqlite3.connect(self.rema) as db:
            db.executescript((ROOT/'analytics/rema_locked_staging.sql').read_text())
            db.execute('INSERT INTO rema_locked_snapshots VALUES (?,?,?,?)',(
                SHA,'https://www.rema.no/kampanjer/last-pris/',
                '2026-10-10T10:00:00+00:00','synthetic archived HTML'))
            rows=[
                (12,'Synthetic Eggs',2690,'402','g',None,6692,'kg'),
                (27,'Synthetic Cheese',11840,'800','g',None,14800,'kg'),
                (104,'Synthetic Sausage',2940,'300','g',None,9800,'kg'),
                (106,'Synthetic Meatballs',5990,'600','g',None,9983,'kg'),
                (110,'Synthetic Cakes',5990,'0.55','kg',None,10891,'kg'),
                (112,'Synthetic Cakes',5990,'0.55','kg',None,10891,'kg'),
            ]
            db.executemany('''INSERT INTO rema_locked_cards
                (snapshot_sha256,card_no,product_name,price_ore,price_basis,
                 details_text,package_quantity,package_unit,pack_count,
                 unit_price_ore,unit_price_unit,review_state)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',[
                (SHA,num,name,price,'per_package','synthetic',
                 quantity,unit,count,rate,rate_unit,'source_only')
                for num,name,price,quantity,unit,count,rate,rate_unit in rows
            ])
        with sqlite3.connect(self.kass) as db:
            db.executescript((ROOT/'analytics/schema.sql').read_text())
            db.executemany('''INSERT INTO kassalapp_staging
                (id,provider_product_id,ean,product_name,brand,store_code,
                 reported_weight_g,fetched_at,review_status,review_notes,
                 source_ref,raw_payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',[
                (1,'P1','4006381333931','Synthetic Eggs','Synthetic','R',
                 999,'2026-10-10T00:00:00Z','needs_review','synthetic',
                 'test:1','{}'),
                (2,'P2','4006381333931','Synthetic unrelated chicken','Synthetic',
                 'R',400,'2026-10-10T00:00:00Z','needs_review','synthetic',
                 'test:2','{}'),
            ])
        self.cards=(12,27,104,106,110)

    def review(self, cards=None):
        return inspect(self.rema,self.kass,SHA,self.cards if cards is None else cards)

    def test_five_exact_pinned_source_only_cards_and_kassalapp_lookup_scope(self):
        report=self.review()
        self.assertEqual(len(report['cases']),5)
        self.assertEqual([x['card_no'] for x in report['cases']],list(self.cards))
        self.assertEqual(report['kassalapp_staging_rows_seen'],2)
        self.assertEqual(report['manual_external_lookups'],'not_attempted')
        self.assertTrue(all(x['source_only'] and not x['identity_confirmed']
             and not x['nutrition_verified'] and not x['local_retail_price_verified']
             for x in report['cases']))
        self.assertEqual(report['status'],'source_only')
        self.assertEqual(report['lookup_scope'],'local_staging_exact_name_only')

    def test_negative_local_lookup_not_global_product_nonexistence(self):
        report=self.review()
        cheese=report['cases'][1]
        self.assertEqual(cheese['lookup']['result'],'no_exact_name_in_staging')
        self.assertEqual(cheese['lookup']['matching_rows'],0)
        self.assertEqual(cheese['result'],'insufficient_evidence')
        self.assertIn('no_exact_title_candidate_in_existing_kassalapp_staging',cheese['reasons'])
        self.assertIn('retailer_ean_missing',cheese['reasons'])

    def test_same_name_mass_conflict_still_cannot_claim_matching_ean(self):
        report=self.review()
        eggs=report['cases'][0]
        self.assertEqual(eggs['candidate_staging_ids'],[1])
        self.assertEqual(eggs['result'],'conflicting_evidence')
        self.assertIn('candidate_package_mass_conflict',eggs['reasons'])
        self.assertFalse(eggs['identity_confirmed'])

    def test_same_name_equal_mass_still_review_only(self):
        with sqlite3.connect(self.kass) as db:
            db.execute('UPDATE kassalapp_staging SET reported_weight_g=402 WHERE id=1')
        report=self.review()
        self.assertEqual(report['cases'][0]['result'],'insufficient_evidence')
        self.assertFalse(report['cases'][0]['identity_confirmed'])
        self.assertEqual(report['cases'][0]['lookup']['result'],'name_candidate_not_verified')

    def test_sibling_visible_card_is_flagged_not_auto_merged(self):
        report=self.review()
        case=report['cases'][-1]
        self.assertEqual(case['same_visible_fields_card_numbers'],[112])
        self.assertEqual(case['result'],'insufficient_evidence')
        self.assertIn('other_cards_share_visible_fields_identity_unresolved',case['reasons'])

    def test_source_only_derived_unit_rates_and_no_false_prices(self):
        report=self.review()
        eggs=report['cases'][0]
        self.assertEqual(eggs['price_ore'],2690)
        self.assertEqual(eggs['derived_unit_price_ore'],6692)
        self.assertEqual(eggs['derived_unit_status'],'derived_source_only_not_verified')
        self.assertNotIn('verified',str(report['status']))

    def test_invalid_snapshot_or_five_id_contract_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'exact 64-character'):
            inspect(self.rema,self.kass,'invalid',self.cards)
        with self.assertRaisesRegex(ValueError,'snapshot absent'):
            inspect(self.rema,self.kass,'a'*64,self.cards)
        with self.assertRaisesRegex(ValueError,'exactly five'):
            self.review((12,27,104,106,106))
        with self.assertRaisesRegex(ValueError,'exactly five'):
            self.review((12,27,104,106))
        with self.assertRaisesRegex(ValueError,'separate'):
            inspect(self.rema,self.rema,SHA,self.cards)
        with sqlite3.connect(self.rema) as db:
            db.execute('DELETE FROM rema_locked_cards WHERE card_no=12')
        with self.assertRaisesRegex(ValueError,'selected REMA cards missing'):
            self.review()

    def test_empty_kassalapp_source_rejected(self):
        with sqlite3.connect(self.kass) as db:
            db.execute('DELETE FROM kassalapp_staging')
        with self.assertRaisesRegex(ValueError,'Kassalapp staging is empty'):
            self.review()

    def test_private_append_only_report_is_idempotent_and_sql_visible(self):
        before_rema=self.rema.read_bytes()
        before_kass=self.kass.read_bytes()
        report=self.review()
        dest=self.root/'private'/'five.sqlite3'
        expected=(ROOT/'analytics/reconciliation_pilot_schema.sql')
        self.assertEqual(store(report,dest,expected,self.root/'private'),(5,0))
        self.assertEqual(store(report,dest,expected,self.root/'private'),(0,5))
        self.assertEqual(self.rema.read_bytes(),before_rema)
        self.assertEqual(self.kass.read_bytes(),before_kass)
        with sqlite3.connect(dest) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pilot_runs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM pilot_cards').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT count(*) FROM pilot_lookup_attempts').fetchone()[0],5)
            rows=db.execute((ROOT/'analytics/queries/reconciliation_pilot_review.sql')
                            .read_text()).fetchall()
            self.assertEqual([x[3] for x in rows],list(self.cards))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE pilot_cards SET product_name='altered'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM pilot_runs')

    def test_store_refuses_outside_private_path_and_source_db(self):
        report=self.review()
        with self.assertRaisesRegex(ValueError,'ignored data/private'):
            store(report,self.root/'public.sqlite3',
                  ROOT/'analytics/reconciliation_pilot_schema.sql',self.root/'private')
        self.assertFalse((self.root/'public.sqlite3').exists())

    def test_real_cli_read_only_preview_runs_without_output_db(self):
        result=subprocess.run([sys.executable,
            str(ROOT/'scripts/review_rema_five.py'),
            '--rema-db',str(self.rema),
            '--kassalapp-db',str(self.kass),
            '--snapshot-sha',SHA,'--cards',','.join(map(str,self.cards))],
            check=True,capture_output=True,text=True)
        self.assertIn('READ-ONLY PREVIEW',result.stdout)
        self.assertIn('no_exact_name_in_staging',result.stdout)
        self.assertIn('local_kassalapp_rows_scanned', result.stdout)
        self.assertIn('source values redacted', result.stdout)
        self.assertNotIn('Synthetic Eggs', result.stdout)
        self.assertNotIn('Synthetic Cheese', result.stdout)
        self.assertNotIn('4006381333931', result.stdout)
        self.assertNotIn(SHA, result.stdout)
        self.assertNotIn('"price_ore": 2690', result.stdout)
        self.assertFalse((self.root/'private').exists())


if __name__=='__main__':
    unittest.main()
