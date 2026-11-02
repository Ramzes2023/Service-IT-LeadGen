"""Local operator for existing 1062/21 B2B email HOLD review.

Default: EML parse and preview with absolutely zero network.
Optional: exactly one approved HOLD card, fenced and read back. No worker.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .b2b_email_opportunity_bridge import build_bitrix_preview, InputRejected
from .b2b_bitrix_hold_delivery import (
    APPROVAL, HoldWriteBlocked, deliver_one_hold, validated_hold,
)


def operate(argv=None):
    parser = argparse.ArgumentParser(description="B2B EML to existing 1062/21 tender review")
    parser.add_argument("--eml", nargs="+", required=True)
    parser.add_argument("--tender-id", required=True)
    parser.add_argument("--apply-one", action="store_true")
    parser.add_argument("--verified-original", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        _paths = [Path(p) for p in args.eml]
        if len(_paths) > 30 or any(not p.is_file() or p.stat().st_size > 256000 for p in _paths):
            raise InputRejected("EMAIL_INPUT_OUT_OF_SCOPE")
        preview = build_bitrix_preview([p.read_bytes() for p in _paths])
        params = validated_hold(preview, tender_id=args.tender_id)
        if not args.apply_one:
            print(json.dumps({
                "status": "B2B_HOLD_PREVIEW_ONLY",
                "email_count": preview["emails_seen"],
                "unique_tenders": preview["tenders_unique"],
                "it_opportunity_previews": preview["it_opportunity_previews"],
                "selected_id": args.tender_id,
                "selected_kind": params["fields"]["UF_CRM_13_SVCIT_SIGNAL_KIND"],
                "url": params["fields"]["UF_CRM_13_SVCIT_SIGNAL_URL"],
                "crm_writes": "NO", "qualified_real_leads": 0,
            }, ensure_ascii=False))
            return 0
        if args.approval != APPROVAL or not args.verified_original:
            raise HoldWriteBlocked("EXPLICIT_ONE_CARD_APPROVAL_REQUIRED")
        if not os.environ.get("LOCALAPPDATA"):
            raise HoldWriteBlocked("WINDOWS_LOCALAPPDATA_REQUIRED")
        from .b2b_bitrix_readiness import ReadOnlyBitrix
        import httpx
        base = os.environ.get("BITRIX24_B2B_ONE_SHOT_WEBHOOK", "")
        state = (Path(os.environ["LOCALAPPDATA"]) / "ServiceIT" /
                 "B2B-Bitrix-Hold-Operator")
        state.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            rest = ReadOnlyBitrix(base, client=client)
            result = deliver_one_hold(
                preview, tender_id=args.tender_id, rest=rest,
                approval=args.approval, original_verified=True, fence_dir=state)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") in {
            "ONE_TENDER_HOLD_WRITTEN_AND_READBACK_VERIFIED",
            "EXISTING_TENDER_REVIEW_NO_POST",
        } else 2
    except (InputRejected, HoldWriteBlocked, ValueError, OSError):
        # No URL, webhook, raw email or remote response body is logged.
        print("B2B_TO_BITRIX=BLOCKED_OR_UNCERTAIN; CHECK_LOCAL_SOURCE_AND_FENCE; DO_NOT_BLINDLY_RETRY")
        return 2


if __name__ == "__main__":
    raise SystemExit(operate())
