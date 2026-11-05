"""TASK-020: synthetic offline reconciliation; no private real-priced assertions."""
from __future__ import annotations
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from nutrivalue.reconciliation import Identity, compare_candidates, derived_unit_price_ore
from scripts.review_reconciliation_queue import inspect

ROOT=Path(__file__).resolve().parents[1]
SHA1='a'*64
SHA2='b'*64


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dbpath=Path(temp.name)/'private.sqlite3'
        self.db=sqlite3.connect(self.dbpath)
        self.addCleanup(self.db.close)
        for filename in ('analytics/kiwi_locked_staging.sql',
                         'analytics/rema_locked_staging.sql',
                         'analytics/reconciliation_evidence.sql'):
            self.db.executescript((ROOT/filename).read_text(encoding='utf-8'))
        self.db.executemany('INSERT INTO kiwi_locked_snapshots VALUES (?,?,?,?)',[
            (SHA2,'https://kiwi.no/dagligvarer/prislaas','2026-10-08T00:00:00Z','old'),
            (SHA1,'https://kiwi.no/dagligvarer/prislaas','2026-10-10T00:00:00Z','new')])
        self.db.executemany('INSERT INTO kiwi_locked_cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
            (SHA2,1,'Old salmon','old',5000,'per_package',0,None,None,None,'',None,None,'unchecked','source_only'),
            (SHA1,1,'Synthetic salmon','300 g',5000,'per_package',0,'300','g',None,'pr. kg 160',16000,'kg','mismatch','source_only'),
            (SHA1,2,'Synthetic milk','1 l + pant',3000,'per_package',1,'1','liter',None,'pr. liter',None,'liter','unchecked','source_only'),
            (SHA1,3,'Synthetic normal','200 g',2000,'per_package',0,'200','g',None,'pr. kg 100',10000,'kg','match','source_only')])
        self.db.execute('INSERT INTO rema_locked_snapshots VALUES (?,?,?,?)',
                        (SHA2,'https://www.rema.no/kampanjer/last-pris/','2026-10-10T00:00:00Z','new'))
        self.db.execute('INSERT INTO rema_locked_cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (SHA2,1,'Synthetic chicken',3000,'per_package','unknown',None,None,None,None,None,'source_only'))
        self.db.commit()

    def test_latest_per_retailer_queue_is_read_only_and_source_only(self):
        before=self.db.total_changes
        report=inspect(self.dbpath,5)
        self.assertEqual(report['source_rows_with_exceptions'],3)
        self.assertEqual(report['selected_count'],3)
        self.assertEqual({x['retailer'] for x in report['cards']},{'KIWI','REMA'})
        self.assertTrue(all(x['snapshot_sha256'] in (SHA1,SHA2) for x in report['cards']))
        self.assertTrue(all(x['evidence_status']=='source_only' for x in report['cards']))
        self.assertEqual(report['retail_price_status'],'source_only')
        self.assertEqual(before,self.db.total_changes)

    def test_empty_source_and_limits_fail_closed(self):
        with self.assertRaises(ValueError):
            inspect(self.dbpath,6)
        with self.assertRaises(ValueError):
            inspect(self.dbpath,0)
        with self.assertRaises(ValueError):
            inspect(self.dbpath.parent/'missing.sqlite3')

    def test_exact_same_identity_remains_candidate_only(self):
        x=Identity('4006381333931','Prior','Original','450','g')
        result=compare_candidates(x,x)
        self.assertEqual(result['status'],'candidate_match')
        self.assertFalse(result['confirmed_identity'])
        self.assertFalse(result['verified_price'])
        self.assertFalse(result['verified_nutrition'])

    def test_missing_ean_brand_variant_cannot_be_confirmed(self):
        retailer=Identity(None,None,'Original','450','g')
        manufacturer=Identity('4006381333931','Prior','Original','450','g')
        result=compare_candidates(retailer,manufacturer)
        self.assertEqual(result['status'],'needs_manual_review')
        self.assertIn('ean_not_corroborated',result['reasons'])
        self.assertIn('brand_not_corroborated',result['reasons'])

    def test_brand_variant_and_pack_disagreement_are_conflicts(self):
        retailer=Identity('4006381333931','Prior','Natural','500','g')
        candidate=Identity('4006381333931','Different','Vanilla','450','g')
        result=compare_candidates(retailer,candidate)
        self.assertEqual(result['status'],'conflicting_evidence')
        self.assertEqual(result['reasons'],
                         ['brand_conflict','package_conflict','variant_conflict'])

    def test_compatible_net_pack_and_unit_conversions_are_candidate_only(self):
        a=Identity('4006381333931','Prior','Natural','500','g',2)
        b=Identity('4006381333931','Prior','Natural','1','kg')
        self.assertEqual(compare_candidates(a,b)['status'],'candidate_match')
        self.assertEqual(derived_unit_price_ore(10000,'500','g',2,target_unit='kg'),
                         (10000,'derived_source_only_not_verified'))

    def test_pant_unknown_mass_density_and_source_rate_mismatch(self):
        self.assertEqual(derived_unit_price_ore(2000,'1','l',None,target_unit='kg')[0],None)
        self.assertEqual(derived_unit_price_ore(2000,'1','l',None,target_unit='l',deposit_applies=True)[0],None)
        self.assertEqual(derived_unit_price_ore(2000,None,None,None,target_unit='kg')[0],None)
        self.assertEqual(derived_unit_price_ore(2000,'200','g',None,target_unit='kg',source_unit_ore=11000),
                         (10000,'source_unit_rate_mismatch_review'))
        self.assertEqual(derived_unit_price_ore(2000,'200','g',None,price_basis='unknown')[0],None)
        self.assertEqual(derived_unit_price_ore(2000,'0','g',None)[0],None)

    def test_source_families_and_conflicts_not_url_vote_count(self):
        for name,family,derived in [
            ('first','manufacturer',None),
            ('mirror','manufacturer','first'),
            ('independent','provider',None)
        ]:
            self.db.execute('INSERT INTO recon_sources VALUES (?,?,?,?,?,?,?,?)',
                            (name,name,family,'source:'+name,SHA1,'2026-10-11T00:00:00Z',
                             derived,'permitted_private'))
        for idx,(source,value) in enumerate([
            ('first','500'),('mirror','500'),('independent','450')
        ]):
            self.db.execute('INSERT INTO recon_field_assertions (assertion_sha256,source_id,retailer,retailer_snapshot_sha256,retailer_card_no,candidate_ref,field_name,asserted_value,added_at) VALUES (?,?,?,?,?,?,?,?,?)',
                            (str(idx)*64,source,'KIWI',SHA1,1,'candidate-1',
                             'package_quantity',value,'2026-10-11T00:00:00Z'))
        self.db.commit()
        row=self.db.execute((ROOT/'analytics/queries/reconciliation_field_conflicts.sql')
                            .read_text(encoding='utf-8')).fetchone()
        self.assertEqual(row[-4],2)  # upstream families, not three websites
        self.assertEqual(row[-3],2)  # two competing values
        self.assertEqual(row[-1],'conflicting_evidence')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE recon_field_assertions SET asserted_value='700' WHERE source_id='first'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM recon_sources WHERE source_id='mirror'")


    def test_kiwi_only_database_reports_rema_unstaged_and_preserves_file(self):
        self.db.execute('DROP TABLE rema_locked_cards')
        self.db.execute('DROP TABLE rema_locked_snapshots')
        self.db.commit()
        before=self.dbpath.read_bytes()
        result=inspect(self.dbpath,5)
        self.assertEqual(result['available_retailers'],['KIWI'])
        self.assertEqual(result['skipped_retailers_without_staging'],['REMA'])
        self.assertEqual(result['coverage'],'partial')
        self.assertEqual(result['source_rows_with_exceptions'],2)
        self.assertTrue(all(x['retailer']=='KIWI' for x in result['cards']))
        self.assertEqual(self.dbpath.read_bytes(),before)

    def test_rema_only_database_reports_kiwi_unstaged(self):
        self.db.execute('DROP TABLE kiwi_locked_cards')
        self.db.execute('DROP TABLE kiwi_locked_snapshots')
        self.db.commit()
        result=inspect(self.dbpath,5)
        self.assertEqual(result['available_retailers'],['REMA'])
        self.assertEqual(result['skipped_retailers_without_staging'],['KIWI'])
        self.assertEqual(result['coverage'],'partial')
        self.assertEqual(result['source_rows_with_exceptions'],1)
        self.assertEqual(result['cards'][0]['product_name'],'Synthetic chicken')

    def test_no_staging_rejects_instead_of_falsely_reporting_zero_problems(self):
        for table in ('rema_locked_cards','rema_locked_snapshots',
                      'kiwi_locked_cards','kiwi_locked_snapshots'):
            self.db.execute(f'DROP TABLE {table}')
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'No KIWI or REMA'):
            inspect(self.dbpath,5)

    def test_partial_pair_and_schema_drift_fail_closed(self):
        self.db.execute('DROP TABLE rema_locked_cards')
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'REMA staging is incomplete'):
            inspect(self.dbpath,5)
        self.db.execute('DROP TABLE kiwi_locked_cards')
        self.db.execute('CREATE TABLE kiwi_locked_cards (snapshot_sha256 TEXT, card_no INTEGER)')
        self.db.execute('DROP TABLE rema_locked_snapshots')
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'KIWI staging schema changed'):
            inspect(self.dbpath,5)

    def test_direct_cli_kiwi_only_emits_valid_json_and_source_gaps(self):
        self.db.execute('DROP TABLE rema_locked_cards')
        self.db.execute('DROP TABLE rema_locked_snapshots')
        self.db.commit()
        result=subprocess.run(
            [sys.executable,str(ROOT/'scripts/review_reconciliation_queue.py'),
             '--db',str(self.dbpath),'--limit','5'],
            capture_output=True,text=True,check=True,
        )
        payload=json.loads(result.stdout)
        self.assertEqual(payload['available_retailers'],['KIWI'])
        self.assertEqual(payload['skipped_retailers_without_staging'],['REMA'])
        self.assertEqual(payload['retail_price_status'],'source_only')
        self.assertEqual(payload['source_rows_with_exceptions'],2)
        self.assertEqual(result.stderr,'')



if __name__=='__main__':
    unittest.main()
