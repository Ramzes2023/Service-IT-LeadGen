"""LOCAL ONLY non-destructive SQLite probe for PR78 item #7 reservation failures.

The previous live -ApplyOnce returned an ambiguous status, while PR80
verified ALL nine AI fields empty and the original ledger NOT_RESERVED.
PR81 repeated ALL pre-reservation CRM checks successfully. This probe
tests ONLY the remaining mkdir/SQLite/commit/readback/filesystem path.

It NEVER opens or alters backfill-v1.sqlite3, never connects to Bitrix24,
never invokes GigaChat, never reserves a real operation and never attempts
another historical CRM write.

It DOES create the missing parent directory (if necessary) and ONE random
temporary SQLite file inside it; the temporary file is removed immediately
after the test. These are local filesystem writes, not CRM writes.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import uuid


class ProbeBlocked(ValueError):
    pass


def fixed_path(localappdata: str) -> Path:
    if not isinstance(localappdata,str) or not localappdata:
        raise ProbeBlocked("LOCALAPPDATA_NOT_AVAILABLE")
    root=Path(localappdata)
    if not root.is_absolute():
        raise ProbeBlocked("LOCALAPPDATA_NOT_ABSOLUTE")
    return root/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"


def probe_sqlite_storage(fixed_ledger: Path) -> dict:
    if (not isinstance(fixed_ledger,Path)
            or fixed_ledger.name!="backfill-v1.sqlite3"):
        raise ProbeBlocked("FIXED_LEDGER_NAME_REQUIRED")
    if fixed_ledger.is_file() or fixed_ledger.is_symlink():
        return {
            "status":"LOCAL_SQLITE_PROBE_NOT_RUN_EXISTING_LEDGER",
            "existing_ledger_untouched":True,
            "temporary_probe_cleaned":True,
            "crm_writes":0,"historical_post_retried":False,
        }
    target=fixed_ledger.parent
    if target.is_symlink():
        raise ProbeBlocked("UNEXPECTED_SYMLINK_DIRECTORY")
    directory_preexisted=target.is_dir()
    stage="MKDIR"
    temporary=None
    db_created=False
    cleanup_ok=True
    failure=None
    verification_ok=False
    try:
        target.mkdir(parents=True,exist_ok=True)
        if not target.is_dir():
            raise ProbeBlocked("DIRECTORY_NOT_CREATED")
        # Avoid using the historical ledger filename or key. Atomic exclusive
        # creation avoids accidentally opening any preexisting SQLite file.
        stage="CREATE_TEMP_FILE"
        temporary=target/("_serviceit_sqlite_probe_"+uuid.uuid4().hex+".sqlite3")
        descriptor=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(descriptor)
        db_created=True
        stage="SQLITE_OPEN"
        with closing(sqlite3.connect(str(temporary),timeout=5)) as db:
            db.execute("PRAGMA synchronous=FULL")
            stage="SQLITE_CREATE_AND_COMMIT"
            with db:
                db.execute("""CREATE TABLE test_reservation(
                    op_key TEXT PRIMARY KEY, state TEXT NOT NULL,
                    digest TEXT NOT NULL)""")
                db.execute("INSERT INTO test_reservation VALUES (?,?,?)",
                           ("SYNTHETIC_LOCAL_ONLY","UNCERTAIN","0"*64))
            stage="SQLITE_READBACK"
            row=db.execute("SELECT state,digest FROM test_reservation WHERE op_key=?",
                           ("SYNTHETIC_LOCAL_ONLY",)).fetchone()
            verification_ok=row==("UNCERTAIN","0"*64)
            if not verification_ok:
                raise ProbeBlocked("TEMP_SQLITE_READBACK_MISMATCH")
        stage="COMPLETE"
    except (OSError,sqlite3.Error,ProbeBlocked):
        # Never leak absolute paths, exception descriptions or raw data.
        failure=stage
    finally:
        if db_created and temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
                for extra in ("-journal","-wal","-shm"):
                    (Path(str(temporary)+extra)).unlink(missing_ok=True)
            except OSError:
                cleanup_ok=False

    if not cleanup_ok:
        status="LOCAL_SQLITE_PROBE_CLEANUP_FAILED"
    elif failure:
        status="LOCAL_SQLITE_PROBE_BLOCKED"
    elif verification_ok:
        status="LOCAL_SQLITE_PROBE_PASS"
    else:
        status="LOCAL_SQLITE_PROBE_INCONCLUSIVE"
    return {
        "status":status,
        "stopped_at":failure or "NONE",
        "parent_existed_before":directory_preexisted,
        "parent_exists_after":target.is_dir(),
        "temporary_sqlite_created":db_created,
        "sqlite_transaction_readback_verified":verification_ok,
        "temporary_probe_cleaned":cleanup_ok,
        "real_ledger_exists_after":fixed_ledger.exists(),
        "real_ledger_untouched":True,
        "crm_reads":0,"crm_writes":0,"gigachat_calls":0,
        "historical_post_retried":False,
        "safe_to_repeat_previous_apply":False,
    }


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description="Local-only synthetic SQLite probe. No REST or model.")
    p.add_argument("--local-only",action="store_true")
    args=p.parse_args(argv)
    try:
        if not args.local_only:
            print(json.dumps({"status":"LOCAL_SQLITE_PROBE_OFFLINE",
                              "crm_reads":0,"crm_writes":0}))
            return 0
        path=fixed_path(os.environ.get("LOCALAPPDATA",""))
        out=probe_sqlite_storage(path)
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0 if out["status"]=="LOCAL_SQLITE_PROBE_PASS" else 2
    except (ProbeBlocked,OSError,ValueError):
        print(json.dumps({"status":"LOCAL_SQLITE_PROBE_BLOCKED",
                          "crm_reads":0,"crm_writes":0,
                          "historical_post_retried":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
