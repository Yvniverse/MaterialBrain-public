#!/usr/bin/env python3
"""Verify picking write UAT conservation across Material/Reservation/InventoryLot.

Snapshot JSON contract:
{
  "materials": {"<material_id>": {"quantity":"...","reserved_quantity":"..."}},
  "reservations": {"<project_id>:<material_id>": {"quantity":"...","consumed_quantity":"..."}},
  "inventory_lots": {"<inventory_lot_id>": {"quantity":"..."}},
  "stock_movement_count": 123
}
Expected picks:
{"picks":[{"material_id":1,"project_id":2,"inventory_lot_id":3,"quantity":"4"}, ...]}
"""
from __future__ import annotations
import argparse,json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

def D(x): return Decimal(str(x))
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('before',type=Path); ap.add_argument('after',type=Path); ap.add_argument('expected',type=Path); args=ap.parse_args()
 b=json.loads(args.before.read_text()); a=json.loads(args.after.read_text()); e=json.loads(args.expected.read_text())
 mat=defaultdict(lambda:Decimal('0')); res=defaultdict(lambda:Decimal('0')); lot=defaultdict(lambda:Decimal('0'))
 for p in e.get('picks',[]):
  q=D(p['quantity']); mat[str(p['material_id'])]+=q; res[f"{p['project_id']}:{p['material_id']}"]+=q; lot[str(p['inventory_lot_id'])]+=q
 failures=[]
 for mid,q in mat.items():
  bm=b['materials'][mid]; am=a['materials'][mid]
  if D(bm['quantity'])-D(am['quantity'])!=q: failures.append(f'material {mid} quantity delta mismatch')
  if D(bm['reserved_quantity'])-D(am['reserved_quantity'])!=q: failures.append(f'material {mid} reserved delta mismatch')
 for key,q in res.items():
  br=b['reservations'][key]; ar=a['reservations'][key]
  if D(br['quantity'])-D(ar['quantity'])!=q: failures.append(f'reservation {key} outstanding delta mismatch')
  if D(ar['consumed_quantity'])-D(br['consumed_quantity'])!=q: failures.append(f'reservation {key} consumed delta mismatch')
 for lid,q in lot.items():
  if D(b['inventory_lots'][lid]['quantity'])-D(a['inventory_lots'][lid]['quantity'])!=q: failures.append(f'lot {lid} delta mismatch')
 expected_movements=len(e.get('picks',[]))
 if int(a['stock_movement_count'])-int(b['stock_movement_count'])!=expected_movements: failures.append('stock movement count delta mismatch')
 report={'expected_pick_events':expected_movements,'material_totals':{k:str(v) for k,v in mat.items()},'failures':failures}
 print(json.dumps(report,ensure_ascii=False,indent=2)); return 1 if failures else 0
if __name__=='__main__': raise SystemExit(main())
