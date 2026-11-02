"""Daily bounded public procurement discovery. No customers contacted or CRM touched.

Rotates 3 of 9 Service IT themes and checks Moscow and Moscow Oblast
using max 6 of Krasoft's documented 20 anonymous searches per day.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from leadpilot.krasoft_public_mcp import SEARCH_TERMS, SourceBlocked, probe


def run(day=None, output_root="exports", probe_fn=probe):
    current = day or datetime.now(timezone.utc)
    group = (current.timetuple().tm_yday - 1) % 3
    queries = list(SEARCH_TERMS[group * 3:(group + 1) * 3])
    results = {}
    for region in ("77", "50"):
        results[region] = probe_fn(queries, region=region, page_size=5,
                                   observed_at=current)
    root = Path(output_root).resolve()
    expected = Path("exports").resolve()
    if root != expected:
        raise SourceBlocked("OUTPUT_ROOT_MUST_BE_EXPORTS")
    root.mkdir(parents=True, exist_ok=True)
    dest = root / ("krasoft-rotation-" + current.strftime("%Y%m%dT%H%M%SZ") + ".json")
    report = {"phase": "LIVE_SOURCE_TRANSPORT_RESEARCH_ONLY",
              "service_groups_scanned": queries, "region_reports": results,
              "qualified_real_leads": 0, "crm_writes": "NO"}
    with dest.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return dest


if __name__ == "__main__":
    try:
        path = run()
    except SourceBlocked as error:
        print("KRASOFT_LIVE_ACCEPTANCE=BLOCKED; REASON=" + str(error)
              + "; QUALIFIED_REAL_LEADS=0; CRM_WRITES=NO")
        raise SystemExit(2) from None
    print("KRASOFT_TRANSPORT_ONLY_REPORT=" + str(path))
    print("KRASOFT_REAL_QUALIFIED_LEADS=0; CRM_WRITES=NO")
