#!/usr/bin/env python3
"""Live VPS read-only source access diagnosis for user's real MKB B2B tender.

No CRM/PG access, no credentials, no model, no login, no data mutation.
Only seven fixed source-confirmation booleans and a bounded exit status.
"""
import hashlib
from pathlib import Path
import re
import sys

import httpx

EXPECTED_LIVE_V16_SHA = "126ce5d0f76544e5edc17e1c4a557258a069dbfd2558e5297fb39bb3012d05f5"
ID = "4675475"
SUBJECT = "Приобретение серверного оборудования для нужд МКБ"
URL = "https://zakupki.kontur.ru/4675475_40"
ALLOWED = frozenset({"zakupki.kontur.ru"})


def out(name, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,60}", name)
    assert value in ("YES", "NO")
    print("MKB_SOURCE_" + name + "=" + value, flush=True)


def main():
    live = Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    if not live.is_file() or live.is_symlink():
        out("LIVE_SHA_MATCH", "NO")
        return 2
    sha = hashlib.sha256(live.read_bytes()).hexdigest()
    matched = sha == EXPECTED_LIVE_V16_SHA
    out("LIVE_SHA_MATCH", "YES" if matched else "NO")
    if not matched:
        return 2

    from leadpilot.serviceit_public_evidence_research import (
        _robots, _get, VisibleText, ResearchBlocked,
    )
    from leadpilot.serviceit_b2b_public_mirrors import from_public_page
    try:
        with httpx.Client(trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(12.0)) as client:
            _robots(client, URL, ALLOWED)
            out("ROBOTS_APPROVED", "YES")
            raw = _get(client, URL, ALLOWED)
            parser = VisibleText()
            parser.feed(raw)
            body = " ".join(parser.parts)
            out("PUBLIC_HTML_READ", "YES" if body else "NO")
            out("EXACT_ID", "YES" if re.search(
                r"(?<!\d)" + ID + r"(?!\d)", body) else "NO")
            out("PUBLIC_TITLE", "YES" if SUBJECT.casefold() in body.casefold()
                else "NO")
            near = re.search(r"(?<!\d)" + ID + r"(?!\d)", body)
            right = body[near.end():near.end()+250].casefold() if near else ""
            out("B2B_PROVENANCE", "YES" if re.search(
                r"опубликован\w*.{0,180}b2b-center",right) else "NO")
            out("BUYER_LABEL", "YES" if "заказчик" in body.casefold()
                else "NO")
            out("INN_LABEL", "YES" if "инн" in body.casefold() else "NO")
            out("DEADLINE_LABEL", "YES" if
                "окончание подачи заявок" in body.casefold() else "NO")
            out("GEOGRAPHY_LABEL", "YES" if
                "место поставки" in body.casefold() else "NO")
            row = from_public_page(client, URL, ID, SUBJECT,
                                   mirror_host="zakupki.kontur.ru")
            out("V16_MIRROR_ACCEPTED", "YES" if row else "NO")
            return 0
    except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
        out("PUBLIC_HTML_READ", "NO")
        return 2


if __name__ == "__main__":
    sys.exit(main())
