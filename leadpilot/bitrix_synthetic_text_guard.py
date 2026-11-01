"""Synthetic-only text safety checks for the ServiceIT Bitrix24 handoff.

Contact details are never permitted in the demo action text. This helper is
intentionally shared by offline acceptance and the future write adapter so
that they cannot disagree on a telephone number.
"""
from __future__ import annotations

import re

# A phone-shaped string containing 10-16 digits; allow typical punctuation
# (+, spaces, parentheses and dashes). Do not mistake a short date for a phone.
_PHONE = re.compile(r"(?<![A-Za-z0-9])\+?\d(?:[\s().-]*\d){9,15}(?![A-Za-z0-9])")
_CONTACT_SCHEMES = ("http", "mailto:", "tel:", "wa.me", "t.me/")


def synthetic_action_is_safe(action: object) -> bool:
    if (not isinstance(action, str) or not 8 <= len(action) <= 120\n            or action != action.strip() or not action.strip()):
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in action):
        return False
    if "@" in action or any(token in action.lower() for token in _CONTACT_SCHEMES):
        return False
    if _PHONE.search(action):
        return False
    return True
