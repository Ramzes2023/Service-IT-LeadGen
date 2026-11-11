#!/usr/bin/env python3
"""Read-only live code hash audit, never touch Bitrix, Postgres, keys or network."""
import hashlib
from pathlib import Path
import os
import re
import sys

FILES=("serviceit_b2b_tender_index_v18.py","serviceit_b2b_dossier_v11.py",
       "serviceit_vps_worker.py","serviceit_b2b_v19_backfill.py")
OLD_V19="002fab9fec1e5a59c8b7f74f0cbd174912d32ed1fdd82b4188f1d1783338cb1a"

def run():
    if len(sys.argv)!=5 or any(re.fullmatch(r"[a-f0-9]{64}",x) is None for x in sys.argv[1:]):
        print("V19_ORIGIN_INPUT_INVALID=YES",flush=True)
        return 2
    all_ok=True
    for index,(file,expected) in enumerate(zip(FILES,sys.argv[1:]),1):
        path=Path("/app/leadpilot")/file
        actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else "MISSING"
        status=("MAIN" if actual==expected else
                "V19_FIRST" if index==4 and actual==OLD_V19 else "OTHER")
        print(f"V19_ORIGIN_MODULE_{index}={status}",flush=True)
        if index==4 and status=="OTHER":
            print("V19_ORIGIN_SIDECAR_SHA="+actual,flush=True)
            all_ok=False
        elif index<4 and status=="OTHER":
            all_ok=False
    print("V19_ORIGIN_MATCH_CURRENT_MAIN="+("YES" if all_ok else "NO"),flush=True)
    print("V19_ORIGIN_CRM_WRITES=0",flush=True)
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        print("V19_ORIGIN_AUDIT_ERROR=YES",flush=True)
        raise SystemExit(2)
