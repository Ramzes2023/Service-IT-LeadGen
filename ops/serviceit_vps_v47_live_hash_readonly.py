#!/usr/bin/env python3
"""Read-only VPS runtime attestation for guarded V47 Mini rollout."""
import hashlib
import os
from pathlib import Path

NAMES=(
    "serviceit_vps_worker.py",
    "serviceit_vps_b2b_deep_research.py",
    "serviceit_vps_b2b_dossier_v11_worker.py",
    "serviceit_b2b_tavily_research_pro_v46.py",
    "serviceit_b2b_tavily_research_mini_v47.py",
    "serviceit_b2b_tavily_fallback_v44.py",
)
root=Path("/app/leadpilot")
for i,name in enumerate(NAMES):
    p=root/name
    sha=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else "MISSING"
    print("V47_LIVE_MODULE_"+str(i)+"_SHA="+sha,flush=True)
for k in (
    "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_PRO_ENABLED",
    "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED",
    "SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED",
    "SERVICEIT_VPS_POLL_ENABLED",
):
    value=os.environ.get(k,"UNSET")
    print("V47_LIVE_FLAG_"+k+"="+(value if value in ("YES","NO") else "OTHER"),flush=True)
print("V47_LIVE_SHA_CRM_WRITES=0",flush=True)
