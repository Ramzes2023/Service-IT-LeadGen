"""Generate a no-write VPS-only canary from the reviewed B2B geo module.

The output is Python source streamed over pinned SSH into an already running
nonroot writer container. The reviewed source is dynamically loaded in memory;
neither the live image nor the filesystem/CRM/PG state changes.
"""
from pathlib import Path

geo_code = Path("leadpilot/serviceit_b2b_moscow_geo.py").read_text(encoding="utf-8")
assert "def check_public_procurement_location" in geo_code
assert "def classify_location_lines" in geo_code

print("import json, os, sys, types")
print("import httpx")
print("from leadpilot.serviceit_vps_pg_queue import db")
print("geo = types.ModuleType('leadpilot.serviceit_b2b_moscow_geo')")
print("geo.__package__ = 'leadpilot'")
print("sys.modules['leadpilot.serviceit_b2b_moscow_geo'] = geo")
print("exec(compile(" + repr(geo_code) + ", 'reviewed_b2b_geo.py', 'exec'), geo.__dict__)")
print(String := '''
SOURCES = ("4675475", "4677160", "4677404", "4678279", "4676643")
counts = {"MOSCOW": 0, "OUTSIDE": 0, "MIXED": 0, "UNKNOWN": 0}
try:
    env = dict(os.environ)
    with db(env) as con, con.transaction():
        con.execute("SET TRANSACTION READ ONLY")
        rows = con.execute(
            "SELECT source_id,notice_json FROM serviceit_vps_b2b_inbox "
            "WHERE source_id = ANY(%s) AND state='VERIFIED' ORDER BY source_id",
            ([f"B2B-CENTER-{s}" for s in SOURCES],),
        ).fetchall()
    table = {}
    for key, note in rows:
        tender_id = str(key).removeprefix("B2B-CENTER-")
        table[tender_id] = note if isinstance(note, dict) else json.loads(note)
    print("MOSCOW_CANARY_PG_READONLY=YES", flush=True)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        for tender_id in SOURCES:
            if tender_id not in table:
                print(f"MOSCOW_CANARY_{tender_id}=ABSENT", flush=True)
                continue
            result = geo.check_public_procurement_location(table[tender_id], client)
            if result not in counts:
                raise ValueError("unexpected geo status")
            counts[result] += 1
            print(f"MOSCOW_CANARY_{tender_id}={result}", flush=True)
    for key,value in counts.items():
        print(f"MOSCOW_CANARY_{key}_COUNT={value}", flush=True)
    print("MOSCOW_CANARY_CRM_WRITES=0",flush=True)
    print("MOSCOW_CANARY_PG_WRITES=0",flush=True)
except Exception as exc:
    print("MOSCOW_CANARY_ERROR=" + type(exc).__name__.upper(), flush=True)
    raise SystemExit(2) from None
''')
