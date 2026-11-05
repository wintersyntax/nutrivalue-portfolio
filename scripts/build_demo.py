#!/usr/bin/env python3
"""Create a fully synthetic, SQLite portfolio fixture. All records are invented."""
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'demo.sqlite3'

def build(connection: sqlite3.Connection) -> None:
    connection.executescript((ROOT/'analytics/schema.sql').read_text())
    connection.executescript((ROOT/'analytics/reference_schema.sql').read_text())
    connection.executemany('INSERT INTO stores(id,name) VALUES (?,?)',
                           [(1,'Demo Store North'),(2,'Demo Store South')])
    connection.executemany('INSERT INTO products(id,canonical_name,brand,category) VALUES (?,?,?,?)',
                           [(1,'Demo Protein A','SYNTHETIC','protein'),
                            (2,'Demo Protein B','SYNTHETIC','protein')])
    connection.executemany('''INSERT INTO nutrition_profiles
       (id,product_id,protein_per_100g,kcal_per_100g,measurement_basis,profile_type,
        source_name,source_ref,captured_at) VALUES (?,?,?,?,?,?,?,?,?)''',
       [(1,1,18,120,'as_sold','exact_branded','SYNTHETIC','SYNTHETIC NUTRITION A','2099-01-01'),
        (2,2,10,80,'as_sold','exact_branded','SYNTHETIC','SYNTHETIC NUTRITION B','2099-01-01')])
    connection.executemany('''INSERT INTO price_observations
       (id,product_id,store_id,price_nok,package_net_edible_g,observed_at,valid_from,
        valid_until,source_name,source_ref,verification_status,conditions)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
       [(1,1,1,60,400,'2099-01-01','2099-01-01','2099-12-31',
         'SYNTHETIC','SYNTHETIC PRICE A','demo','not a real offer'),
        (2,2,2,30,500,'2099-01-01','2099-01-01','2099-12-31',
         'SYNTHETIC','SYNTHETIC PRICE B','demo','not a real offer')])
    connection.execute('''INSERT INTO mvt_snapshots(id,locale,content_sha256,retrieved_at)
       VALUES(1,'nb','SYNTHETIC-NOT-OFFICIAL','2099-01-01')''')
    connection.executemany('INSERT INTO mvt_food_groups VALUES (?,?,?)',
                           [(1,'G1','Synthetic group 1'),(1,'G2','Synthetic group 2')])
    connection.executemany('''INSERT INTO mvt_foods
      (snapshot_id,food_id,food_name,food_group_id,source_uri,edible_part_pct,energy_kcal)
      VALUES (?,?,?,?,?,?,?)''',
      [(1,'F1','Demo reference food A','G1','SYNTHETIC','100','120'),
       (1,'F2','Demo reference food B','G1','SYNTHETIC','100','80'),
       (1,'F3','Demo reference food C','G2','SYNTHETIC','100','220'),
       (1,'F4','Demo reference food D','G2','SYNTHETIC','100',None)])
    connection.executemany('''INSERT INTO mvt_food_nutrients
        (snapshot_id,food_id,nutrient_id,quantity_per_100g,unit,source_id)
        VALUES (?,?,?,?,?,?)''',
       [(1,'F1','Protein','20','g','SYNTHETIC'),
        (1,'F2','Protein','10','g','SYNTHETIC'),
        (1,'F4','Protein','5','g','SYNTHETIC')])
    connection.commit()

if __name__ == '__main__':
    destination = Path(sys.argv[1]) if len(sys.argv)>1 else DEFAULT_DB
    if destination.exists():
        raise SystemExit(f'Refusing to overwrite {destination}. Delete it first to rebuild.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(destination) as db:
        db.execute('PRAGMA foreign_keys=ON')
        build(db)
    print(f'Created SYNTHETIC DEMO database: {destination}')
